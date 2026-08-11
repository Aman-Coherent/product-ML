"""
Per-company pipeline: url_read -> classify -> [research -> generate].

The `mode` controls which steps run:
  - "classification": url_read -> classify
  - "generation":      url_read -> classify -> research -> generate
  - "both":            same as generation (classification is always needed
                        to tag products with the right supply chain category)
"""
from __future__ import annotations

import time

from litellm import Router
from redis.asyncio import Redis

from backend.core.classifier import classify_company, research_company
from backend.core.generator import generate_products
from backend.core.llm_router import first_groq_key, first_groq_key_ref
from backend.core.models import CompanyResult, UrlReadResult
from backend.core.url_reader import read_url_for_llm


async def process_company(
    router: Router,
    redis: Redis | None,
    company_id: str,
    company_name: str,
    location: str | None,
    url: str | None,
    mode: str,
) -> CompanyResult:
    start = time.monotonic()
    try:
        url_read: UrlReadResult = await read_url_for_llm(
            url, redis=redis, groq_fallback_key=first_groq_key(), groq_fallback_key_ref=first_groq_key_ref()
        )

        classification = await classify_company(router, company_name, location, url_read)

        research = None
        products = None
        if mode in ("generation", "both"):
            research = await research_company(router, company_name, location, url_read, classification)
            products = await generate_products(
                router, company_name, location, url_read, classification, research
            )

        elapsed_ms = int((time.monotonic() - start) * 1000)
        return CompanyResult(
            company_id=company_id,
            company_name=company_name,
            location=location,
            url=url,
            url_read=url_read,
            classification=classification,
            research=research,
            products=products,
            success=True,
            processing_time_ms=elapsed_ms,
        )
    except Exception as exc:  # noqa: BLE001 - must never crash the whole batch
        elapsed_ms = int((time.monotonic() - start) * 1000)
        return CompanyResult(
            company_id=company_id,
            company_name=company_name,
            location=location,
            url=url,
            url_read=UrlReadResult(),
            success=False,
            error=str(exc),
            processing_time_ms=elapsed_ms,
        )
