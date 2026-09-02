"""
Non-invasive SMTP verification: connects to a domain's real mail server and
asks "would you accept mail to this address" (RCPT TO) WITHOUT ever sending
an actual message (no DATA command). This is the same technique every
email-verification SaaS (Hunter.io, NeverBounce, ZeroBounce...) uses -
it's a read-only handshake, not spam.

Two real-world limitations, by design not a bug:
  1. Many mail providers (Gmail/Google Workspace, Microsoft 365 especially)
     either reject verification probes outright or run "catch-all" mode
     (accept every RCPT TO, even nonsense ones) specifically to defeat this
     technique. We detect catch-all by probing a random nonsense address
     alongside the real guess - if both succeed, the check is inconclusive
     and we say so (EmailTier.PATTERN_CATCHALL), rather than pretending it's
     confirmed.
  2. Outbound port 25 is blocked by default on several cloud hosts (common
     anti-spam policy on AWS/Azure/GCP). If the connection can't even be
     opened, this degrades to "unverified" rather than crashing - see
     SMTP_VERIFICATION_ENABLED below, which lets ops turn this whole step
     off if the hosting environment doesn't allow outbound 25 at all.
"""
from __future__ import annotations

import asyncio
import contextlib
import logging
import secrets

import dns.asyncresolver
import dns.exception
from redis.asyncio import Redis

logger = logging.getLogger("email_finder.smtp")

SMTP_CONNECT_TIMEOUT = 6.0
SMTP_COMMAND_TIMEOUT = 6.0
# Verification lives on the network's mercy far more than any other part of
# this pipeline - keep it to a handful of concurrent connections app-wide so
# a big batch doesn't look like a port scan to any single mail provider.
MAX_CONCURRENT_SMTP_CHECKS = 4
_smtp_semaphore = asyncio.Semaphore(MAX_CONCURRENT_SMTP_CHECKS)

CATCHALL_CACHE_TTL_SECONDS = 14 * 24 * 3600
RESULT_CACHE_TTL_SECONDS = 14 * 24 * 3600
# The address our probe pretends to be "from" - a neutral, clearly
# non-deliverable placeholder, never a real mailbox.
_PROBE_MAIL_FROM = "verify-probe@example.com"


class SmtpUnreachable(Exception):
    """Connection/handshake couldn't be completed at all (blocked port,
    timeout, no MX, etc.) - verification is simply not possible, distinct
    from "we connected and got a real answer"."""


async def _mx_host(domain: str) -> str | None:
    resolver = dns.asyncresolver.Resolver()
    resolver.timeout = SMTP_CONNECT_TIMEOUT
    resolver.lifetime = SMTP_CONNECT_TIMEOUT
    try:
        answer = await resolver.resolve(domain, "MX")
    except (dns.exception.DNSException, Exception):
        return None
    records = sorted(answer, key=lambda r: r.preference)
    return str(records[0].exchange).rstrip(".") if records else None


async def _read_response(reader: asyncio.StreamReader) -> str:
    line = await asyncio.wait_for(reader.readline(), timeout=SMTP_COMMAND_TIMEOUT)
    return line.decode(errors="ignore")


async def _rcpt_accepts(mx_host: str, email: str) -> bool:
    """Opens ONE connection and asks about ONE address, then disconnects
    immediately (RSET+QUIT) - never issues DATA, so no message is ever
    actually sent, regardless of the answer."""
    reader, writer = await asyncio.wait_for(
        asyncio.open_connection(mx_host, 25), timeout=SMTP_CONNECT_TIMEOUT
    )
    try:
        await _read_response(reader)  # banner

        writer.write(b"EHLO emailverifier.local\r\n")
        await writer.drain()
        await _read_response(reader)

        writer.write(f"MAIL FROM:<{_PROBE_MAIL_FROM}>\r\n".encode())
        await writer.drain()
        await _read_response(reader)

        writer.write(f"RCPT TO:<{email}>\r\n".encode())
        await writer.drain()
        resp = await _read_response(reader)

        writer.write(b"QUIT\r\n")
        await writer.drain()

        return resp.startswith("250")
    finally:
        writer.close()
        with contextlib.suppress(Exception):
            await writer.wait_closed()


async def _cache_get(redis: Redis | None, key: str) -> str | None:
    if redis is None:
        return None
    try:
        val = await redis.get(key)
        return val.decode() if isinstance(val, bytes) else val
    except Exception:
        return None


async def _cache_set(redis: Redis | None, key: str, value: str, ttl: int) -> None:
    if redis is None:
        return
    try:
        await redis.setex(key, ttl, value)
    except Exception:
        pass


async def is_catchall_domain(domain: str, redis: Redis | None = None) -> bool | None:
    """True/False if determined, None if the domain couldn't be reached at
    all (verification inconclusive for a different reason)."""
    cache_key = f"smtp_catchall:{domain}"
    cached = await _cache_get(redis, cache_key)
    if cached is not None:
        return cached == "1"

    mx_host = await _mx_host(domain)
    if not mx_host:
        return None

    bogus = f"verify-probe-{secrets.token_hex(8)}@{domain}"
    async with _smtp_semaphore:
        try:
            accepted = await _rcpt_accepts(mx_host, bogus)
        except Exception:
            return None

    await _cache_set(redis, cache_key, "1" if accepted else "0", CATCHALL_CACHE_TTL_SECONDS)
    return accepted


async def verify_address(email: str, redis: Redis | None = None) -> bool | None:
    """True = mail server explicitly confirmed it'll accept this address.
    False = explicitly rejected. None = couldn't verify (blocked/timeout/no MX)."""
    domain = email.rsplit("@", 1)[-1].lower()
    cache_key = f"smtp_verify:{email.lower()}"
    cached = await _cache_get(redis, cache_key)
    if cached is not None:
        return {"1": True, "0": False}.get(cached)

    mx_host = await _mx_host(domain)
    if not mx_host:
        return None

    async with _smtp_semaphore:
        try:
            accepted = await _rcpt_accepts(mx_host, email)
        except Exception:
            logger.debug("SMTP verification unreachable for %s", email, exc_info=True)
            return None

    await _cache_set(redis, cache_key, "1" if accepted else "0", RESULT_CACHE_TTL_SECONDS)
    return accepted
