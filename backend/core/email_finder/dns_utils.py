"""
Cheap DNS validation used to prune domain guesses before spending a real
HTTP fetch or SMTP handshake on them. Results are cached in Redis (when
available) since the same domain is often re-checked across companies in
the same batch and DNS lookups add up at scale.
"""
from __future__ import annotations

import logging

import dns.asyncresolver
import dns.exception
from redis.asyncio import Redis

logger = logging.getLogger("email_finder.dns")

DNS_TIMEOUT = 4.0
DNS_CACHE_TTL_SECONDS = 7 * 24 * 3600


def _cache_key(domain: str, record_type: str) -> str:
    return f"dns_cache:{record_type}:{domain}"


async def _cached_lookup(redis: Redis | None, domain: str, record_type: str) -> bool | None:
    if redis is None:
        return None
    try:
        cached = await redis.get(_cache_key(domain, record_type))
        if cached is None:
            return None
        return cached in (b"1", "1")
    except Exception:
        return None


async def _store_lookup(redis: Redis | None, domain: str, record_type: str, resolved: bool) -> None:
    if redis is None:
        return
    try:
        await redis.setex(_cache_key(domain, record_type), DNS_CACHE_TTL_SECONDS, "1" if resolved else "0")
    except Exception:
        logger.debug("DNS cache write failed for %s", domain, exc_info=True)


async def _resolves(domain: str, record_type: str, redis: Redis | None) -> bool:
    cached = await _cached_lookup(redis, domain, record_type)
    if cached is not None:
        return cached

    resolver = dns.asyncresolver.Resolver()
    resolver.timeout = DNS_TIMEOUT
    resolver.lifetime = DNS_TIMEOUT
    try:
        await resolver.resolve(domain, record_type)
        resolved = True
    except (dns.exception.DNSException, Exception):
        resolved = False

    await _store_lookup(redis, domain, record_type, resolved)
    return resolved


async def has_mx(domain: str, redis: Redis | None = None) -> bool:
    """True if the domain has mail servers configured - the strongest signal
    that it's a real, actively-used company domain (a parked/for-sale domain
    almost never has MX records)."""
    return await _resolves(domain, "MX", redis)


async def has_a_record(domain: str, redis: Redis | None = None) -> bool:
    """True if the domain resolves at all (website exists even if mail isn't
    configured there - some companies host mail on a different domain)."""
    return await _resolves(domain, "A", redis)


async def first_resolving_domain(
    candidates: list[str], redis: Redis | None = None, require_mx: bool = False
) -> str | None:
    """Checks candidates in order, returns the first that resolves. Prefers
    MX (real mail server) over a bare A record when both exist among the
    candidates, since that's the strongest "this is a real, live company
    domain" signal - but falls back to an A-record-only match rather than
    reporting nothing, since plenty of legitimate small businesses host mail
    elsewhere (Google Workspace on a subdomain, etc.) while still serving
    their main site off the guessed domain."""
    results = await resolving_domains(candidates, redis, limit=1, require_mx=require_mx)
    return results[0] if results else None


async def resolving_domains(
    candidates: list[str], redis: Redis | None = None, limit: int = 3, require_mx: bool = False
) -> list[str]:
    """Like first_resolving_domain, but returns up to `limit` resolving
    candidates instead of committing to just one.

    This exists because "does DNS say this domain exists" is a much weaker
    signal than it looks - confirmed real case: a company's actual site is
    "bienen-wiese.de", but the guessed no-hyphen variant "bienenwiese.de"
    is ALSO a real, unrelated, MX-having registered domain that happens to
    exist. Under the old first-match-wins behavior, that unrelated domain
    won the DNS check (tried first, since guessing tries the concatenated
    form before the hyphenated one) and the pipeline committed to it
    permanently - even though its page couldn't even be fetched at all,
    let alone shown to be the real company's site. Returning several
    candidates lets the caller (pipeline.py) actually try rendering each
    one and only settle on the first that produces real content, instead
    of trusting DNS resolution alone to mean "this is the right domain."""
    mx_matches: list[str] = []
    a_only_matches: list[str] = []
    for domain in candidates:
        if len(mx_matches) >= limit:
            break
        if await has_mx(domain, redis):
            mx_matches.append(domain)
        elif not require_mx and len(a_only_matches) < limit and await has_a_record(domain, redis):
            a_only_matches.append(domain)

    if require_mx:
        return mx_matches[:limit]
    # MX-confirmed candidates ranked ahead of A-record-only ones, same
    # preference as the original single-winner behavior.
    return (mx_matches + a_only_matches)[:limit]
