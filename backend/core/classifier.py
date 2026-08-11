"""
Groq-based multi-label supply chain classifier. Categorizes a company into
PACKAGING / MACHINERY / FINISHED_GOODS / RAW_MATERIAL (or a combination),
using website markdown as primary context when available, with 0-shot
chain-of-thought prompting (reasoning field ordered first in the schema).
"""
from __future__ import annotations

from litellm import Router

from backend.core.llm_router import FAST_MODEL
from backend.core.models import CompanyClassification, IndustryContext, UrlReadResult
from backend.core.structured_output import generate_structured, schema_hint

SYSTEM_PROMPT = """You are a senior supply chain analyst. Your job is to classify a \
company into its supply chain role(s) using ONLY these 4 categories:

- PACKAGING: makes packaging materials/products (boxes, films, bottles, labels, pouches, containers)
- MACHINERY: makes industrial machines/equipment used in manufacturing or processing
- FINISHED_GOODS: makes final consumer/end-use products sold to consumers or businesses
- RAW_MATERIAL: supplies raw/basic materials (metals, chemicals, textiles, resins, agricultural inputs)

These 4 category strings are the ONLY valid values anywhere in your JSON output \
(for both `primary_category` and every entry in `all_categories`). Never invent a \
new category name (e.g. "STEEL", "TEXTILES") even if it feels more precise — always \
map the company's real business to the closest one (or combination) of the 4 \
categories above. A steelmaker, for example, is RAW_MATERIAL.

MULTI-CATEGORY RULE: A company can belong to multiple categories if it genuinely \
operates across them (e.g. a company that both mines raw material AND manufactures \
finished goods from it). Only mark multi when there is clear evidence of more than \
one distinct business line - do not default to multi.

LOCATION INTELLIGENCE: When the company is not well known and no website content is \
available, use the location as a signal. Certain regions have concentrated industry \
clusters (e.g. Surat/India = textiles & diamond processing, Shenzhen/China = \
electronics manufacturing, Ruhr/Germany = heavy machinery & steel). Use this only as \
a supporting signal, not a guarantee.

Always fill `reasoning` before deciding the categories - think step by step.

Respond with a single JSON object ONLY (no prose, no markdown fences), matching \
exactly this shape:
{schema}"""


def _user_prompt(company_name: str, location: str | None, url_read: UrlReadResult) -> str:
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
            "Classify using the company name and location only."
        )

    parts.append("\nThink step by step, then classify this company.")
    return "\n".join(parts)


async def classify_company(
    router: Router,
    company_name: str,
    location: str | None,
    url_read: UrlReadResult,
) -> CompanyClassification:
    system_prompt = SYSTEM_PROMPT.format(schema=schema_hint(CompanyClassification))
    classification = await generate_structured(
        router,
        FAST_MODEL,
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": _user_prompt(company_name, location, url_read)},
        ],
        schema_cls=CompanyClassification,
        temperature=0.1,
        max_tokens=1200,
    )
    classification.url_was_used = url_read.success
    return classification


RESEARCH_SYSTEM_PROMPT = """You are an industry research analyst. Given a company's \
website content (or name + location if no website is available) and its supply chain \
classification, extract concrete details that will be used to generate a realistic \
product catalog for this company. Only report what is supported by the given \
context - if the website mentions specific product line names, use them verbatim.

Respond with a single JSON object ONLY (no prose, no markdown fences), matching \
exactly this shape:
{schema}"""


def _research_prompt(
    company_name: str, location: str | None, url_read: UrlReadResult, classification: CompanyClassification
) -> str:
    parts = [
        f"Company Name: {company_name}",
        f"Location: {location or 'Unknown'}",
        f"Supply Chain Classification: {classification.display_label}",
    ]
    if url_read.success and url_read.markdown:
        parts.append(f"\nWEBSITE CONTENT:\n---\n{url_read.markdown}\n---")
    else:
        parts.append("\nNo website content available - infer from name, location, and industry classification.")
    parts.append(
        "\nExtract only what helps generate accurate product NAMES later: specific "
        "product line names, target markets/industries served, and materials or "
        "components used. Do not report pricing, descriptions, or anything else - "
        "it will not be used."
    )
    return "\n".join(parts)


async def research_company(
    router: Router,
    company_name: str,
    location: str | None,
    url_read: UrlReadResult,
    classification: CompanyClassification,
) -> IndustryContext:
    system_prompt = RESEARCH_SYSTEM_PROMPT.format(schema=schema_hint(IndustryContext))
    return await generate_structured(
        router,
        FAST_MODEL,
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": _research_prompt(company_name, location, url_read, classification)},
        ],
        schema_cls=IndustryContext,
        temperature=0.3,
        max_tokens=1000,
    )
