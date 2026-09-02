"""
Finds a company's official website when the input CSV didn't provide one.

Uses Groq's `groq/compound` model with its built-in `web_search` tool (the
same raw-API pattern backend/core/url_reader.py already uses for its
compound-beta fallback, and the same Groq key priority rules - see
llm_router.pick_all_groq_keys). This reuses infrastructure/keys this
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

# `groq/compound` has an unusually low 250-REQUEST-PER-DAY cap (confirmed
# live via the actual 429 response body: "Rate limit reached ... on
# requests per day (RPD): Limit 250, Used 250") - a genuinely different
# failure mode from a normal per-minute rate limit, and one that a
# same-key retry-with-backoff CANNOT fix (waiting 1.5s doesn't refill a
# daily quota). The real fix, confirmed by how the rest of this app avoids
# the exact same trap (see model_catalog.py's docstring: "every extra
# model/key is a genuinely separate quota bucket"), is to ROTATE ACROSS
# EVERY AVAILABLE KEY - the system pool here has 8 Groq keys configured,
# so that's 8 x 250 = 2000 requests/day available, not 250, once every key
# actually gets used instead of only key #0 (see llm_router.pick_all_groq_keys).
MAX_CONCURRENT_SEARCHES = 5
_search_semaphore = asyncio.Semaphore(MAX_CONCURRENT_SEARCHES)

# Process-local memory of which key_refs are known to have hit their DAILY
# quota already (as opposed to a merely transient error) - once confirmed
# exhausted, skip straight past that key for the rest of THIS worker
# process's lifetime instead of wasting an HTTP round trip finding out
# again on every subsequent company. Cleared automatically on worker
# restart, which happens at least daily in normal operation anyway (quota
# resets on Groq's side on a rolling/daily basis).
_daily_quota_exhausted_keys: set[str] = set()

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


def _is_daily_quota_error(status_code: int, body: str) -> bool:
    return status_code == 429 and "per day" in body.lower()


async def find_official_website(
    company_name: str,
    location: str | None,
    groq_api_keys: list[tuple[str, str]],
    redis: Redis | None = None,
) -> str | None:
    """`groq_api_keys` is the FULL list of (api_key, key_ref) pairs
    available for this call (see llm_router.pick_all_groq_keys) - not just
    one. Tries each key in turn until one succeeds; a key hitting its
    daily quota is remembered and skipped for every subsequent call in
    this worker process, rather than being retried pointlessly.

    Returns a base URL (e.g. "https://www.acme.com") or None. Never
    raises - a failed/ambiguous search just means "couldn't find one",
    handled by pipeline.py falling through to domain-guessing."""
    if not groq_api_keys:
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
            for api_key, key_ref in groq_api_keys:
                if key_ref in _daily_quota_exhausted_keys:
                    continue

                try:
                    resp = await client.post(
                        "https://api.groq.com/openai/v1/chat/completions",
                        headers={"Authorization": f"Bearer {api_key}", "Groq-Model-Version": "latest"},
                        json={
                            "model": "groq/compound",
                            "messages": [{"role": "user", "content": prompt}],
                            "compound_custom": {"tools": {"enabled_tools": ["web_search"]}},
                            "temperature": 0,
                        },
                    )
                    if resp.status_code == 429:
                        if _is_daily_quota_error(resp.status_code, resp.text):
                            logger.info("Groq key %s hit its daily compound quota - skipping it for the rest of this run", key_ref)
                            _daily_quota_exhausted_keys.add(key_ref)
                        else:
                            logger.info("Groq search rate-limited (429, transient) on key %s, trying next key", key_ref)
                        continue

                    resp.raise_for_status()
                    data = resp.json()
                    content = data["choices"][0]["message"]["content"]
                    break
                except Exception as exc:
                    logger.info("website search failed for %r via key %s: %s", company_name, key_ref, exc)
                    continue

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
