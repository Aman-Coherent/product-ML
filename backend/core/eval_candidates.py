"""
Candidate (NOT wired into the production pipeline) implementations of the
Tier 2 token-optimization ideas from the usage/optimization plan:

  1. `CompanyAnalysis` / `analyze_company()` - merges classify_company +
     research_company into a single call/schema, eliminating one full
     markdown reinjection + one system-prompt/schema block per company.
  2. `generate_products_deduped()` - drops the full raw markdown from the
     generation prompt in favor of research's already-distilled fields,
     falling back to a short excerpt only when research found nothing
     concrete.

These intentionally live OUTSIDE classifier.py/generator.py/pipeline.py so
production behavior is completely unchanged until scripts/eval_pipeline.py
confirms no quality regression against the current two/three-call baseline.
Once validated, promote by:
  - swapping pipeline.py's classify_company+research_company calls for
    analyze_company (and updating CompanyResult/CompanyInput field mapping
    to read straight off CompanyAnalysis instead of two separate objects)
  - swapping generator.py's generate_products body for this deduped prompt
  - deleting this file
"""
from __future__ import annotations

from litellm import Router
from pydantic import BaseModel, Field

from backend.core.llm_router import FAST_MODEL, STRUCTURED_MODEL
from backend.core.models import CompanyProducts, SupplyChainCategory, UrlReadResult
from backend.core.structured_output import generate_structured, schema_hint


class CompanyAnalysis(BaseModel):
    """classify_company's CompanyClassification fields + research_company's
    IndustryContext fields, merged into one schema/call. `reasoning` stays
    first for the same chain-of-thought reason as the original schemas."""

    reasoning: str = Field(
        description=(
            "Step-by-step thinking: "
            "1) What does this company actually make/do (from website content if provided, "
            "otherwise from name + location)? "
            "2) What does the location tell us about their industry cluster? "
            "3) Which supply chain role(s) do they fill, and if multiple, what proportion is each? "
            "4) What specific product lines, target markets, and materials does the evidence support "
            "(for naming purposes only - pricing/descriptions are never needed)?"
        )
    )
    all_categories: list[SupplyChainCategory] = Field(
        description="Every supply chain category that applies to this company (1 or more)."
    )
    primary_category: SupplyChainCategory = Field(description="The single most dominant supply chain role.")
    is_multi: bool = Field(description="True if more than one category applies.")
    display_label: str = Field(
        description=(
            "Human-readable label. Single category: just the category name, e.g. 'PACKAGING'. "
            "Multi-category: joined with ' + ', e.g. 'PACKAGING + MACHINERY'."
        )
    )
    multi_breakdown: str = Field(
        default="",
        description="If is_multi, e.g. 'Packaging (70%), Machinery (30%)'. Empty string if not multi.",
    )
    confidence: float = Field(ge=0.0, le=1.0, description="Confidence in this classification, 0 to 1.")
    is_known_company: bool = Field(description="True if this is a real, recognizable company.")
    product_lines: list[str] = Field(default_factory=list, description="Actual or plausible product line names.")
    target_markets: list[str] = Field(default_factory=list, description="Industries/markets served.")
    materials: list[str] = Field(default_factory=list, description="Raw materials or components used, if relevant.")


ANALYSIS_SYSTEM_PROMPT = """You are a senior supply chain analyst and industry researcher. Given a \
company's website content (or name + location if no website is available), do TWO things in one pass:

1) Classify the company into its supply chain role(s) using ONLY these 4 categories:
- PACKAGING: makes packaging materials/products (boxes, films, bottles, labels, pouches, containers)
- MACHINERY: makes industrial machines/equipment used in manufacturing or processing
- FINISHED_GOODS: makes final consumer/end-use products sold to consumers or businesses
- RAW_MATERIAL: supplies raw/basic materials (metals, chemicals, textiles, resins, agricultural inputs)

These 4 category strings are the ONLY valid values anywhere in your JSON output. Never invent a new \
category name even if it feels more precise - always map the company's real business to the closest \
one (or combination) of the 4 categories above. A steelmaker, for example, is RAW_MATERIAL.

MULTI-CATEGORY RULE: A company can belong to multiple categories if it genuinely operates across them. \
Only mark multi when there is clear evidence of more than one distinct business line.

LOCATION INTELLIGENCE: When the company is not well known and no website content is available, use the \
location as a signal (e.g. Surat/India = textiles & diamond processing, Shenzhen/China = electronics \
manufacturing, Ruhr/Germany = heavy machinery & steel). Supporting signal only, not a guarantee.

2) Extract only research details that help generate accurate product NAMES later: specific product line \
names (verbatim if the website mentions them), target markets/industries served, and materials or \
components used. Only report what the given context supports. Do not report pricing, descriptions, or \
anything else that isn't a product name - it will not be used and wastes tokens.

Always fill `reasoning` before deciding anything else - think step by step.

Respond with a single JSON object ONLY (no prose, no markdown fences), matching exactly this shape:
{schema}"""


def _analysis_prompt(company_name: str, location: str | None, url_read: UrlReadResult) -> str:
    parts = [f"Company Name: {company_name}", f"Location: {location or 'Unknown'}"]
    if url_read.success and url_read.markdown:
        parts.append(
            f"\nCOMPANY WEBSITE CONTENT (source: {url_read.source.value}):\n"
            f"---\n{url_read.markdown}\n---\n"
            "Use the website content above as the primary source of truth."
        )
    else:
        parts.append(
            "\nNo website content is available for this company "
            f"(reason: {url_read.error or 'no url provided'}). "
            "Analyze using the company name and location only."
        )
    parts.append("\nThink step by step, then classify and research this company.")
    return "\n".join(parts)


async def analyze_company(
    router: Router,
    company_name: str,
    location: str | None,
    url_read: UrlReadResult,
) -> CompanyAnalysis:
    system_prompt = ANALYSIS_SYSTEM_PROMPT.format(schema=schema_hint(CompanyAnalysis))
    return await generate_structured(
        router,
        FAST_MODEL,
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": _analysis_prompt(company_name, location, url_read)},
        ],
        schema_cls=CompanyAnalysis,
        temperature=0.15,
        max_tokens=1500,
    )


GENERATION_SYSTEM_PROMPT = """You are a product catalog specialist. Generate a realistic product \
catalog for the given company, grounded strictly in the provided context (industry research and \
supply chain classification, plus a short website excerpt when available).

RULES:
- Generate between 10 and 50 products.
- Every product must plausibly belong to this company's actual business based on the context given.
- If known/likely product lines are listed, use those names and close variations first.
- Do NOT invent products unrelated to the company's classified supply chain role(s).
- Avoid repeating the same product with only minor name changes - each name should be distinct and specific.
- Each product's `category` must be exactly one of: PACKAGING, MACHINERY, FINISHED_GOODS, \
RAW_MATERIAL (use the primary category unless a product clearly belongs to a secondary \
category in a multi-category company). Never use any other value.
- Every object inside "products" must contain EXACTLY these 2 keys, spelled exactly this \
way, and no others: name, category. Do NOT add subcategory, market, material, \
description, price_range, id, or any other extra key.
- LANGUAGE: Every product `name` MUST be written in English, no matter what language the \
website content or company context is in. Translate non-English source names into the accurate, \
natural, industry-standard English term. Never output non-English words in `name`, except for \
proper nouns/brand names and standard units/abbreviations.

Respond with a single JSON object ONLY (no prose, no markdown fences), matching exactly \
this shape:
{schema}"""

# Only sent when research found nothing concrete to extract (empty
# product_lines) - i.e. exactly the case where the distilled fields alone
# don't give the generator enough to work with.
_FALLBACK_EXCERPT_CHARS = 800


def _generation_prompt_deduped(
    company_name: str,
    location: str | None,
    url_read: UrlReadResult,
    analysis: CompanyAnalysis,
) -> str:
    parts = [
        f"Company Name: {company_name}",
        f"Location: {location or 'Unknown'}",
        f"Supply Chain Classification: {analysis.display_label}",
        f"Classification reasoning: {analysis.reasoning}",
    ]
    if analysis.product_lines:
        parts.append(f"\nKnown/likely product lines: {', '.join(analysis.product_lines)}")
    if analysis.target_markets:
        parts.append(f"Target markets: {', '.join(analysis.target_markets)}")
    if analysis.materials:
        parts.append(f"Materials/components: {', '.join(analysis.materials)}")

    if not analysis.product_lines and url_read.success and url_read.markdown:
        parts.append(f"\nWEBSITE EXCERPT (research found no specific product lines - use this instead):\n---\n{url_read.markdown[:_FALLBACK_EXCERPT_CHARS]}\n---")

    parts.append("\nGenerate the product catalog now (10-50 products).")
    return "\n".join(parts)


async def generate_products_deduped(
    router: Router,
    company_name: str,
    location: str | None,
    url_read: UrlReadResult,
    analysis: CompanyAnalysis,
) -> CompanyProducts:
    system_prompt = GENERATION_SYSTEM_PROMPT.format(schema=schema_hint(CompanyProducts))
    return await generate_structured(
        router,
        STRUCTURED_MODEL,
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": _generation_prompt_deduped(company_name, location, url_read, analysis)},
        ],
        schema_cls=CompanyProducts,
        temperature=0.5,
        max_tokens=2500,
        # NOTE: no `extra_params` prompt_cache_key here - it's baked into
        # each Mistral deployment's litellm_params in llm_router.py instead,
        # so it never leaks onto a Groq fallback call. See that module's
        # MISTRAL_PROMPT_CACHE_KEY / _deployment() docstring for why a
        # call-level kwarg here previously broke Mistral-down fallbacks.
    )
