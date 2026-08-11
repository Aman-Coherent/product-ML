"""
Mistral-based structured product generator. Produces 10-50 realistic
products for a company, grounded in website content (when available) and
the research/classification context, with a Pydantic-enforced schema so
output is always valid, well-typed JSON.

Product names must always be in English. The primary defense is prompt
instruction (free — no extra latency/cost). As a safety net, because website
content is frequently non-English and the model sometimes copies a source
product name verbatim despite instructions, a post-generation pass scans for
non-ASCII names and — only when at least one is found — issues a single
batched translation call to fix just those. The common case (already English)
never pays for the extra round trip.
"""
from __future__ import annotations

import logging
import re

from litellm import Router
from pydantic import BaseModel, Field

from backend.core.llm_router import FAST_MODEL, STRUCTURED_MODEL
from backend.core.models import (
    CompanyClassification,
    CompanyProducts,
    IndustryContext,
    Product,
    UrlReadResult,
)
from backend.core.structured_output import generate_structured, schema_hint

logger = logging.getLogger("generator")

SYSTEM_PROMPT = """You are a product catalog specialist. Generate a realistic product \
catalog for the given company, grounded strictly in the provided context (website \
content, industry research, and supply chain classification). 

RULES:
- Generate between 10 and 50 products.
- Every product must plausibly belong to this company's actual business based on the context given.
- If website content lists specific products, use those names and close variations first.
- Do NOT invent products unrelated to the company's classified supply chain role(s).
- Avoid repeating the same product with only minor name changes - each name should be distinct and specific.
- Each product's `category` must be exactly one of: PACKAGING, MACHINERY, FINISHED_GOODS, \
RAW_MATERIAL (use the primary category unless a product clearly belongs to a secondary \
category in a multi-category company). Never use any other value.
- Every object inside "products" must contain EXACTLY these 2 keys, spelled exactly this \
way, and no others: name, category. Do NOT add subcategory, market, material, \
description, price_range, id, or any other extra key.
- LANGUAGE: Every product `name` MUST be written in English, no matter what language the \
website content or company context is in. If the source material only gives you a \
non-English name (e.g. French "Bidon en polypropylène"), translate it into the accurate, \
natural, industry-standard English term (e.g. "Polypropylene Container") instead of \
copying the original-language text. Never output non-English words in `name`, except for \
proper nouns/brand names and standard units/abbreviations.

Respond with a single JSON object ONLY (no prose, no markdown fences), matching exactly \
this shape:
{schema}"""


def _generation_prompt(
    company_name: str,
    location: str | None,
    url_read: UrlReadResult,
    classification: CompanyClassification,
    research: IndustryContext,
) -> str:
    parts = [
        f"Company Name: {company_name}",
        f"Location: {location or 'Unknown'}",
        f"Supply Chain Classification: {classification.display_label}",
        f"Classification reasoning: {classification.reasoning}",
    ]
    if url_read.success and url_read.markdown:
        parts.append(f"\nWEBSITE CONTENT:\n---\n{url_read.markdown}\n---")

    if research.product_lines:
        parts.append(f"\nKnown/likely product lines: {', '.join(research.product_lines)}")
    if research.target_markets:
        parts.append(f"Target markets: {', '.join(research.target_markets)}")
    if research.materials:
        parts.append(f"Materials/components: {', '.join(research.materials)}")

    parts.append("\nGenerate the product catalog now (10-50 products).")
    return "\n".join(parts)


# English text is ASCII in the overwhelming majority of real product names
# (occasional accented brand names are a rare, acceptable false-positive —
# re-translating a name that's already fine just returns it unchanged).
_NON_ASCII_RE = re.compile(r"[^\x00-\x7F]")

_TRANSLATE_SYSTEM_PROMPT = """You are a technical/industrial translator. Translate each \
numbered product name into clear, natural, industry-standard English. Preserve technical \
meaning exactly - these are real manufactured product names, not marketing copy. Keep \
brand names, model numbers, and units unchanged. Return exactly one translation per input \
line, in the same order, with nothing else.

Respond with a single JSON object ONLY (no prose, no markdown fences), matching exactly \
this shape:
{schema}"""


class _TranslationBatch(BaseModel):
    translations: list[str] = Field(
        description="English translation for each input product name, one-to-one, in the exact same order as given."
    )


async def _translate_non_english_names(router: Router, products: list[Product]) -> list[Product]:
    """Safety net for the (rare) case where the model copies a source-language
    product name verbatim despite the system prompt's English-only rule.
    Cheap and only runs when needed: one Groq call translating just the
    flagged names, never the whole catalog."""
    flagged = [(i, p.name) for i, p in enumerate(products) if _NON_ASCII_RE.search(p.name)]
    if not flagged:
        return products

    try:
        batch = await generate_structured(
            router,
            FAST_MODEL,
            messages=[
                {
                    "role": "system",
                    "content": _TRANSLATE_SYSTEM_PROMPT.format(schema=schema_hint(_TranslationBatch)),
                },
                {
                    "role": "user",
                    "content": "\n".join(f"{n}. {name}" for n, (_, name) in enumerate(flagged, start=1)),
                },
            ],
            schema_cls=_TranslationBatch,
            temperature=0.0,
            max_tokens=800,
        )
        if len(batch.translations) != len(flagged):
            logger.warning(
                "Translation batch size mismatch (%d flagged, %d returned) - keeping originals",
                len(flagged),
                len(batch.translations),
            )
            return products
        for (idx, original_name), translated in zip(flagged, batch.translations):
            translated = translated.strip()
            if translated:
                products[idx] = Product(name=translated, category=products[idx].category)
            else:
                logger.warning("Empty translation for %r - keeping original", original_name)
    except Exception:
        logger.warning("Non-English product name translation fallback failed - keeping originals", exc_info=True)
    return products


def _dedupe_products(products: list[Product]) -> list[Product]:
    seen: set[str] = set()
    unique: list[Product] = []
    for p in products:
        key = p.name.strip().lower()
        if key not in seen:
            seen.add(key)
            unique.append(p)
    return unique


async def generate_products(
    router: Router,
    company_name: str,
    location: str | None,
    url_read: UrlReadResult,
    classification: CompanyClassification,
    research: IndustryContext,
) -> CompanyProducts:
    system_prompt = SYSTEM_PROMPT.format(schema=schema_hint(CompanyProducts))
    result = await generate_structured(
        router,
        STRUCTURED_MODEL,
        messages=[
            {"role": "system", "content": system_prompt},
            {
                "role": "user",
                "content": _generation_prompt(company_name, location, url_read, classification, research),
            },
        ],
        schema_cls=CompanyProducts,
        temperature=0.5,
        # Each product is now just {name, category} — 50 short objects
        # comfortably fits well under 2K completion tokens.
        max_tokens=2500,
        # NOTE: Mistral's prompt_cache_key is intentionally NOT passed here.
        # It's baked into each Mistral deployment's own litellm_params in
        # llm_router.py instead - see MISTRAL_PROMPT_CACHE_KEY and
        # _deployment()'s docstring for why a generic call-level kwarg here
        # previously broke the Groq fallback whenever Mistral was down.
    )

    translated = await _translate_non_english_names(router, list(result.products))
    # Translation can occasionally collapse two source-language names into the
    # same English term - dedupe again, but skip Pydantic's min_length=10
    # revalidation (model_construct) since that constraint was already
    # satisfied pre-translation and shouldn't retroactively fail the company.
    deduped = _dedupe_products(translated)
    return CompanyProducts.model_construct(reasoning=result.reasoning, products=deduped)
