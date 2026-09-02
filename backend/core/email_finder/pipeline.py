"""
Per-company orchestrator: the one entry point the job engine calls.

Fallback chain (each step only runs if the previous one produced nothing):

  1. Website is KNOWN (from the CSV) -> crawl it -> extract real emails.
  2. Website UNKNOWN -> Groq compound-beta web search for the official
     site -> if found, crawl it -> extract real emails.
  3. Still no website -> guess candidate domains from the company name
     (+ country signal from location) -> keep the first one that actually
     resolves in DNS -> crawl THAT -> extract real emails.
  4. Still nothing (a real, DNS-confirmed domain exists but no email was
     found on any of its pages) -> generate common company-inbox local
     parts (info@, contact@, sales@...) on that domain and try to verify
     each via a non-invasive SMTP RCPT-TO check.
  5. Nothing at all -> primary_email stays None, clearly reported as such.

Every result carries a `tier` (see models.EmailTier) so a scraped,
domain-matched address is never visually indistinguishable from a guessed,
unverified one - the two are NOT equally trustworthy and the data must not
pretend otherwise.
"""
from __future__ import annotations

import time

from redis.asyncio import Redis

from backend.core.email_finder import dns_utils, pattern_generator, smtp_verifier, website_discovery
from backend.core.email_finder.crawler import crawl_company_site, normalize_url, site_domain
from backend.core.email_finder.domain_guesser import generate_domain_candidates
from backend.core.email_finder.domain_utils import registrable_domain
from backend.core.email_finder.extractor import extract_emails
from backend.core.email_finder.models import (
    TIER_CONFIDENCE,
    EmailCandidate,
    EmailResult,
    EmailTier,
    WebsiteSource,
)

MAX_ALTERNATES = 10
MAX_PATTERN_ATTEMPTS = 6

# Ops toggle: outbound port 25 (needed for SMTP verification) is blocked on
# this deployment (confirmed - every attempt was timing out/refused, just
# wasting a few seconds per guessed email for no benefit). Turned off for
# now per product decision - guessed emails skip straight to
# PATTERN_UNVERIFIED instead of trying and failing to confirm them first.
# Flip back to True later if this ever runs somewhere that allows outbound
# port 25 (see smtp_verifier.py's module docstring for what that unlocks).
SMTP_VERIFICATION_ENABLED = False


def _rank_and_split(candidates: list[EmailCandidate]) -> tuple[EmailCandidate | None, list[EmailCandidate]]:
    if not candidates:
        return None, []
    ordered = sorted(candidates, key=lambda c: c.rank_key, reverse=True)
    primary, rest = ordered[0], ordered[1:]
    return primary, rest[:MAX_ALTERNATES]


async def _try_pattern_fallback(domain: str, redis: Redis | None) -> list[EmailCandidate]:
    candidates = pattern_generator.generate_candidates(domain, limit=MAX_PATTERN_ATTEMPTS)
    if not candidates:
        return []

    if not SMTP_VERIFICATION_ENABLED:
        email, label = candidates[0]
        return [
            EmailCandidate(
                email=email, label=label, tier=EmailTier.PATTERN_UNVERIFIED,
                confidence=TIER_CONFIDENCE[EmailTier.PATTERN_UNVERIFIED],
            )
        ]

    catchall = await smtp_verifier.is_catchall_domain(domain, redis)
    if catchall is True:
        email, label = candidates[0]
        return [
            EmailCandidate(
                email=email, label=label, tier=EmailTier.PATTERN_CATCHALL,
                confidence=TIER_CONFIDENCE[EmailTier.PATTERN_CATCHALL],
            )
        ]

    for email, label in candidates:
        verified = await smtp_verifier.verify_address(email, redis)
        if verified is True:
            return [
                EmailCandidate(
                    email=email, label=label, tier=EmailTier.PATTERN_SMTP_VERIFIED,
                    confidence=TIER_CONFIDENCE[EmailTier.PATTERN_SMTP_VERIFIED],
                )
            ]
        # verified is False -> that specific address doesn't exist, keep
        # trying the next candidate. verified is None -> couldn't confirm
        # (blocked/timeout); fall through and treat the whole attempt as
        # unverified rather than looping through every remaining candidate
        # against a server we already know isn't answering.
        if verified is None:
            break

    email, label = candidates[0]
    return [
        EmailCandidate(
            email=email, label=label, tier=EmailTier.PATTERN_UNVERIFIED,
            confidence=TIER_CONFIDENCE[EmailTier.PATTERN_UNVERIFIED],
        )
    ]


async def find_company_email(
    company_id: str,
    company_name: str,
    location: str | None,
    url: str | None,
    redis: Redis | None = None,
    groq_api_key: str | None = None,
) -> EmailResult:
    start = time.monotonic()
    result = EmailResult(company_id=company_id, company_name=company_name, location=location, input_url=url)

    try:
        resolved_url: str | None = None
        website_source = WebsiteSource.NOT_FOUND

        if url and url.strip():
            resolved_url = normalize_url(url)
            website_source = WebsiteSource.PROVIDED
        else:
            found = await website_discovery.find_official_website(company_name, location, groq_api_key, redis)
            if found:
                resolved_url = found
                website_source = WebsiteSource.WEB_SEARCH

        all_candidates: list[EmailCandidate] = []

        # Only a URL the user directly supplied is trusted enough to accept
        # an off-domain email match (e.g. a small business listing a gmail
        # address on its own real site). WEB_SEARCH and DOMAIN_GUESS are
        # both inferred - if the inference itself is wrong, an off-domain
        # email on that wrong page is most likely a completely unrelated
        # third party's address (see crawler.py's parked-domain note for a
        # confirmed real example of exactly this). Restricting those to
        # same-domain-only matches means a wrong guess just yields "nothing
        # found" instead of a confidently-wrong result.
        def _allow_offsite() -> bool:
            return website_source == WebsiteSource.PROVIDED

        if resolved_url:
            pages = await crawl_company_site(resolved_url, redis, allow_offsite=_allow_offsite())
            domain = site_domain(resolved_url)
            for page in pages:
                result.pages_checked.append(page.url)
                if page.success:
                    all_candidates.extend(extract_emails(page.content, page.url, domain, _allow_offsite()))

        if not all_candidates and not resolved_url:
            # No URL given and web search found nothing - last resort:
            # guess a domain from the company name itself and confirm it's
            # real before touching it at all.
            guesses = generate_domain_candidates(company_name, location)
            guessed_domain = await dns_utils.first_resolving_domain(guesses, redis)
            if guessed_domain:
                resolved_url = f"https://{guessed_domain}"
                website_source = WebsiteSource.DOMAIN_GUESS
                pages = await crawl_company_site(resolved_url, redis, allow_offsite=False)
                for page in pages:
                    result.pages_checked.append(page.url)
                    if page.success:
                        all_candidates.extend(extract_emails(page.content, page.url, guessed_domain, False))

        if not all_candidates and resolved_url:
            # We have a confirmed-real domain but found no live email on it
            # anywhere - try common company-inbox patterns as a last resort,
            # clearly tiered below anything actually scraped. Always strip
            # "www." first - nobody's inbox is info@www.company.com.
            all_candidates.extend(await _try_pattern_fallback(registrable_domain(resolved_url), redis))

        primary, alternates = _rank_and_split(all_candidates)

        result.resolved_url = resolved_url
        result.website_source = website_source
        result.alternate_emails = alternates
        if primary:
            result.primary_email = primary.email
            result.primary_label = primary.label
            result.primary_tier = primary.tier
            result.primary_confidence = primary.confidence
            result.primary_source_page = primary.source_page
        else:
            result.error = "website_not_found" if website_source == WebsiteSource.NOT_FOUND else "no_email_found"

        result.processing_time_ms = int((time.monotonic() - start) * 1000)
        return result

    except Exception as exc:  # noqa: BLE001 - must never crash the whole batch
        result.success = False
        result.error = str(exc)
        result.processing_time_ms = int((time.monotonic() - start) * 1000)
        return result
