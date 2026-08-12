"""
Real per-key, per-model usage tracking for every configured Groq/Mistral API
key (system pool + user-added) plus every user-added Jina Reader key (no
system pool exists for Jina - see config.py/llm_router.pick_jina_key), so
Settings can show "how much of today's free quota is left" instead of the
user finding out via a wall of 429s.

Two data sources, used opportunistically:

1. **Token usage** (`response.usage.prompt_tokens/completion_tokens`) — always
   present in the response body itself regardless of provider, so this is
   the reliable core of the feature. Combined with our own cumulative Redis
   counters, this gives exact daily/monthly totals per (key, model).

2. **Live provider rate-limit headers** — a bonus when available, NOT relied
   upon. Verified directly against the installed litellm version's source
   (`llms/openai_like/chat/handler.py`, which backs `groq/*` models): Groq
   calls made through litellm's Router never populate `response
   ._hidden_params["headers"]` / `_response_headers` at all, even with
   `litellm.return_response_headers = True` set — only the generic
   OpenAI-compatible path (which Mistral uses) does. So for Groq we can
   *never* get a live "x-ratelimit-remaining-tokens" style header through
   this router; for Mistral we opportunistically can. Given that asymmetry,
   "remaining today" is computed the same way for both providers: our own
   cumulative usage today subtracted from the known static per-model
   rd/tpd limits in `model_catalog.py` — accurate as long as every call is
   captured (see `capture-hook` wiring in structured_output.py/url_reader.py),
   and provider-agnostic by construction.

Storage: Redis only (daily hashes + a small "latest call" snapshot per (key,
model)), consistent with how the rest of the app already tracks
ephemeral/rolling state (job control, circuit breaker, run locks).
"""
from __future__ import annotations

import logging
import re
from datetime import datetime, timedelta, timezone

from redis.asyncio import Redis
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.config import get_settings
from backend.core.model_catalog import specs_for_provider
from backend.db.models import UserApiKey

logger = logging.getLogger("usage_tracker")

DAILY_TTL_SECONDS = 400 * 24 * 3600  # a little over a year, so month-over-month history survives
MONTH_WINDOW_DAYS = 30

_enabled = False


def enable_usage_capture() -> None:
    """Call once at process startup (FastAPI lifespan + ARQ worker startup).
    Idempotent and safe to call from both processes."""
    global _enabled
    if _enabled:
        return
    try:
        import litellm

        litellm.return_response_headers = True
        _enabled = True
    except Exception:
        logger.warning("Failed to enable litellm usage/header capture", exc_info=True)


_MASK_CHAR = "\u25cf"


def _mask(key: str) -> str:
    if len(key) <= 8:
        return _MASK_CHAR * len(key)
    return f"{key[:4]}{_MASK_CHAR * 8}{key[-4:]}"


def _to_int(value: object) -> int | None:
    if value is None:
        return None
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return None


_DURATION_RE = re.compile(r"(?:(?P<minutes>\d+)m)?(?:(?P<seconds>\d+(?:\.\d+)?)s)?")


def _parse_duration(value: object) -> float | None:
    """Parses Groq's "2m59.56s" / "7.66s" reset-countdown strings into seconds."""
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    match = _DURATION_RE.fullmatch(str(value).strip())
    if not match or not (match.group("minutes") or match.group("seconds")):
        return None
    minutes = float(match.group("minutes") or 0)
    seconds = float(match.group("seconds") or 0)
    return minutes * 60 + seconds


def parse_provider_headers(provider: str, headers: dict) -> dict:
    """Normalizes each provider's rate-limit headers into one shape. Returns
    all-None when headers are empty/absent - callers must not assume any
    key is populated (see module docstring re: Groq never surfacing these
    through the router path today)."""
    out: dict[str, float | int | None] = {
        "remaining_requests": None,
        "limit_requests": None,
        "remaining_tokens": None,
        "limit_tokens": None,
        "reset_requests_s": None,
        "reset_tokens_s": None,
    }
    if not headers:
        return out
    h = {str(k).lower(): v for k, v in dict(headers).items()}

    if provider == "groq":
        out["remaining_requests"] = _to_int(h.get("x-ratelimit-remaining-requests"))
        out["limit_requests"] = _to_int(h.get("x-ratelimit-limit-requests"))
        out["remaining_tokens"] = _to_int(h.get("x-ratelimit-remaining-tokens"))
        out["limit_tokens"] = _to_int(h.get("x-ratelimit-limit-tokens"))
        out["reset_requests_s"] = _parse_duration(h.get("x-ratelimit-reset-requests"))
        out["reset_tokens_s"] = _parse_duration(h.get("x-ratelimit-reset-tokens"))
    elif provider == "mistral":
        out["remaining_tokens"] = _to_int(h.get("x-ratelimitbysize-remaining-minute"))
        out["limit_tokens"] = _to_int(h.get("x-ratelimitbysize-limit-minute"))
        out["reset_tokens_s"] = _to_int(h.get("x-ratelimitbysize-reset-minute"))
    return out


def _daily_key(model_id: str, date_str: str) -> str:
    return f"usage:daily:{model_id}:{date_str}"


def _latest_key(model_id: str) -> str:
    return f"usage:latest:{model_id}"


async def record_usage(
    redis: Redis | None,
    model_id: str | None,
    provider: str,
    prompt_tokens: int,
    completion_tokens: int,
    cached_tokens: int = 0,
    headers: dict | None = None,
) -> None:
    """Best-effort - wrapped so a tracking failure never breaks a company's
    processing. `model_id` is the `key_ref__tag` deployment id litellm
    reports back via `response._hidden_params["model_id"]` (see
    llm_router._deployment); daily/monthly totals are keyed by it directly
    so per-model breakdowns are exact, not inferred."""
    if redis is None or not model_id:
        return
    try:
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        daily_key = _daily_key(model_id, today)
        pipe = redis.pipeline()
        pipe.hincrby(daily_key, "requests", 1)
        pipe.hincrby(daily_key, "prompt_tokens", int(prompt_tokens or 0))
        pipe.hincrby(daily_key, "completion_tokens", int(completion_tokens or 0))
        pipe.hincrby(daily_key, "cached_tokens", int(cached_tokens or 0))
        pipe.expire(daily_key, DAILY_TTL_SECONDS)

        snapshot = parse_provider_headers(provider, headers or {})
        latest_key = _latest_key(model_id)
        mapping = {k: ("" if v is None else v) for k, v in snapshot.items()}
        mapping["last_used_at"] = datetime.now(timezone.utc).isoformat()
        # Individual HSET-per-field calls rather than one multi-field
        # `hset(..., mapping=...)`: some deployments (e.g. the legacy
        # Windows Redis 3.0 port) predate Redis 4.0's multi-field HSET and
        # hard-reject it with "wrong number of arguments" - looping is a
        # couple of extra pipelined round-trip-free commands, not extra
        # round trips, so there's no real cost to being compatible here.
        for field, value in mapping.items():
            pipe.hset(latest_key, field, value)
        pipe.expire(latest_key, DAILY_TTL_SECONDS)

        await pipe.execute()
    except Exception:
        logger.warning("record_usage failed for model_id=%s", model_id, exc_info=True)


def _decode_hash(raw: dict | None) -> dict:
    if not raw:
        return {}
    out = {}
    for k, v in raw.items():
        key = k.decode() if isinstance(k, bytes) else k
        val = v.decode() if isinstance(v, bytes) else v
        out[key] = val
    return out


async def _enumerate_physical_keys(session: AsyncSession, user_id: str) -> list[dict]:
    settings = get_settings()
    items: list[dict] = []
    for i, key in enumerate(settings.groq_keys):
        items.append(
            {"key_ref": f"system-groq-{i}", "provider": "groq", "label": f"System Groq #{i + 1}", "masked_key": _mask(key), "is_system": True}
        )
    for i, key in enumerate(settings.mistral_keys):
        items.append(
            {"key_ref": f"system-mistral-{i}", "provider": "mistral", "label": f"System Mistral #{i + 1}", "masked_key": _mask(key), "is_system": True}
        )

    # "jina" is included here even though it has no system-pool counterpart
    # above (see config.py/llm_router.pick_jina_key) - a user's Jina key is
    # the ONLY way Reader calls ever get authenticated, so it must show up
    # in the same usage table as everything else or there's no way to tell
    # it's actually being used.
    result = await session.execute(
        select(UserApiKey).where(UserApiKey.user_id == user_id, UserApiKey.provider.in_(("groq", "mistral", "jina")))
    )
    for k in result.scalars().all():
        items.append(
            {"key_ref": f"user-{k.id}", "provider": k.provider, "label": k.label, "masked_key": _mask(k.api_key), "is_system": False}
        )
    return items


async def get_usage_overview(redis: Redis | None, session: AsyncSession, user_id: str) -> list[dict]:
    """One entry per physical key, each with a per-model breakdown (since a
    single key now fans out across several models, each with its own
    independent quota - see model_catalog.py)."""
    if redis is None:
        return []

    keys = await _enumerate_physical_keys(session, user_id)
    if not keys:
        return []

    today = datetime.now(timezone.utc)
    dates = [(today - timedelta(days=d)).strftime("%Y-%m-%d") for d in range(MONTH_WINDOW_DAYS)]

    # Flatten (key, model) pairs so every hash read happens in one pipeline
    # round-trip instead of N+1 awaits.
    pairs = []
    for key_item in keys:
        for spec in specs_for_provider(key_item["provider"]):
            pairs.append((key_item, spec))

    pipe = redis.pipeline()
    for key_item, spec in pairs:
        model_id = f"{key_item['key_ref']}__{spec.tag}"
        pipe.hgetall(_latest_key(model_id))
        for date_str in dates:
            pipe.hgetall(_daily_key(model_id, date_str))
    raw = await pipe.execute()

    per_model_data: dict[str, dict] = {}
    idx = 0
    for key_item, spec in pairs:
        model_id = f"{key_item['key_ref']}__{spec.tag}"
        latest = _decode_hash(raw[idx])
        idx += 1
        daily_hashes = [_decode_hash(raw[idx + n]) for n in range(MONTH_WINDOW_DAYS)]
        idx += MONTH_WINDOW_DAYS

        today_hash = daily_hashes[0]
        requests_today = int(today_hash.get("requests") or 0)
        prompt_today = int(today_hash.get("prompt_tokens") or 0)
        completion_today = int(today_hash.get("completion_tokens") or 0)
        cached_today = int(today_hash.get("cached_tokens") or 0)

        requests_month = sum(int(h.get("requests") or 0) for h in daily_hashes)
        prompt_month = sum(int(h.get("prompt_tokens") or 0) for h in daily_hashes)
        completion_month = sum(int(h.get("completion_tokens") or 0) for h in daily_hashes)

        tokens_today = prompt_today + completion_today
        tokens_month = prompt_month + completion_month

        remaining_requests_today = max(spec.rpd - requests_today, 0) if spec.rpd is not None else None
        remaining_tokens_today = max(spec.tpd - tokens_today, 0) if spec.tpd is not None else None

        per_model_data[model_id] = {
            "tag": spec.tag,
            "requests_today": requests_today,
            "tokens_today": tokens_today,
            "cached_tokens_today": cached_today,
            "requests_month": requests_month,
            "tokens_month": tokens_month,
            "limit_requests_per_day": spec.rpd,
            "limit_tokens_per_day": spec.tpd,
            "remaining_requests_today": remaining_requests_today,
            "remaining_tokens_today": remaining_tokens_today,
            "live_remaining_tokens": _to_int(latest.get("remaining_tokens")),
            "live_limit_tokens": _to_int(latest.get("limit_tokens")),
            "live_reset_tokens_s": _to_int(latest.get("reset_tokens_s")) if latest.get("reset_tokens_s") not in (None, "") else None,
            "last_used_at": latest.get("last_used_at") or None,
        }

    out: list[dict] = []
    for key_item in keys:
        models = [per_model_data[f"{key_item['key_ref']}__{spec.tag}"] for spec in specs_for_provider(key_item["provider"])]
        requests_today = sum(m["requests_today"] for m in models)
        tokens_today = sum(m["tokens_today"] for m in models)
        requests_month = sum(m["requests_month"] for m in models)
        tokens_month = sum(m["tokens_month"] for m in models)
        last_used_candidates = [m["last_used_at"] for m in models if m["last_used_at"]]
        out.append(
            {
                **key_item,
                "requests_today": requests_today,
                "tokens_today": tokens_today,
                "requests_month": requests_month,
                "tokens_month": tokens_month,
                "last_used_at": max(last_used_candidates) if last_used_candidates else None,
                "models": models,
            }
        )
    return out


def get_usage_summary(items: list[dict]) -> list[dict]:
    """Groups the per-key overview by provider for an overall usage view."""
    by_provider: dict[str, dict] = {}
    for item in items:
        bucket = by_provider.setdefault(
            item["provider"],
            {"provider": item["provider"], "key_count": 0, "requests_today": 0, "tokens_today": 0, "requests_month": 0, "tokens_month": 0},
        )
        bucket["key_count"] += 1
        bucket["requests_today"] += item["requests_today"]
        bucket["tokens_today"] += item["tokens_today"]
        bucket["requests_month"] += item["requests_month"]
        bucket["tokens_month"] += item["tokens_month"]
    return list(by_provider.values())
