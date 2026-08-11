"""
Converts a company URL into LLM-ready markdown.

Fallback chain:
  1. Redis cache          — same URL never re-fetched within 7 days
  2. Jina Reader          — r.jina.ai/{url} -> clean markdown (headless Chrome render)
  3. Groq compound-beta   — visit_website built-in tool, Groq fetches server-side
  4. name_location        — caller falls back to name+location only (no markdown)

The LLM never scrapes with a custom parser — Jina Reader (or Groq's own
compound-beta tool) does the fetching + cleaning, and the resulting
markdown is simply placed inside the LLM prompt as context.
"""
from __future__ import annotations

import hashlib
import json
import logging

import httpx
from redis.asyncio import Redis

from backend.config import get_settings
from backend.core.circuit_breaker import ContentUnavailable, jina_breaker
from backend.core.models import UrlReadResult, UrlReadSource
from backend.core.usage_tracker import record_usage

logger = logging.getLogger("url_reader")

# This same block gets re-sent to the LLM up to 3x per company (classify,
# research, generate), so trimming it is a 3x multiplier on the savings.
# Jina Reader already strips nav/boilerplate, and the highest-signal content
# (hero text, about, product listings) is almost always front-loaded on a
# homepage, so the tail past ~5000 chars is rarely load-bearing. Still worth
# spot-checking against your own data with scripts/eval_pipeline.py if you
# tighten this further - see the Tier 1 section of the usage/optimization
# plan for the full reasoning.
MAX_MARKDOWN_CHARS = 5000
CACHE_TTL_SECONDS = 7 * 24 * 3600
# The free/unauthenticated r.jina.ai tier renders noticeably slower than the
# paid tier (observed 2-16s+ per site vs sub-second priority-queue
# responses) since it has no priority queueing - 15s was tuned for the paid
# tier and was clipping legitimately-succeeding free-tier requests.
JINA_TIMEOUT = 25.0
MIN_CONTENT_LENGTH = 200
CIRCUIT_STATUS_KEY = "circuit_status:jina_reader"


def _cache_key(url: str) -> str:
    return f"url_cache:{hashlib.sha256(url.encode()).hexdigest()}"


async def _sync_circuit_status(redis: Redis | None) -> None:
    """
    Mirrors the in-process circuit breaker state into Redis so the FastAPI
    process (which never calls Jina itself) can show a live indicator of
    what the ARQ worker process is doing.
    """
    if redis is None:
        return
    try:
        await redis.set(CIRCUIT_STATUS_KEY, json.dumps(jina_breaker.status()), ex=120)
    except Exception:
        logger.warning("Failed to sync circuit breaker status to Redis", exc_info=True)


async def _fetch_jina(url: str) -> UrlReadResult:
    """r.jina.ai works completely unauthenticated - no key, no charge - just
    at lower throughput/priority than the paid tier. We still prefer sending
    the API key when one is configured (higher rate limit, priority queue),
    but a 402 specifically means THAT KEY's account balance is exhausted,
    not that Jina Reader itself is unusable - retrying the exact same
    request without the Authorization header falls through to the free
    tier instead of abandoning Jina entirely and degrading straight to
    compound-beta/name+location for every single company.
    """
    settings = get_settings()
    jina_url = f"https://r.jina.ai/{url}"
    base_headers = {"Accept": "application/json", "X-Return-Format": "markdown"}

    async with httpx.AsyncClient(timeout=JINA_TIMEOUT) as client:
        resp = None
        if settings.JINA_API_KEY:
            headers = {**base_headers, "Authorization": f"Bearer {settings.JINA_API_KEY}"}
            resp = await client.get(jina_url, headers=headers)
            if resp.status_code == 402:
                logger.warning("Jina API key has no balance (402) - falling back to free/unauthenticated tier")
                resp = None

        if resp is None:
            resp = await client.get(jina_url, headers=base_headers)

        # 400/404/422 mean "Jina looked at THIS url and couldn't render it"
        # (malformed, dead domain, JS it refuses to execute, etc) - that's a
        # fact about the company's website, not about Jina's health, so it
        # must not count as a circuit-breaker failure (see ContentUnavailable).
        if resp.status_code in (400, 404, 422):
            raise ContentUnavailable(f"jina_rejected_{resp.status_code}")
        resp.raise_for_status()
        data = resp.json()
        content = data.get("data", {}).get("content", "") or data.get("content", "")

        if len(content) < MIN_CONTENT_LENGTH:
            raise ContentUnavailable("empty_content")

        markdown = content[:MAX_MARKDOWN_CHARS]
        return UrlReadResult(
            markdown=markdown,
            source=UrlReadSource.JINA_READER,
            success=True,
            token_estimate=len(markdown) // 4,
        )


async def _fetch_compound_beta(
    url: str, groq_api_key: str, redis: Redis | None = None, key_ref: str | None = None
) -> UrlReadResult:
    """Groq's `groq/compound` built-in `visit_website` tool fetches the URL
    server-side.

    Note: the old `compound-beta` model + `"tools": [{"type": "visit_website"}]`
    payload (an OpenAI-style tools array) is REJECTED by Groq's current API
    with `tools[0].type must be one of [function, mcp]` - built-in tools are
    now configured via the Groq-specific `compound_custom.tools.enabled_tools`
    field on the current `groq/compound` model, and `visit_website` is only
    available on the "latest" Compound version (the `Groq-Model-Version`
    header), not the default pinned one.

    This bypasses the litellm Router entirely (raw httpx call), so unlike
    every other LLM call in this app it gets Groq's REAL rate-limit headers
    directly off `resp.headers` - litellm's Router path for Groq models
    doesn't currently surface them at all (see usage_tracker.py docstring).
    """
    async with httpx.AsyncClient(timeout=20.0) as client:
        resp = await client.post(
            "https://api.groq.com/openai/v1/chat/completions",
            headers={
                "Authorization": f"Bearer {groq_api_key}",
                "Groq-Model-Version": "latest",
            },
            json={
                "model": "groq/compound",
                "messages": [
                    {
                        "role": "user",
                        "content": (
                            f"Visit {url} and summarize: what products/services this company "
                            "offers, their industry, target markets, and any specific product "
                            "names mentioned. Be factual and specific."
                        ),
                    }
                ],
                "compound_custom": {"tools": {"enabled_tools": ["visit_website"]}},
            },
        )
        resp.raise_for_status()
        data = resp.json()
        content = data["choices"][0]["message"]["content"]

        if redis is not None and key_ref is not None:
            usage = data.get("usage") or {}
            try:
                await record_usage(
                    redis,
                    f"{key_ref}__compound",
                    "groq",
                    usage.get("prompt_tokens", 0),
                    usage.get("completion_tokens", 0),
                    headers=dict(resp.headers),
                )
            except Exception:
                logger.debug("compound-beta usage capture failed", exc_info=True)

        if len(content) < MIN_CONTENT_LENGTH:
            raise ValueError("compound_beta_empty")

        markdown = content[:MAX_MARKDOWN_CHARS]
        return UrlReadResult(
            markdown=markdown,
            source=UrlReadSource.COMPOUND_BETA,
            success=True,
            token_estimate=len(markdown) // 4,
        )


async def read_url_for_llm(
    url: str | None,
    redis: Redis | None = None,
    groq_fallback_key: str | None = None,
    groq_fallback_key_ref: str | None = None,
) -> UrlReadResult:
    """
    Main entry point. Returns a UrlReadResult that the pipeline feeds
    directly into the LLM prompt as context. Never raises — always
    degrades gracefully to the name_location result.
    """
    if not url or not url.strip():
        return UrlReadResult(source=UrlReadSource.NONE, success=False, error="no_url_provided")

    url = url.strip()
    if not url.startswith(("http://", "https://")):
        url = f"https://{url}"

    # 1. Redis cache
    if redis is not None:
        try:
            cached = await redis.get(_cache_key(url))
            if cached:
                result = UrlReadResult(**json.loads(cached))
                result.from_cache = True
                return result
        except Exception:
            logger.warning("Redis cache read failed for %s", url, exc_info=True)

    # 2 & 3. Jina Reader (via circuit breaker) -> compound-beta fallback
    #
    # CircuitBreaker.call() invokes `fallback(*args, **kwargs)` with the same
    # args it passed to the primary function (here: the `url` positional
    # arg), so this closure must accept and ignore them - it already gets
    # `url`/`groq_fallback_key` from the enclosing scope. Without *_args here,
    # every single Jina failure (real 422s, empty content, or once the
    # circuit trips OPEN) crashed on `_compound_fallback() takes 0 positional
    # arguments but 1 was given`, silently degrading every one of those
    # companies all the way to name+location only - compound-beta was never
    # actually reached despite the "AI Browse fallback" UI badge implying it was.
    async def _compound_fallback(*_args, **_kwargs) -> UrlReadResult:
        if not groq_fallback_key:
            return UrlReadResult(
                source=UrlReadSource.NAME_LOCATION, success=False, error="no_fallback_key"
            )
        try:
            return await _fetch_compound_beta(url, groq_fallback_key, redis, groq_fallback_key_ref)
        except Exception as exc:
            logger.info("compound-beta fallback failed for %s: %s", url, exc)
            return UrlReadResult(
                source=UrlReadSource.NAME_LOCATION, success=False, error=f"compound_failed:{exc}"
            )

    try:
        result = await jina_breaker.call(_fetch_jina, url, fallback=_compound_fallback)
    except Exception as exc:
        logger.info("URL read fully failed for %s: %s", url, exc)
        result = UrlReadResult(source=UrlReadSource.NAME_LOCATION, success=False, error=str(exc))
    finally:
        await _sync_circuit_status(redis)

    # 4. Cache successful results
    if redis is not None and result.success:
        try:
            await redis.setex(_cache_key(url), CACHE_TTL_SECONDS, result.model_dump_json())
        except Exception:
            logger.warning("Redis cache write failed for %s", url, exc_info=True)

    return result
