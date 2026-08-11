"""
All Pydantic schemas shared across the pipeline: URL reading results,
supply-chain classification (reasoning-FIRST for chain-of-thought
accuracy), industry research, and structured product generation.
"""
from __future__ import annotations

from enum import Enum

from pydantic import BaseModel, Field, field_validator


# ─────────────────────────── URL reading ───────────────────────────

class UrlReadSource(str, Enum):
    JINA_READER = "jina_reader"
    COMPOUND_BETA = "compound_beta"
    NAME_LOCATION = "name_location"
    NONE = "none"


class UrlReadResult(BaseModel):
    markdown: str = ""
    source: UrlReadSource = UrlReadSource.NONE
    success: bool = False
    token_estimate: int = 0
    error: str | None = None
    from_cache: bool = False


# ─────────────────────── Supply chain classification ───────────────────────

class SupplyChainCategory(str, Enum):
    PACKAGING = "PACKAGING"
    MACHINERY = "MACHINERY"
    FINISHED_GOODS = "FINISHED_GOODS"
    RAW_MATERIAL = "RAW_MATERIAL"


class CompanyClassification(BaseModel):
    """
    Field order matters: `reasoning` MUST come first so the LLM commits to
    its chain-of-thought analysis before locking in an answer. Reordering
    this schema measurably degrades classification accuracy.
    """

    reasoning: str = Field(
        description=(
            "Step-by-step thinking: "
            "1) What does this company actually make/do (from website content if provided, "
            "otherwise from name + location)? "
            "2) What does the location tell us about their industry cluster? "
            "3) Which supply chain role(s) do they fill? "
            "4) If multiple roles apply, what proportion of the business is each role?"
        )
    )
    all_categories: list[SupplyChainCategory] = Field(
        description="Every supply chain category that applies to this company (1 or more)."
    )
    primary_category: SupplyChainCategory = Field(
        description="The single most dominant supply chain role."
    )
    is_multi: bool = Field(description="True if more than one category applies.")
    display_label: str = Field(
        description=(
            "Human-readable label. Single category: just the category name, e.g. 'PACKAGING'. "
            "Multi-category: joined with ' + ', e.g. 'PACKAGING + MACHINERY'."
        )
    )
    multi_breakdown: str = Field(
        default="",
        description=(
            "If is_multi, a short breakdown of each category's approximate share, "
            "e.g. 'Packaging (70%), Machinery (30%)'. Empty string if not multi."
        ),
    )
    confidence: float = Field(ge=0.0, le=1.0, description="Confidence in this classification, 0 to 1.")
    is_known_company: bool = Field(description="True if this is a real, recognizable company.")
    url_was_used: bool = Field(default=False, description="True if website content was used as context.")


# ─────────────────────────── Industry research ───────────────────────────

class IndustryContext(BaseModel):
    """Every field here exists only to help generate() write better product
    NAMES - anything that doesn't move the needle on naming (e.g. pricing
    tier) is deliberately excluded so no tokens are spent producing or
    re-injecting it. See generator.py's SYSTEM_PROMPT for the matching
    output-side rule (products may only ever contain name + category)."""

    reasoning: str = Field(description="Brief note on where this information came from (website vs inference).")
    product_lines: list[str] = Field(default_factory=list, description="Actual or plausible product line names.")
    target_markets: list[str] = Field(default_factory=list, description="Industries/markets served.")
    materials: list[str] = Field(default_factory=list, description="Raw materials or components used, if relevant.")


# ─────────────────────────── Product generation ───────────────────────────

class Product(BaseModel):
    name: str = Field(description="Specific, realistic product name for this company.")
    category: SupplyChainCategory = Field(
        description="Which of the company's supply chain category(s) this specific product belongs to."
    )


class CompanyProducts(BaseModel):
    reasoning: str = Field(description="Brief justification of why these products fit this company.")
    products: list[Product] = Field(min_length=10, max_length=50)

    @field_validator("products")
    @classmethod
    def _dedupe_names(cls, products: list[Product]) -> list[Product]:
        seen: set[str] = set()
        unique: list[Product] = []
        for p in products:
            key = p.name.strip().lower()
            if key not in seen:
                seen.add(key)
                unique.append(p)
        return unique


# ─────────────────────────── Pipeline result ───────────────────────────

class CompanyResult(BaseModel):
    company_id: str
    company_name: str
    location: str | None = None
    url: str | None = None

    url_read: UrlReadResult
    classification: CompanyClassification | None = None
    research: IndustryContext | None = None
    products: CompanyProducts | None = None

    success: bool = True
    error: str | None = None
    processing_time_ms: int = 0


# ─────────────────────────── SSE events ───────────────────────────

class SSEEventType(str, Enum):
    PROGRESS = "progress"
    COMPANY_DONE = "company_done"
    COMPANY_FAILED = "company_failed"
    STATUS_CHANGE = "status_change"
    LOG = "log"
    COMPLETE = "complete"
    ERROR = "error"


class SSEEvent(BaseModel):
    event: SSEEventType
    job_id: str
    data: dict
    seq: int = 0
