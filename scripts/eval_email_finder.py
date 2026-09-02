"""
Standalone validation harness for backend/core/email_finder/ - runs the
real pipeline (real Jina fetches, real DNS/SMTP checks, real Groq search)
against a fixed sample of companies WITHOUT touching the app's DB, Redis
job state, or the frontend. Use this to sanity-check extraction accuracy
before trusting the feature on a real CSV.

Sample deliberately covers every path through pipeline.py:
  - URL provided, real company -> should hit SCRAPED_VERIFIED
  - No URL, well-known company -> exercises the Groq web-search step
  - No URL, obscure/fictional name -> exercises domain-guess + pattern
    fallback (and should gracefully end in "nothing found" for the
    truly-fake one, not a hallucinated address)

Usage (from repo root):
    python scripts/eval_email_finder.py
"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from backend.core.email_finder.pipeline import find_company_email  # noqa: E402
from backend.core.llm_router import pick_all_groq_keys  # noqa: E402

SAMPLE_COMPANIES = [
    {"name": "Tetra Pak", "location": "Lund, Sweden", "url": "https://www.tetrapak.com"},
    {"name": "Caterpillar Inc", "location": "Irving, Texas, USA", "url": "https://www.caterpillar.com"},
    {"name": "SKF", "location": "Gothenburg, Sweden", "url": None},  # forces web-search path
    {"name": "Amcor", "location": "Zurich, Switzerland", "url": None},  # forces web-search path
    {"name": "Local Packaging Solutions Pvt Ltd", "location": "Mumbai, India", "url": None},  # likely domain-guess/pattern path
    {"name": "Totally Fictional Nonexistent Widgets Co", "location": "Nowhere", "url": None},  # should end empty, not hallucinated
]


async def main() -> None:
    groq_keys = pick_all_groq_keys(user_keys=[])

    print(f"{'Company':<42} {'Website source':<14} {'Resolved URL':<32} {'Primary email':<32} {'Tier':<24} {'Conf':<5} {'Pages':<6} {'ms':<6}")
    print("-" * 165)

    for i, company in enumerate(SAMPLE_COMPANIES):
        result = await find_company_email(
            company_id=f"eval-{i}",
            company_name=company["name"],
            location=company["location"],
            url=company["url"],
            redis=None,
            groq_api_keys=groq_keys,
        )
        print(
            f"{company['name']:<42} "
            f"{result.website_source.value:<14} "
            f"{(result.resolved_url or '-'):<32} "
            f"{(result.primary_email or '-'):<32} "
            f"{(result.primary_tier.value if result.primary_tier else '-'):<24} "
            f"{result.primary_confidence:<5.2f} "
            f"{len(result.pages_checked):<6} "
            f"{result.processing_time_ms:<6}"
        )
        if result.alternate_emails:
            print(f"   alternates: {[c.email + ' (' + c.tier.value + ')' for c in result.alternate_emails]}")
        if result.error:
            print(f"   note: {result.error}")

    print("\nCheck each row against reality manually:")
    print("  - SCRAPED_VERIFIED rows: open resolved_url yourself and confirm the email is really there.")
    print("  - PATTERN_* rows: these are guesses, not confirmed site content - judge confidence accordingly.")
    print("  - The fictional company MUST end with primary_email = '-' (no website found, nothing hallucinated).")


if __name__ == "__main__":
    asyncio.run(main())
