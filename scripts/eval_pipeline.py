"""
Before/after comparison harness for the Tier 2 token-optimization
candidates in backend/core/eval_candidates.py.

Runs BOTH the current production pipeline (classify_company +
research_company + generate_products) and the candidate merged/deduped
pipeline (analyze_company + generate_products_deduped) over the same fixed
sample of companies, sharing one url_read result per company so the
comparison isolates just the prompt/schema restructuring, not URL-fetch
variance.

Makes real LLM calls against your configured system Groq/Mistral keys (see
`env`) - it does NOT touch the app's database, Redis job state, or usage
counters (token usage is measured locally via a router.acompletion wrapper,
not the production usage_tracker).

Usage (from repo root):
    python scripts/eval_pipeline.py

A material regression in classification agreement or product-name overlap,
or no net token savings, means don't promote the candidates to production -
see eval_candidates.py's module docstring for what "promote" means.
"""
from __future__ import annotations

import asyncio
import sys
from dataclasses import dataclass, field
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from backend.core.classifier import classify_company, research_company  # noqa: E402
from backend.core.eval_candidates import analyze_company, generate_products_deduped  # noqa: E402
from backend.core.generator import generate_products  # noqa: E402
from backend.core.llm_router import build_router, first_groq_key, first_groq_key_ref  # noqa: E402
from backend.core.url_reader import read_url_for_llm  # noqa: E402

# Deliberately diverse fixed sample: every supply chain category, with and
# without URLs, well-known vs obscure names, and one dead-domain case to
# exercise the no-website-content path for both variants identically.
SAMPLE_COMPANIES = [
    {"name": "Tetra Pak", "location": "Lund, Sweden", "url": "https://www.tetrapak.com"},
    {"name": "Caterpillar Inc", "location": "Irving, Texas, USA", "url": "https://www.caterpillar.com"},
    {"name": "Nestle", "location": "Vevey, Switzerland", "url": "https://www.nestle.com"},
    {"name": "ArcelorMittal", "location": "Luxembourg City, Luxembourg", "url": "https://corporate.arcelormittal.com"},
    {"name": "Amcor", "location": "Zurich, Switzerland", "url": "https://www.amcor.com"},
    {"name": "Surat Silk Mills", "location": "Surat, Gujarat, India", "url": None},
    {"name": "Shenzhen Precision Electronics Co", "location": "Shenzhen, China", "url": None},
    {"name": "Ruhr Werkzeugmaschinen GmbH", "location": "Essen, Germany", "url": None},
    {"name": "SKF", "location": "Gothenburg, Sweden", "url": "https://www.skf.com"},
    {"name": "Local Packaging Solutions", "location": "Mumbai, India", "url": "https://this-domain-does-not-exist-abc123.example"},
]


@dataclass
class VariantResult:
    total_tokens: int = 0
    requests: int = 0
    primary_category: str | None = None
    all_categories: list[str] = field(default_factory=list)
    product_names: set[str] = field(default_factory=set)
    error: str | None = None


def _norm(name: str) -> str:
    return name.strip().lower()


def _jaccard(a: set[str], b: set[str]) -> float:
    union = a | b
    if not union:
        return 1.0
    return len(a & b) / len(union)


class _TokenCounter:
    def __init__(self):
        self.total_tokens = 0
        self.requests = 0

    def wrap(self, router):
        original = router.acompletion

        async def counting_acompletion(*args, **kwargs):
            response = await original(*args, **kwargs)
            usage = getattr(response, "usage", None)
            if usage is not None:
                self.total_tokens += (usage.prompt_tokens or 0) + (usage.completion_tokens or 0)
            self.requests += 1
            return response

        router.acompletion = counting_acompletion


async def _run_baseline(router, name: str, location: str | None, url_read) -> VariantResult:
    counter = _TokenCounter()
    counter.wrap(router)
    result = VariantResult()
    try:
        classification = await classify_company(router, name, location, url_read)
        research = await research_company(router, name, location, url_read, classification)
        products = await generate_products(router, name, location, url_read, classification, research)
        result.primary_category = classification.primary_category.value
        result.all_categories = [c.value for c in classification.all_categories]
        result.product_names = {_norm(p.name) for p in products.products}
    except Exception as exc:  # noqa: BLE001
        result.error = str(exc)
    result.total_tokens = counter.total_tokens
    result.requests = counter.requests
    return result


async def _run_candidate(router, name: str, location: str | None, url_read) -> VariantResult:
    counter = _TokenCounter()
    counter.wrap(router)
    result = VariantResult()
    try:
        analysis = await analyze_company(router, name, location, url_read)
        products = await generate_products_deduped(router, name, location, url_read, analysis)
        result.primary_category = analysis.primary_category.value
        result.all_categories = [c.value for c in analysis.all_categories]
        result.product_names = {_norm(p.name) for p in products.products}
    except Exception as exc:  # noqa: BLE001
        result.error = str(exc)
    result.total_tokens = counter.total_tokens
    result.requests = counter.requests
    return result


async def main() -> None:
    router = build_router(user_keys=[])
    groq_key = first_groq_key()
    groq_key_ref = first_groq_key_ref()

    rows = []
    for company in SAMPLE_COMPANIES:
        url_read = await read_url_for_llm(company["url"], redis=None, groq_fallback_key=groq_key, groq_fallback_key_ref=groq_key_ref)

        # Fresh router.acompletion wrapper per phase so token counters don't
        # bleed between baseline/candidate (each _run_* re-wraps from
        # scratch off the router's ORIGINAL acompletion captured at
        # build_router() time - re-wrapping an already-wrapped bound method
        # would double-count, so rebuild the router per company instead).
        router = build_router(user_keys=[])

        baseline = await _run_baseline(router, company["name"], company["location"], url_read)

        router = build_router(user_keys=[])
        candidate = await _run_candidate(router, company["name"], company["location"], url_read)

        category_match = baseline.primary_category == candidate.primary_category
        name_overlap = _jaccard(baseline.product_names, candidate.product_names)
        token_delta = candidate.total_tokens - baseline.total_tokens
        token_delta_pct = (token_delta / baseline.total_tokens * 100) if baseline.total_tokens else 0.0

        rows.append(
            {
                "company": company["name"],
                "url_source": url_read.source.value,
                "category_match": category_match,
                "baseline_category": baseline.primary_category,
                "candidate_category": candidate.primary_category,
                "name_overlap": name_overlap,
                "baseline_products": len(baseline.product_names),
                "candidate_products": len(candidate.product_names),
                "baseline_tokens": baseline.total_tokens,
                "candidate_tokens": candidate.total_tokens,
                "token_delta_pct": token_delta_pct,
                "baseline_error": baseline.error,
                "candidate_error": candidate.error,
            }
        )

    print(f"\n{'Company':<32} {'URL src':<14} {'Cat match':<10} {'Name overlap':<13} {'Products (B/C)':<15} {'Tokens (B/C)':<18} {'Token delta':<12}")
    print("-" * 120)
    for row in rows:
        print(
            f"{row['company']:<32} {row['url_source']:<14} "
            f"{'YES' if row['category_match'] else 'NO ' + str(row['baseline_category']) + '->' + str(row['candidate_category']):<10} "
            f"{row['name_overlap']:.0%}{'':<9} "
            f"{row['baseline_products']}/{row['candidate_products']:<12} "
            f"{row['baseline_tokens']}/{row['candidate_tokens']:<14} "
            f"{row['token_delta_pct']:+.1f}%"
        )
        if row["baseline_error"] or row["candidate_error"]:
            print(f"   ! baseline_error={row['baseline_error']!r} candidate_error={row['candidate_error']!r}")

    n = len(rows)
    matches = sum(1 for r in rows if r["category_match"])
    avg_overlap = sum(r["name_overlap"] for r in rows) / n
    total_baseline_tokens = sum(r["baseline_tokens"] for r in rows)
    total_candidate_tokens = sum(r["candidate_tokens"] for r in rows)
    overall_delta_pct = (total_candidate_tokens - total_baseline_tokens) / total_baseline_tokens * 100 if total_baseline_tokens else 0.0

    print("-" * 120)
    print(f"Category agreement: {matches}/{n} ({matches / n:.0%})")
    print(f"Average product-name overlap (Jaccard): {avg_overlap:.0%}")
    print(f"Total tokens - baseline: {total_baseline_tokens}, candidate: {total_candidate_tokens} ({overall_delta_pct:+.1f}%)")
    print(
        "\nRule of thumb: only promote the candidates if category agreement is >= 90%, "
        "average name overlap is >= 60%, and candidate tokens are meaningfully lower."
    )


if __name__ == "__main__":
    asyncio.run(main())
