"""
Provider-agnostic structured output helper.

We deliberately do NOT rely on each provider's proprietary strict-schema /
tool-calling enforcement (`response_format={"type": "json_schema", ...}`).
In practice this varies wildly in reliability:

  - Groq's smaller open models (e.g. `openai/gpt-oss-20b`) support a
    "Structured Output" mode with server-side schema validation, but the
    model itself does not always respect enum constraints (e.g. inventing
    a category not in the allowed list) — Groq then hard-rejects the whole
    response with a 400 `tool_use_failed` error instead of degrading
    gracefully, which would otherwise fail every single company in a job.
  - Other providers (Mistral, OpenAI, Claude, custom OpenAI-compatible
    endpoints) support `json_schema` with varying levels of strictness.

Instead we use the universally-supported `json_object` mode (guarantees
syntactically valid JSON on every major provider) combined with:
  1. An explicit, compact schema description embedded directly in the
     prompt (including literal enum values), so the model always sees
     exactly what's expected regardless of provider.
  2. Pydantic validation with a bounded self-correction retry loop: on a
     JSON parse or schema validation failure, the invalid output and the
     validation error are fed back to the model and it is asked to return
     a corrected object.

This trades a small amount of prompt-engineering effort for consistent
behavior across every provider a user might configure.
"""
from __future__ import annotations

import json
import logging
from typing import TypeVar

from litellm import Router
from pydantic import BaseModel, ValidationError

from backend.core.model_catalog import provider_for_tag
from backend.core.usage_tracker import record_usage

logger = logging.getLogger("structured_output")

T = TypeVar("T", bound=BaseModel)


async def _capture_usage(router: Router, response) -> None:
    """Best-effort - never allowed to affect the actual generation result.
    `router._usage_redis` is a dynamic attribute job_engine.py attaches
    after building the router (see its docstring for why it isn't threaded
    through as a real parameter)."""
    redis = getattr(router, "_usage_redis", None)
    if redis is None:
        return
    try:
        hidden = getattr(response, "_hidden_params", None) or {}
        model_id = hidden.get("model_id")
        if not model_id:
            return
        tag = model_id.split("__", 1)[1] if "__" in model_id else model_id
        provider = provider_for_tag(tag) or "unknown"

        usage = getattr(response, "usage", None)
        prompt_tokens = getattr(usage, "prompt_tokens", 0) or 0
        completion_tokens = getattr(usage, "completion_tokens", 0) or 0
        cached_tokens = 0
        details = getattr(usage, "prompt_tokens_details", None)
        if details is not None:
            cached_tokens = getattr(details, "cached_tokens", 0) or 0

        headers = hidden.get("headers") or getattr(response, "_response_headers", None) or {}

        await record_usage(redis, model_id, provider, prompt_tokens, completion_tokens, cached_tokens, headers)
    except Exception:
        logger.debug("Usage capture failed", exc_info=True)


def schema_hint(model_cls: type[BaseModel]) -> str:
    """Compact JSON-shape description (with literal enum values AND field
    descriptions spelled out) to embed directly in a prompt, so models that
    don't natively enforce a JSON schema still know the exact expected
    structure and semantics of every field — including nested objects, e.g.
    a `list[Product]` field must show each `Product`'s own sub-fields, not
    just collapse to the word "object". Dropping field descriptions (keeping
    only bare types) causes free-text fields like a "human-readable label"
    to be filled with whatever the model feels like instead of what was
    actually asked for — the description is often the only thing that
    disambiguates intent for non-enum string/number fields."""
    schema = model_cls.model_json_schema()
    defs = schema.get("$defs", {})

    def object_repr(prop: dict, indent: str) -> str:
        properties = prop.get("properties", {})
        if not properties:
            return "object"
        inner_indent = indent + "  "
        lines = []
        for name, sub_prop in properties.items():
            description = sub_prop.get("description")
            line = f'{inner_indent}"{name}": {type_repr(sub_prop, inner_indent)}'
            if description:
                line += f"  // {description}"
            lines.append(line)
        return "{\n" + ",\n".join(lines) + f"\n{indent}}}"

    def type_repr(prop: dict, indent: str = "") -> str:
        if "$ref" in prop:
            ref_name = prop["$ref"].split("/")[-1]
            return type_repr(defs.get(ref_name, {}), indent)
        if "anyOf" in prop:
            return " or ".join(type_repr(p, indent) for p in prop["anyOf"])
        if "enum" in prop:
            return " | ".join(f'"{v}"' for v in prop["enum"])
        if prop.get("type") == "array":
            return f"[{type_repr(prop.get('items', {}), indent)}, ...]"
        if prop.get("type") == "object":
            return object_repr(prop, indent)
        if prop.get("type") == "null":
            return "null"
        return prop.get("type", "any")

    return object_repr(schema, "")


async def generate_structured(
    router: Router,
    model: str,
    messages: list[dict],
    schema_cls: type[T],
    temperature: float = 0.2,
    max_tokens: int = 1500,
    max_retries: int = 2,
    extra_params: dict | None = None,
) -> T:
    """Calls the LLM in `json_object` mode and validates the result against
    `schema_cls`, retrying with a corrective follow-up message up to
    `max_retries` times if the output is malformed or fails validation.
    Never silently falls back to a different model — retries happen on the
    exact same `model` alias so callers keep full control of routing.

    `extra_params` is forwarded as-is to `router.acompletion` as a call-level
    kwarg, which means it applies to WHICHEVER deployment litellm ends up
    using - including a `fallbacks` hop to a completely different provider,
    since litellm never strips params a provider doesn't understand. Do NOT
    put provider-specific params here for any `model` alias that has
    `fallbacks` configured to a different provider (reproduced live:
    Mistral's `prompt_cache_key` passed this way broke the Groq fallback
    outright whenever Mistral errored, turning a clean provider failover
    into a total outage). For provider-specific params, bake them into that
    provider's own deployments' `litellm_params` in llm_router.py instead
    (see MISTRAL_PROMPT_CACHE_KEY there for the pattern) - this parameter
    should only be used for params that are safe on every deployment in the
    `model` alias's full fallback chain."""
    working_messages = list(messages)
    last_error: Exception | None = None
    last_raw: str | None = None

    for attempt in range(max_retries + 1):
        try:
            response = await router.acompletion(
                model=model,
                messages=working_messages,
                response_format={"type": "json_object"},
                temperature=temperature,
                max_tokens=max_tokens,
                **(extra_params or {}),
            )
            await _capture_usage(router, response)
            last_raw = response.choices[0].message.content
            if not last_raw or not last_raw.strip():
                raise ValueError("Model returned an empty response")
            data = json.loads(last_raw)
            return schema_cls.model_validate(data)
        except (json.JSONDecodeError, ValidationError, ValueError) as exc:
            last_error = exc
            logger.warning(
                "Structured output attempt %d/%d failed for model=%s: %s",
                attempt + 1,
                max_retries + 1,
                model,
                exc,
            )
            if attempt < max_retries:
                # Mistral's API hard-rejects an assistant turn with empty-string
                # content ("must have either content or tool_calls, but not
                # none") - which is exactly what an empty/whitespace model
                # response produces here, permanently failing the whole
                # correction loop (and any fallback model sharing this same
                # message history) on every empty-response retry instead of
                # just this one attempt.
                working_messages = working_messages + [
                    {"role": "assistant", "content": last_raw.strip() if last_raw and last_raw.strip() else "(empty response)"},
                    {
                        "role": "user",
                        "content": (
                            f"That response was invalid: {exc}. Return ONLY the corrected JSON "
                            "object matching the required schema exactly — no prose, no markdown "
                            "code fences, no extra keys, and use only the allowed enum values."
                        ),
                    },
                ]

    raise RuntimeError(f"Structured output failed after {max_retries + 1} attempt(s): {last_error}")
