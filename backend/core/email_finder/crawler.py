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

import hashlib
import logging
import re
from urllib.parse import urljoin, urlparse

import httpx
from redis.asyncio import Redis

logger = logging.getLogger("email_finder.crawler")

JINA_TIMEOUT = 25.0
MAX_PAGE_CHARS = 20_000
MIN_CONTENT_LENGTH = 50  # much lower bar than url_reader's 200 - a bare contact page can be tiny
CACHE_TTL_SECONDS = 30 * 24 * 3600  # emails change far less often than product copy
MAX_EXTRA_PAGES = 2  # homepage + up to 2 more (one discovered link, one guessed path)

_CONTACT_LINK_PATTERN = re.compile(
    r"contact|reach-us|reach_us|get-in-touch|talk-to-us|enquir|inquir", re.IGNORECASE
)
_ABOUT_LINK_PATTERN = re.compile(r"about|who-we-are|company", re.IGNORECASE)
_MARKDOWN_LINK = re.compile(r"\[([^\]]*)\]\((https?://[^\s)]+)\)")

_GUESSED_PATHS = ["/contact", "/contact-us", "/contactus", "/about", "/about-us"]

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


async def _fetch_one(client: httpx.AsyncClient, url: str, redis: Redis | None) -> PageFetch:
    if redis is not None:
        try:
            cached = await redis.get(_cache_key(url))
            if cached is not None:
                text = cached.decode() if isinstance(cached, bytes) else cached
                return PageFetch(url, text, success=bool(text), from_cache=True)
        except Exception:
            logger.debug("crawl cache read failed for %s", url, exc_info=True)

    jina_url = f"https://r.jina.ai/{url}"
    try:
        resp = await client.get(
            jina_url, headers={"Accept": "application/json", "X-Return-Format": "markdown"}
        )
        if resp.status_code in (400, 404, 422):
            result = PageFetch(url, "", success=False)
        else:
            resp.raise_for_status()
            data = resp.json()
            content = (data.get("data", {}).get("content", "") or data.get("content", ""))[:MAX_PAGE_CHARS]
            if _is_parked_page(content):
                logger.info("skipping parked/placeholder page: %s", url)
                content = ""
            result = PageFetch(url, content, success=len(content) >= MIN_CONTENT_LENGTH)
    except Exception as exc:
        logger.info("crawl fetch failed for %s: %s", url, exc)
        result = PageFetch(url, "", success=False)

    if redis is not None:
        try:
            await redis.setex(_cache_key(url), CACHE_TTL_SECONDS, result.content)
        except Exception:
            logger.debug("crawl cache write failed for %s", url, exc_info=True)

    return result


def _discover_links(markdown: str, base_url: str, pattern: re.Pattern) -> list[str]:
    hits: list[str] = []
    for text, href in _MARKDOWN_LINK.findall(markdown):
        if pattern.search(text) or pattern.search(href):
            hits.append(urljoin(base_url, href))
    return hits


async def crawl_company_site(url: str, redis: Redis | None = None, allow_offsite: bool = True) -> list[PageFetch]:
    """Fetches the homepage, then up to MAX_EXTRA_PAGES more pages: a
    discovered Contact link (preferred), else a discovered About link, else
    a couple of common guessed paths - stopping early the moment a fetched
    page already contains a usable email, so most companies only cost 1-2
    fetches, not every path every time.

    `allow_offsite` must match whatever the caller will use for the real
    extraction pass afterward (see pipeline.py) - otherwise this could stop
    early on page 1 because of an off-domain match the caller is going to
    discard anyway, and never fetch the page that actually has the
    on-domain one."""
    from backend.core.email_finder.extractor import extract_emails  # local import avoids a cycle at module load

    base_url = normalize_url(url)
    domain = site_domain(base_url)

    async with httpx.AsyncClient(timeout=JINA_TIMEOUT) as client:
        pages: list[PageFetch] = []

        home = await _fetch_one(client, base_url, redis)
        pages.append(home)
        if home.success and extract_emails(home.content, base_url, domain, allow_offsite):
            return pages

        candidate_urls: list[str] = []
        if home.success:
            candidate_urls += _discover_links(home.content, base_url, _CONTACT_LINK_PATTERN)
            candidate_urls += _discover_links(home.content, base_url, _ABOUT_LINK_PATTERN)
        candidate_urls += [urljoin(base_url, p) for p in _GUESSED_PATHS]

        seen = {base_url}
        tried = 0
        for candidate in candidate_urls:
            if candidate in seen or tried >= MAX_EXTRA_PAGES:
                continue
            seen.add(candidate)
            tried += 1

            page = await _fetch_one(client, candidate, redis)
            pages.append(page)
            if page.success and extract_emails(page.content, candidate, domain, allow_offsite):
                break

        return pages
