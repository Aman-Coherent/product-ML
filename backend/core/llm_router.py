"""
Per-user LiteLLM Router factory. For Groq/Mistral, a user's own key (added in
Settings) always takes priority over the shared system pool: if the user has
at least one active key for a provider, ONLY their key(s) are used for that
provider's model group and the system pool is left out entirely; the system
pool (8 Groq + 8 Mistral from the shared env file) is only used as a fallback
for users who have not added their own key for that provider yet. Claude/
OpenAI/custom keys have no system equivalent and are always user-supplied.

Every key is fanned out across every free model in its provider's catalog
(see model_catalog.py) — Groq/Mistral rate-limit per model per org, so each
extra model is an entirely separate quota bucket on the same key, not a
slice of an existing one. PRIMARY-tier models are comparable-or-better than
the old single default and are load-balanced interchangeably; OVERFLOW-tier
models (much bigger daily budget, weaker model) are only reached once every
PRIMARY deployment is already rate-limited, via `fallbacks` below.

The same priority also applies to the one Groq call that bypasses this
Router entirely: the compound-beta URL-read fallback in pipeline.py calls
Groq's raw API directly (see `pick_groq_fallback_key()` below), and it
respects the exact same "user key present -> never touch the system pool"
rule as everything else here.

RPM/TPM counters used for routing decisions (`usage-based-routing-v2`) are
process-local in-memory only - see the comment above `Router(**router_kwargs)`
below for why Redis-backed cross-process sync is deliberately NOT wired in.
Separately, actual token/request usage *display* data (Settings -> API keys)
is tracked independently in usage_tracker.py, which does use Redis and is
unaffected by that decision.
"""
from __future__ import annotations

import ssl

import litellm
from litellm import Router

from backend.config import get_settings
from backend.core.model_catalog import (
    GROQ_OVERFLOW_MODELS,
    GROQ_PRIMARY_MODELS,
    MISTRAL_PRIMARY_MODELS,
    ModelSpec,
)

FAST_MODEL = "fast"                    # Groq primary tier — classification + research
FAST_OVERFLOW_MODEL = "fast_overflow"  # Groq overflow tier — only once FAST_MODEL is saturated
STRUCTURED_MODEL = "structured"        # Mistral — structured product generation
QUALITY_MODEL = "quality"              # user's Claude/OpenAI/custom key, if configured

# Mistral bills cached-prefix tokens at 10% instead of 100% when every call
# shares the same `prompt_cache_key`. generate_products' system prompt +
# schema (~600 tokens) is byte-identical across every company, so this is a
# near-total-cost win on the biggest static chunk of that call. Baked into
# every Mistral deployment's litellm_params (see _deployment()'s docstring
# for why NOT a generic per-call kwarg). Version-suffixed so a future prompt
# edit starts a fresh cache instead of silently caching stale text.
MISTRAL_PROMPT_CACHE_KEY = "product-generation-v1"

# litellm has no request timeout by default (falls back to the underlying
# SDK's default, ~600s). A single hung network call at that length can pin a
# concurrency slot for ten minutes, and it silently degrades throughput to a
# crawl as more slots leak away one at a time over a large job. 30s is well
# above what a normal completion takes; a genuinely stuck provider fails
# fast and frees the slot for the next company instead of hanging the batch.
REQUEST_TIMEOUT = 30.0

# litellm creates ~4 separate httpx client objects PER deployment (sync,
# async, sync-stream, async-stream), and httpx builds a brand new SSLContext
# for each one whenever `verify` is a bool - which means re-parsing the
# entire certifi CA bundle from disk (`ssl.create_default_context()` +
# `load_verify_locations()`, NOT just opening the file) every single time.
# On this stack that parse costs ~0.6s per client. With the multi-model
# fanout in this file (each key now fanned out across every model in its
# provider's catalog), a single build_router() call creates 50+ deployments
# - 200+ httpx clients - which measured as a 130+ second hang on every job
# start (confirmed via faulthandler.dump_traceback_later, stuck in
# httpx._config.load_ssl_context_verify). httpx explicitly special-cases
# `verify=<ssl.SSLContext instance>` to reuse it as-is with zero re-parsing
# (see httpx._config.SSLConfig.load_ssl_context_verify), so building this
# context ONCE and pointing litellm's global `ssl_verify` at it collapses
# build_router() back down to ~0.06s regardless of deployment count, with
# identical certificate verification behavior (same certifi CA bundle,
# loaded once instead of N times).
if not isinstance(litellm.ssl_verify, ssl.SSLContext):
    litellm.ssl_verify = ssl.create_default_context()


def _deployment(
    model_name_group: str,
    key_ref: str,
    api_key: str,
    spec: ModelSpec,
    extra_litellm_params: dict | None = None,
) -> dict:
    """One (key, model) deployment. `model_info.id` is set to `key_ref__tag`
    so it's BOTH a globally-unique id litellm's router needs to track
    per-deployment cooldowns/rpm/tpm correctly, AND a way to recover which
    physical key + model handled a given response afterward (see
    usage_tracker.record_usage, which reads `response._hidden_params
    ["model_id"]` and splits on "__").

    IMPORTANT: `key_ref` (and this whole id) must never contain a colon.
    litellm's usage-based-routing-v2 (lowest_tpm_rpm_v2.py) builds its own
    Redis/in-memory cache keys as f"{deployment_id}:tpm:{minute}" and then
    recovers the deployment id by naively doing `cache_key.split(":")[0]` -
    it assumes the id itself has zero colons. An earlier version of this
    file used "system:groq:0" / "user:<uuid>" style refs with a "::tag"
    suffix, which silently truncated every single deployment id down to
    just "system" or "user", made every id comparison fail, and caused
    100% of calls to raise "No deployments available" regardless of actual
    usage - reproduced and confirmed via a router-only completion call, no
    real rate limiting involved. Hyphens for the ref itself and a double
    underscore for the tag suffix keep ids human-readable while staying
    entirely colon-free.

    `extra_litellm_params` (e.g. Mistral's `prompt_cache_key`) is baked into
    THIS deployment's own litellm_params rather than passed as a generic
    per-call kwarg from the caller. That distinction matters: litellm builds
    each provider request as `{**deployment_litellm_params, **call_kwargs}`,
    so a provider-specific call kwarg silently survives a `fallbacks` hop to
    a completely different provider's deployment. Reproduced live: passing
    `prompt_cache_key` as a call-level kwarg meant that the moment Mistral
    failed (e.g. a 402) and litellm fell back to Groq, Groq rejected the
    request outright with `property 'prompt_cache_key' is unsupported` -
    turning one provider's outage into a total product-generation outage
    instead of a clean fallback. Scoping it to the deployment's own
    litellm_params means it only ever appears on requests actually sent to
    that deployment."""
    return {
        "model_name": model_name_group,
        "litellm_params": {
            "model": spec.litellm_model,
            "api_key": api_key,
            "rpm": spec.rpm,
            "tpm": spec.tpm,
            **(extra_litellm_params or {}),
        },
        "model_info": {"id": f"{key_ref}__{spec.tag}"},
    }


def _add_fanout(
    model_list: list[dict],
    model_name_group: str,
    key_ref: str,
    api_key: str,
    specs: list[ModelSpec],
    extra_litellm_params: dict | None = None,
) -> None:
    for spec in specs:
        model_list.append(_deployment(model_name_group, key_ref, api_key, spec, extra_litellm_params))


def build_router(user_keys: list[dict] | None = None) -> Router:
    """
    user_keys: list of {"id": str, "provider": "claude"|"openai"|"custom"|"groq"|"mistral",
                          "api_key": str, "model_name": str|None, "base_url": str|None}
    """
    settings = get_settings()
    user_keys = [uk for uk in (user_keys or []) if uk.get("api_key")]

    model_list: list[dict] = []

    def _user_key_ref(uk: dict) -> str:
        # Falls back to an object-identity-based ref for the (theoretical)
        # case of a key dict with no id, so a deployment id is still unique
        # rather than silently colliding with another anonymous key. No
        # colons anywhere here - see _deployment()'s docstring for why.
        return f"user-{uk['id']}" if uk.get("id") else f"user-anon-{id(uk)}"

    user_groq_keys = [uk for uk in user_keys if uk.get("provider") == "groq"]
    user_mistral_keys = [uk for uk in user_keys if uk.get("provider") == "mistral"]

    # Groq: the user's own key(s), if any, entirely replace the system pool
    # for this job - not merged/load-balanced alongside it - so "add your own
    # key" actually means "use my key", not "add extra shared capacity".
    if user_groq_keys:
        for uk in user_groq_keys:
            key_ref = _user_key_ref(uk)
            _add_fanout(model_list, FAST_MODEL, key_ref, uk["api_key"], GROQ_PRIMARY_MODELS)
            _add_fanout(model_list, FAST_OVERFLOW_MODEL, key_ref, uk["api_key"], GROQ_OVERFLOW_MODELS)
    else:
        for i, key in enumerate(settings.groq_keys):
            key_ref = f"system-groq-{i}"
            _add_fanout(model_list, FAST_MODEL, key_ref, key, GROQ_PRIMARY_MODELS)
            _add_fanout(model_list, FAST_OVERFLOW_MODEL, key_ref, key, GROQ_OVERFLOW_MODELS)

    # Mistral: same replace-not-merge priority as Groq above.
    if user_mistral_keys:
        for uk in user_mistral_keys:
            key_ref = _user_key_ref(uk)
            _add_fanout(
                model_list, STRUCTURED_MODEL, key_ref, uk["api_key"], MISTRAL_PRIMARY_MODELS,
                extra_litellm_params={"prompt_cache_key": MISTRAL_PROMPT_CACHE_KEY},
            )
    else:
        for i, key in enumerate(settings.mistral_keys):
            key_ref = f"system-mistral-{i}"
            _add_fanout(
                model_list, STRUCTURED_MODEL, key_ref, key, MISTRAL_PRIMARY_MODELS,
                extra_litellm_params={"prompt_cache_key": MISTRAL_PROMPT_CACHE_KEY},
            )

    for uk in user_keys:
        provider = uk.get("provider")
        if provider in ("groq", "mistral"):
            continue  # handled above, with system-pool-replacement priority
        api_key = uk["api_key"]
        key_ref = _user_key_ref(uk)

        if provider == "claude":
            model_list.append(
                {
                    "model_name": QUALITY_MODEL,
                    "litellm_params": {"model": uk.get("model_name") or "claude-3-5-sonnet-latest", "api_key": api_key},
                    "model_info": {"id": f"{key_ref}__claude"},
                }
            )
        elif provider == "openai":
            model_list.append(
                {
                    "model_name": QUALITY_MODEL,
                    "litellm_params": {"model": uk.get("model_name") or "gpt-4o-mini", "api_key": api_key},
                    "model_info": {"id": f"{key_ref}__openai"},
                }
            )
        elif provider == "custom":
            params = {"model": uk.get("model_name") or "openai/custom-model", "api_key": api_key}
            if uk.get("base_url"):
                params["api_base"] = uk["base_url"]
            model_list.append(
                {"model_name": QUALITY_MODEL, "litellm_params": params, "model_info": {"id": f"{key_ref}__custom"}}
            )

    if not model_list:
        raise RuntimeError("No LLM API keys configured (system or user).")

    # QUALITY_MODEL/FAST_OVERFLOW_MODEL only have deployments if a matching
    # key is actually configured. Unconditionally referencing an empty group
    # in `fallbacks` means the moment the primary group hits a rate limit,
    # LiteLLM's fallback chain tries to route to a model_name string with no
    # deployments and hard-crashes with `litellm.BadRequestError` instead of
    # a clean rate-limit error - actively making the fallback mechanism
    # worse than having none. Only wire in fallbacks to a group that exists.
    configured_groups = {m["model_name"] for m in model_list}
    fallbacks = []
    fast_fallbacks = [g for g in (FAST_OVERFLOW_MODEL, STRUCTURED_MODEL, QUALITY_MODEL) if g in configured_groups]
    if fast_fallbacks:
        fallbacks.append({FAST_MODEL: fast_fallbacks})
    fast_overflow_fallbacks = [g for g in (STRUCTURED_MODEL, QUALITY_MODEL) if g in configured_groups]
    if fast_overflow_fallbacks:
        fallbacks.append({FAST_OVERFLOW_MODEL: fast_overflow_fallbacks})
    structured_fallbacks = [g for g in (QUALITY_MODEL, FAST_MODEL) if g in configured_groups]
    if structured_fallbacks:
        fallbacks.append({STRUCTURED_MODEL: structured_fallbacks})

    router_kwargs: dict = dict(
        model_list=model_list,
        routing_strategy="usage-based-routing-v2",
        fallbacks=fallbacks,
        timeout=REQUEST_TIMEOUT,
        num_retries=3,
        retry_after=3,
        # A short cooldown + more tolerance for transient blips avoids a
        # cascade where a concurrency burst causes several keys to each hit
        # `allowed_fails` around the same moment, briefly leaving ZERO
        # deployments available for a full 60s ("No deployments available,
        # try again in 60 seconds" — verified as the #1 real-world failure
        # cause, well above actual 429s, once concurrency exceeds what our
        # per-key TPM budgets can sustain).
        cooldown_time=15,
        allowed_fails=6,
    )

    # Deliberately NOT wiring redis_host/redis_port here for cross-process
    # RPM/TPM sync. Empirically (see scripts/eval_pipeline.py's first run),
    # litellm's Router-level Redis usage cache hangs/fails against this
    # project's Redis (an old Windows-ported build - see usage_tracker.py's
    # HSET-compatibility note for the same root cause hitting a different
    # command), and `usage-based-routing-v2`'s TPM/RPM lookup treats a
    # failed cache read as "0 healthy deployments" rather than degrading to
    # "assume unused" - i.e. this was silently making EVERY deployment
    # look rate-limited on EVERY call, not just under real load. It's also
    # not actually needed: only one ARQ worker process is ever run (see the
    # "Zombie ARQ Worker Process" fix in job history), so a single Router
    # instance's local in-memory usage counters already see every request
    # end to end - there is no second process whose usage would otherwise
    # go untracked. If real multi-process horizontal scaling is added later,
    # this needs a genuinely compatible Redis (5+) before re-enabling.
    return Router(**router_kwargs)


def pick_groq_fallback_key(user_keys: list[dict] | None = None) -> tuple[str | None, str | None]:
    """Used by pipeline.py for the compound-beta URL-read fallback (raw Groq
    API call, not via the Router, so it can't just pick a deployment out of
    `build_router`'s model_list like every other Groq/Mistral call does).

    Same replace-not-merge priority as build_router() above: if the user has
    added their own active Groq key(s), the FIRST one is used here and the
    system pool is never touched for this user at all. Only a user with zero
    Groq keys of their own reaches the system pool - this must never silently
    default to a system key "just for this one feature" while every other
    Groq call in the same job correctly uses the user's own key; that would
    be exactly the kind of unwanted, unrequested system-key usage users
    adding their own keys are trying to avoid.

    Returns (api_key, key_ref) so usage_tracker.py can still attribute this
    raw call back to the right physical key, matching the id scheme
    `_deployment()` uses for every router-routed call.
    """
    user_groq_keys = [uk for uk in (user_keys or []) if uk.get("provider") == "groq" and uk.get("api_key")]
    if user_groq_keys:
        uk = user_groq_keys[0]
        key_ref = f"user-{uk['id']}" if uk.get("id") else f"user-anon-{id(uk)}"
        return uk["api_key"], key_ref

    keys = get_settings().groq_keys
    if not keys:
        return None, None
    return keys[0], "system-groq-0"
