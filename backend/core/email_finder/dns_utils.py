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
    first_a_only: str | None = None
    for domain in candidates:
        if await has_mx(domain, redis):
            return domain
        if first_a_only is None and await has_a_record(domain, redis):
            first_a_only = domain
    if require_mx:
        return None
    return first_a_only
