"""
Finds a company's official website when the input CSV didn't provide one.

Uses Groq's `groq/compound` model with its built-in `web_search` tool (the
same raw-API pattern backend/core/url_reader.py already uses for its
compound-beta fallback, and the same Groq key priority rules - see
llm_router.pick_groq_fallback_key). This reuses infrastructure/keys this
project already has instead of adding a paid search-API dependency
(Serper/Google Custom Search etc.) - if search precision here ever turns
out to be the bottleneck, swapping in a dedicated search API is an isolated
change confined to this file.

IMPORTANT: this only ever returns a URL - it never invents an email. The
returned URL still goes through the exact same crawl + regex extraction as
a CSV-provided URL (see pipeline.py), so a wrong guess here just means "we
looked at the wrong website and found nothing useful," never a fabricated
result.
"""
from __future__ import annotations

import asyncio
import logging
import re

import httpx
from redis.asyncio import Redis

from backend.core.email_finder import dns_utils
from backend.core.email_finder.crawler import site_domain

logger = logging.getLogger("email_finder.website_discovery")

SEARCH_TIMEOUT = 20.0

# Same class of bug as crawler.py's Jina fetching, and the same fix -
# confirmed by the numbers on a real large batch: with no retry and no
# concurrency cap on this call, a huge majority of companies (with no CSV
# URL) were ending up in "website not found" even though only a small
# fraction genuinely have no discoverable site. This call previously had
# NEITHER protection at all - a single 429/timeout from Groq silently gave
# up on search entirely and fell straight through to the much weaker
# domain-guessing fallback, for every company unlucky enough to hit it
# under real concurrent load.
MAX_CONCURRENT_SEARCHES = 5
_search_semaphore = asyncio.Semaphore(MAX_CONCURRENT_SEARCHES)
SEARCH_RATE_LIMIT_RETRIES = 3
SEARCH_RATE_LIMIT_BACKOFF_SECONDS = 1.5

# Directory/social/reference sites compound-beta's web_search commonly
# surfaces instead of (or alongside) the real official site - never a
# company's own contact email, so never worth crawling.
_NON_OFFICIAL_DOMAINS = {
    "linkedin.com", "facebook.com", "twitter.com", "x.com", "instagram.com",
    "wikipedia.org", "crunchbase.com", "bloomberg.com", "glassdoor.com",
    "indeed.com", "yelp.com", "youtube.com", "github.com", "medium.com",
    "google.com", "maps.google.com", "yellowpages.com", "zoominfo.com",
    "dnb.com", "opencorporates.com",
}

_URL_REGEX = re.compile(r"https?://[^\s\)\]\"'<>]+")


def _extract_urls(text: str) -> list[str]:
    return _URL_REGEX.findall(text)


def _is_plausible_official_domain(domain: str) -> bool:
    domain = domain.lower()
    return domain not in _NON_OFFICIAL_DOMAINS and not any(
        domain.endswith(f".{d}") for d in _NON_OFFICIAL_DOMAINS
    )


async def find_official_website(
    company_name: str,
    location: str | None,
    groq_api_key: str | None,
    redis: Redis | None = None,
) -> str | None:
    """Returns a base URL (e.g. "https://www.acme.com") or None. Never
    raises - a failed/ambiguous search just means "couldn't find one",
    handled by pipeline.py falling through to domain-guessing."""
    if not groq_api_key:
        return None

    location_str = f" located in {location}" if location else ""
    prompt = (
        f'Find the single official corporate website homepage URL for the company "{company_name}"'
        f"{location_str}. Respond with ONLY that one URL, nothing else. If you cannot find an "
        'official website with reasonable confidence, respond with exactly: NONE'
    )

    content: str | None = None
    async with _search_semaphore:
        async with httpx.AsyncClient(timeout=SEARCH_TIMEOUT) as client:
            for attempt in range(SEARCH_RATE_LIMIT_RETRIES + 1):
                try:
                    resp = await client.post(
                        "https://api.groq.com/openai/v1/chat/completions",
                        headers={"Authorization": f"Bearer {groq_api_key}", "Groq-Model-Version": "latest"},
                        json={
                            "model": "groq/compound",
                            "messages": [{"role": "user", "content": prompt}],
                            "compound_custom": {"tools": {"enabled_tools": ["web_search"]}},
                            "temperature": 0,
                        },
                    )
                    if resp.status_code == 429 and attempt < SEARCH_RATE_LIMIT_RETRIES:
                        # Same transient-not-permanent reasoning as
                        # crawler.py's Jina 429 handling - worth a bounded
                        # retry rather than silently giving up on search
                        # and falling through to the weaker domain-guess
                        # fallback for this company.
                        logger.info(
                            "Groq search rate-limited (429) for %r, retrying (attempt %d)",
                            company_name, attempt + 1,
                        )
                        await asyncio.sleep(SEARCH_RATE_LIMIT_BACKOFF_SECONDS * (attempt + 1))
                        continue
                    resp.raise_for_status()
                    data = resp.json()
                    content = data["choices"][0]["message"]["content"]
                    break
                except Exception as exc:
                    logger.info("website search failed for %r: %s", company_name, exc)
                    return None

    if content is None:
        return None

    if "NONE" in content.upper() and not _extract_urls(content):
        return None

    for candidate in _extract_urls(content):
        domain = site_domain(candidate)
        if not domain or not _is_plausible_official_domain(domain):
            continue
        # Compound-beta occasionally hallucinates a domain that doesn't
        # actually resolve - confirm before trusting it, same bar as a
        # guessed domain gets in domain_guesser.
        if await dns_utils.has_a_record(domain, redis) or await dns_utils.has_mx(domain, redis):
            return f"https://{domain}"

    return None
