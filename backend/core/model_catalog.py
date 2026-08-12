"""
Single source of truth for which free-tier models exist per provider, and
their per-model rate limits.

Both Groq and Mistral rate-limit **per model, per organization** — not one
combined number for the whole account (verified directly against
`console.groq.com/docs/rate-limits` and `docs.mistral.ai/admin/billing-usage/
usage-limits`). That means every additional model added here is a genuinely
separate quota bucket on the exact same API key, not a slice of an existing
one. `llm_router.py` fans every configured key out across every model in its
tier; `usage_tracker.py` uses the same catalog to compute "remaining today"
without depending on provider response headers (which Groq's chat-completion
path does not currently surface through litellm's Router — see
`usage_tracker.py` docstring).

Tiering: PRIMARY models are comparable-or-better than the previous single
default (`openai/gpt-oss-20b`) — safe to load-balance across interchangeably.
OVERFLOW models trade quality for a much bigger daily budget and are only
reached once every primary deployment is already rate-limited (wired via
`fallbacks` in `llm_router.py`), so a normal day looks identical in quality
to before this change, and a saturated day degrades gracefully instead of
failing outright.
"""
from __future__ import annotations

from typing import NamedTuple


class ModelSpec(NamedTuple):
    litellm_model: str  # value passed as litellm_params.model
    tag: str  # short id used in key_ref__tag identifiers and the usage UI
    rpm: int
    tpm: int
    rpd: int | None  # None = provider doesn't publish/enforce a daily request cap
    tpd: int | None  # None = provider doesn't publish/enforce a daily token cap


# Confirmed live from https://console.groq.com/docs/rate-limits (free plan).
# Excludes: groq/compound* (agentic tool-orchestration models, reserved for
# the URL-browsing fallback in url_reader.py; only 250 RPD each), the
# llama-prompt-guard-2-* moderation classifiers, and gpt-oss-safeguard-20b
# (safety-tuned, not a general chat model) — none of these are good fits for
# plain classify/generate completions.
GROQ_PRIMARY_MODELS: list[ModelSpec] = [
    ModelSpec("groq/openai/gpt-oss-20b", "gpt-oss-20b", rpm=30, tpm=8_000, rpd=1_000, tpd=200_000),
    ModelSpec("groq/openai/gpt-oss-120b", "gpt-oss-120b", rpm=30, tpm=8_000, rpd=1_000, tpd=200_000),
    ModelSpec("groq/llama-3.3-70b-versatile", "llama-3.3-70b", rpm=30, tpm=12_000, rpd=1_000, tpd=100_000),
    ModelSpec("groq/qwen/qwen3.6-27b", "qwen3.6-27b", rpm=30, tpm=8_000, rpd=1_000, tpd=200_000),
]

# Much bigger daily budget, but the weakest model of the group (8B) — only
# used once every PRIMARY deployment (across every key) is already rate
# limited, so it never dilutes quality on a normal day.
GROQ_OVERFLOW_MODELS: list[ModelSpec] = [
    ModelSpec("groq/llama-3.1-8b-instant", "llama-3.1-8b", rpm=30, tpm=6_000, rpd=14_400, tpd=500_000),
]

# Mistral confirms rate limits are also listed per-model (Admin Panel -> API
# -> Limits), but unlike Groq there is no single published table of free-tier
# numbers per model, and the catalog rotates (open-mistral-nemo is now
# deprecated in favor of the "Ministral 3" edge family). rpm/tpm below are
# COPIED from the already-tuned `mistral-small-latest` values as a
# conservative placeholder — confirm the real per-model numbers in your own
# Admin Panel before trusting them under sustained load; getting this wrong
# reproduces the exact GROQ_TPM/MISTRAL_TPM misconfiguration bug fixed
# earlier (rate-limited router never rotating away from an exhausted
# deployment). rpd/tpd are left as None since Mistral publishes a monthly
# (not daily) token allowance instead.
#
# IMPORTANT: "ministral-3-8b-latest" (previously here) is NOT a real Mistral
# model id - it was a typo conflating the two actually-separate "Ministral 3"
# edge models, "ministral-3b-latest" (3B) and "ministral-8b-latest" (8B).
# Every single Phase 2 (product generation) call hit Mistral's hard
# `400 Invalid model` error as a result - 100% of the time, not
# intermittently like a rate limit - which is why product generation kept
# falling back to Groq (see STRUCTURED_MODEL's fallback chain in
# llm_router.py) instead of ever actually using Mistral. Confirmed against
# Mistral's own docs (docs.mistral.ai) before fixing.
MISTRAL_PRIMARY_MODELS: list[ModelSpec] = [
    ModelSpec("mistral/mistral-small-latest", "mistral-small", rpm=45, tpm=45_000, rpd=None, tpd=None),
    ModelSpec("mistral/ministral-3b-latest", "ministral-3b", rpm=45, tpm=45_000, rpd=None, tpd=None),
    ModelSpec("mistral/ministral-8b-latest", "ministral-8b", rpm=45, tpm=45_000, rpd=None, tpd=None),
]

MISTRAL_OVERFLOW_MODELS: list[ModelSpec] = []

# Jina Reader isn't an LLM and is never routed through litellm's Router (see
# url_reader.py — it's a raw httpx call), so `litellm_model` here is a plain
# label, never actually passed to litellm. This single entry only exists so
# usage_tracker.py can reuse the exact same per-key/per-"model" daily-usage
# machinery it already has for Groq/Mistral instead of a second bespoke code
# path. rpm=500 is Jina's own published authenticated-key limit
# (jina.ai/reader); rpd/tpd are None because Jina publishes no daily cap for
# Reader, only the per-minute one — same "Not published" display as Mistral.
JINA_MODELS: list[ModelSpec] = [
    ModelSpec("jina/reader", "reader", rpm=500, tpm=0, rpd=None, tpd=None),
]


def specs_for_provider(provider: str) -> list[ModelSpec]:
    """All catalog specs (primary + overflow) that a key for this provider
    fans out across — used by usage_tracker.py to know which deployment ids
    to look up for a given physical key."""
    if provider == "groq":
        return [*GROQ_PRIMARY_MODELS, *GROQ_OVERFLOW_MODELS]
    if provider == "mistral":
        return [*MISTRAL_PRIMARY_MODELS, *MISTRAL_OVERFLOW_MODELS]
    if provider == "jina":
        return JINA_MODELS
    return []


def spec_for_tag(provider: str, tag: str) -> ModelSpec | None:
    for spec in specs_for_provider(provider):
        if spec.tag == tag:
            return spec
    return None


def provider_for_tag(tag: str) -> str | None:
    """Reverse lookup used when only a deployment's `key_ref__tag` id is
    known (e.g. in usage_tracker.record_usage, which has no other way to
    tell which provider a call went to)."""
    if any(spec.tag == tag for spec in specs_for_provider("groq")):
        return "groq"
    if any(spec.tag == tag for spec in specs_for_provider("mistral")):
        return "mistral"
    if any(spec.tag == tag for spec in specs_for_provider("jina")):
        return "jina"
    return None
