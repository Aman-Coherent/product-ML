"""
Fetches the small set of pages worth checking for a company's real contact
email: the homepage, plus whichever Contact/About page it links to (or a
handful of common guessed paths if it doesn't link to one at all).

Deliberately separate from backend/core/url_reader.py rather than reusing
it directly: that module caps content at 5000 chars because it's feeding an
LLM prompt where the front-loaded hero/about text is what matters and
token cost is the constraint. Here the opposite is true - contact emails
live in footers and dedicated contact pages, which is exactly the content
that cap would cut off, and there's no LLM token budget to protect since
nothing here is sent to an LLM at all.
"""
from __future__ import annotations

import asyncio
import hashlib
import logging
import re
from urllib.parse import urljoin, urlparse

import httpx
from redis.asyncio import Redis

logger = logging.getLogger("email_finder.crawler")

JINA_TIMEOUT = 25.0
# Contact emails live disproportionately in the FOOTER - which is the very
# END of the page. A plain `content[:MAX_PAGE_CHARS]` head-truncation (the
# previous behavior) silently threw the footer away entirely on any page
# longer than the cap, which is common on content-heavy corporate
# homepages. Keeping a dedicated tail slice fixes that without raising the
# overall budget - most of it still goes to the head (nav, hero, and often
# the Contact/Impressum links themselves), a smaller slice reserved for
# whatever's at the very bottom of the page.
MAX_PAGE_CHARS_HEAD = 16_000
MAX_PAGE_CHARS_TAIL = 4_000
MIN_CONTENT_LENGTH = 50  # much lower bar than url_reader's 200 - a bare contact page can be tiny
CACHE_TTL_SECONDS = 30 * 24 * 3600  # emails change far less often than product copy
MAX_EXTRA_PAGES = 3  # homepage + up to 3 more - enough to try Impressum AND Kontakt
# on a German site (Impressum occasionally has no direct email invisible in
# a group holding company routing everything through a form even there),
# not just whichever one happens to be discovered/guessed first.

# Jina's free/unauthenticated tier hard-limits concurrent requests PER IP -
# confirmed live, not theoretical: 15 real fetches at concurrency=8 returned
# 429 "RateLimitTriggeredError" on 12/15 of them, executing in ~1 second
# (i.e. rejected almost instantly, not timing out), vs 13/15 succeeding
# when run one-at-a-time. A batch job with concurrency=10+ (the default -
# see email_job_engine.py) hammers straight through this limit unless
# fetches to Jina specifically are capped well below that, independent of
# how many companies are being processed in parallel overall (which also
# do other, non-Jina work - DNS/SMTP/Groq calls that don't share this
# bottleneck at all).
MAX_CONCURRENT_JINA_FETCHES = 4
_jina_semaphore = asyncio.Semaphore(MAX_CONCURRENT_JINA_FETCHES)
# Jina's 429 response body includes "retryAfter" (observed as 1 second) -
# a transient, explicitly-retryable condition, not a permanent failure.
# Silently giving up on the first 429 (the previous behavior) is what
# actually caused most of the "no email found" results in real testing -
# the page was fetchable, the request was just rejected under load.
JINA_RATE_LIMIT_RETRIES = 3
JINA_RATE_LIMIT_BACKOFF_SECONDS = 1.5

# German company websites are LEGALLY REQUIRED (Telemediengesetz S 5) to
# publish an "Impressum" page with a real, verifiable email - in practice
# this is the single most reliable source of a company's true official
# email, MORE reliable than a "Kontakt"/"Contact" page, which (same failure
# mode already confirmed on English sites - see Tetra Pak in extractor.py's
# validation history) is very often just a JS web form with no visible
# address at all. Checked as its own highest-priority pattern, separately
# from the general contact pattern below, for exactly that reason - a site
# with BOTH a Kontakt link and an Impressum link should try Impressum first.
#
# Confirmed as a real, serious gap (not theoretical): a 50-real-company
# German test run BEFORE this fix returned a scraped, verified email for
# only 1/50 companies - every other one fell all the way through to a
# low-confidence guessed pattern_unverified address, purely because the
# English-only patterns below never recognized "Kontakt"/"Impressum"/
# "Uber uns" as contact-relevant links at all. See
# scripts/eval_email_finder_de.py for the reproducible before/after.
_IMPRESSUM_LINK_PATTERN = re.compile(r"impressum|imprint|legal-notice|mentions-legales", re.IGNORECASE)
_CONTACT_LINK_PATTERN = re.compile(
    r"contact|reach-us|reach_us|get-in-touch|talk-to-us|enquir|inquir|"
    # German
    r"kontakt|"
    # French / Spanish / Italian / Portuguese / Dutch, cheap to cover once
    # here since the whole pattern-list approach is already language-aware
    r"contacto|contattaci|contato|klantenservice",
    re.IGNORECASE,
)
_ABOUT_LINK_PATTERN = re.compile(
    r"about|who-we-are|company|"
    # German
    r"ueber-uns|über-uns|unternehmen|"
    # French / Spanish / Italian / Dutch
    r"a-propos|quienes-somos|chi-siamo|over-ons",
    re.IGNORECASE,
)
# Markdown links can carry an optional title attribute after the URL -
# `[text](https://url "title")` - which Jina Reader adds very often (it
# derives it from the link's title/aria-label). The previous version of
# this regex required `)` immediately after the URL with no whitespace,
# so it silently failed to match almost any link that had one - confirmed
# on a real site (basf.com): a literal `[Contact](https://www.basf.com/
# us/en/legal/contact "Contact")` link in the footer was never discovered
# at all because of this, not because of a missing keyword. The optional
# non-capturing group here matches a quoted title (single or double
# quotes) if present, without including it in the captured URL.
_MARKDOWN_LINK = re.compile(r'\[([^\]]*)\]\((\S+?)(?:\s+"[^"]*"|\s+\'[^\']*\')?\)')

_GUESSED_PATHS = [
    "/impressum", "/imprint",
    "/contact", "/contact-us", "/contactus", "/kontakt",
    "/about", "/about-us", "/ueber-uns", "/unternehmen",
]

# A domain-guess (see domain_guesser.py) can land on a real, resolving
# domain that isn't actually the company at all - e.g. a lapsed/unregistered
# domain now sitting on a registrar's "for sale" page, or a hosting
# provider's default placeholder. Those pages often DO contain a real
# email - just the wrong one (the hosting company's own support address,
# not the target company's). Confirmed live: "amcor.ch" resolved and
# rendered a Swiss hosting provider's parked-domain page whose own contact
# email would otherwise have been confidently reported as Amcor's.
# Detecting and skipping these pages entirely is cheaper and more reliable
# than trying to guess which emails on a page "belong" to it.
_PARKED_PAGE_MARKERS = re.compile(
    r"domain (?:is|has been) parked|this domain is for sale|"
    r"buy this domain|domain may be for sale|"
    r"website coming soon|default web ?site page|"
    r"parkingcrew|bodis\.com|afternic|sedo\.com|dan\.com|hoststar|"
    r"account (?:has been )?suspended|this account has been suspended",
    re.IGNORECASE,
)


def _cap_content(content: str) -> str:
    """Bounds page content to MAX_PAGE_CHARS_HEAD + MAX_PAGE_CHARS_TAIL total,
    but keeps a slice of the END (the footer) instead of dropping it - see
    MAX_PAGE_CHARS_HEAD's comment. A short marker is inserted at the join so
    a mailto:/email regex can't accidentally straddle the cut and glue two
    unrelated fragments into a garbage match."""
    total_cap = MAX_PAGE_CHARS_HEAD + MAX_PAGE_CHARS_TAIL
    if len(content) <= total_cap:
        return content
    head = content[:MAX_PAGE_CHARS_HEAD]
    tail = content[-MAX_PAGE_CHARS_TAIL:]
    return f"{head}\n\n[...content trimmed...]\n\n{tail}"


def _is_parked_page(content: str) -> bool:
    # Only worth checking the first couple KB - parking/suspension notices
    # are always the entire visible content of the page, never buried deep
    # in an otherwise-real site's markdown.
    return bool(_PARKED_PAGE_MARKERS.search(content[:2000]))


class PageFetch:
    def __init__(self, url: str, content: str, success: bool, from_cache: bool = False):
        self.url = url
        self.content = content
        self.success = success
        self.from_cache = from_cache


def _cache_key(url: str) -> str:
    return f"email_crawl_cache:{hashlib.sha256(url.encode()).hexdigest()}"


def normalize_url(url: str) -> str:
    url = url.strip()
    if not url.startswith(("http://", "https://")):
        url = f"https://{url}"
    return url


def site_domain(url: str) -> str:
    return urlparse(normalize_url(url)).netloc.lower()


async def _fetch_one(
    client: httpx.AsyncClient, url: str, redis: Redis | None, jina_api_key: str | None = None
) -> PageFetch:
    if redis is not None:
        try:
            cached = await redis.get(_cache_key(url))
            if cached is not None:
                text = cached.decode() if isinstance(cached, bytes) else cached
                return PageFetch(url, text, success=bool(text), from_cache=True)
        except Exception:
            logger.debug("crawl cache read failed for %s", url, exc_info=True)

    jina_url = f"https://r.jina.ai/{url}"
    base_headers = {"Accept": "application/json", "X-Return-Format": "markdown"}
    # Same priority rule as url_reader.py's _fetch_jina: a USER's own paid
    # Jina key (added in Settings) gets higher throughput/priority queueing
    # than the public tier - previously this module never accepted one at
    # all, so the email finder always hit the free tier's tighter rate
    # limit regardless of what key the user had configured elsewhere in
    # the app. See pipeline.py/email_job_engine.py for where this is
    # threaded through from.
    headers = {**base_headers, "Authorization": f"Bearer {jina_api_key}"} if jina_api_key else base_headers

    result: PageFetch | None = None
    async with _jina_semaphore:
        for attempt in range(JINA_RATE_LIMIT_RETRIES + 1):
            try:
                resp = await client.get(jina_url, headers=headers)
                if resp.status_code == 429:
                    # Confirmed live (see MAX_CONCURRENT_JINA_FETCHES'
                    # comment): this is a transient per-IP rate limit, not
                    # "this URL is bad" - worth a bounded retry rather than
                    # giving up immediately, which was silently causing
                    # most real "no email found" results under any load.
                    if attempt < JINA_RATE_LIMIT_RETRIES:
                        logger.info("Jina rate-limited (429) on %s, retrying (attempt %d)", url, attempt + 1)
                        await asyncio.sleep(JINA_RATE_LIMIT_BACKOFF_SECONDS * (attempt + 1))
                        continue
                    result = PageFetch(url, "", success=False)
                    break
                if resp.status_code in (400, 404, 422):
                    result = PageFetch(url, "", success=False)
                    break
                resp.raise_for_status()
                data = resp.json()
                raw_content = data.get("data", {}).get("content", "") or data.get("content", "")
                content = _cap_content(raw_content)
                if _is_parked_page(content):
                    logger.info("skipping parked/placeholder page: %s", url)
                    content = ""
                result = PageFetch(url, content, success=len(content) >= MIN_CONTENT_LENGTH)
                break
            except Exception as exc:
                logger.info("crawl fetch failed for %s: %s", url, exc)
                result = PageFetch(url, "", success=False)
                break

    assert result is not None  # loop always assigns before breaking or exhausts retries into the 429 branch

    if redis is not None:
        try:
            await redis.setex(_cache_key(url), CACHE_TTL_SECONDS, result.content)
        except Exception:
            logger.debug("crawl cache write failed for %s", url, exc_info=True)

    return result


def _discover_links(markdown: str, base_url: str, pattern: re.Pattern) -> list[str]:
    hits: list[str] = []
    for text, href in _MARKDOWN_LINK.findall(markdown):
        # _MARKDOWN_LINK no longer requires an http(s):// prefix on the href
        # (see its own comment) so relative links like "/kontakt" are now
        # correctly picked up too - but that also opens the door to
        # in-page anchors ("#top"), JS pseudo-links, and mailto: links
        # (those aren't pages to crawl - the emails inside them are
        # already caught directly by extractor.py's own mailto: regex).
        if href.startswith(("#", "javascript:", "mailto:", "tel:")):
            continue
        if pattern.search(text) or pattern.search(href):
            hits.append(urljoin(base_url, href))
    return hits


def _localized_variant(base_url: str, domain: str, location: str | None) -> str | None:
    """Guesses the company's own country-market homepage (e.g.
    siemens.com -> siemens.de) when `location` gives a strong ccTLD signal
    and the domain isn't already on that TLD.

    Confirmed live why this matters, not just theoretical: a multinational's
    default (often US-geo-redirected) site frequently shows only a
    support-ticket contact FORM with zero visible email anywhere
    (bosch.com's US contact page has none), while the same company's own
    country-market domain has the real thing (bosch.de's legally-mandated
    Impressum has kontakt@bosch.de). This is exactly the same "the form
    version has nothing, the real page does" failure mode already seen on
    English-only sites (see crawler.py's Impressum-priority comment) - the
    fix here is finding the RIGHT page, not changing how emails are read
    off of it once found."""
    from backend.core.email_finder.domain_guesser import cctld_for_location
    from backend.core.email_finder.domain_utils import registrable_domain

    cc = cctld_for_location(location)
    if not cc:
        return None
    # `domain` here is site_domain()'s raw netloc, which - unlike
    # domain_utils.registrable_domain - does NOT strip a leading "www.".
    # Confirmed live: skipping this strip made a real run guess
    # "https://www.de" (sld ended up being "www") instead of
    # "https://bosch.de" for bosch.com - a completely wasted extra fetch.
    bare_domain = registrable_domain(domain)
    if bare_domain.endswith(f".{cc}"):
        return None
    sld = bare_domain.split(".")[0]
    if not sld:
        return None
    return f"https://{sld}.{cc}"


async def crawl_company_site(
    url: str,
    redis: Redis | None = None,
    allow_offsite: bool = True,
    location: str | None = None,
    jina_api_key: str | None = None,
) -> list[PageFetch]:
    """Fetches the homepage, then up to MAX_EXTRA_PAGES more pages: a
    discovered Contact link (preferred), else a discovered About link, else
    a couple of common guessed paths - stopping early the moment a fetched
    page already contains a usable email, so most companies only cost 1-2
    fetches, not every path every time.

    `allow_offsite` must match whatever the caller will use for the real
    extraction pass afterward (see pipeline.py) - otherwise this could stop
    early on page 1 because of an off-domain match the caller is going to
    discard anyway, and never fetch the page that actually has the
    on-domain one.

    `location`, if given, is used ONLY to try the company's own
    country-market domain as an extra source of Contact/Impressum links
    (see _localized_variant) - never to fetch a third party's site.

    `jina_api_key`, if the calling user has added their own in Settings,
    gets them Jina's paid tier's higher per-IP throughput instead of the
    public tier's tight rate limit (see MAX_CONCURRENT_JINA_FETCHES)."""
    from backend.core.email_finder.extractor import extract_emails  # local import avoids a cycle at module load

    base_url = normalize_url(url)
    domain = site_domain(base_url)

    async with httpx.AsyncClient(timeout=JINA_TIMEOUT) as client:
        pages: list[PageFetch] = []

        home = await _fetch_one(client, base_url, redis, jina_api_key)
        pages.append(home)
        if home.success and extract_emails(home.content, base_url, domain, allow_offsite):
            return pages

        link_sources = [home] if home.success else []

        localized_url = _localized_variant(base_url, domain, location)
        if localized_url:
            localized = await _fetch_one(client, localized_url, redis, jina_api_key)
            pages.append(localized)
            if localized.success:
                # Content compared for same_organization, not exact domain,
                # by extract_emails downstream - a kontakt@bosch.de match
                # here is correctly SCRAPED_VERIFIED, not "offsite", the
                # moment pipeline.py's own allow_offsite gate lets it through.
                if extract_emails(localized.content, localized_url, domain, allow_offsite):
                    return pages
                link_sources.append(localized)

        candidate_urls: list[str] = []
        for src in link_sources:
            # Impressum FIRST, deliberately ahead of Kontakt/Contact - see
            # _IMPRESSUM_LINK_PATTERN's comment for why it's the more
            # reliable source on a German (or EU-legal-notice-requiring)
            # site specifically. Sourced from EVERY fetched page so far
            # (homepage AND the localized variant, if fetched) rather than
            # just the homepage, since the localized page's own nav is
            # often what actually has the real Impressum link.
            candidate_urls += _discover_links(src.content, src.url, _IMPRESSUM_LINK_PATTERN)
        for src in link_sources:
            candidate_urls += _discover_links(src.content, src.url, _CONTACT_LINK_PATTERN)
        for src in link_sources:
            candidate_urls += _discover_links(src.content, src.url, _ABOUT_LINK_PATTERN)
        candidate_urls += [urljoin(base_url, p) for p in _GUESSED_PATHS]

        seen = {base_url}
        if localized_url:
            seen.add(localized_url)
        tried = 0
        for candidate in candidate_urls:
            if candidate in seen or tried >= MAX_EXTRA_PAGES:
                continue
            seen.add(candidate)
            tried += 1

            page = await _fetch_one(client, candidate, redis, jina_api_key)
            pages.append(page)
            if page.success and extract_emails(page.content, candidate, domain, allow_offsite):
                break

        return pages
