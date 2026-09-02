"""
Pydantic schemas for the email-finder module. Kept entirely separate from
backend/core/models.py (the product-generation schemas) since this feature
has its own trust model: every result is traceable to either a real scraped
page or an explicitly-labeled guess, never LLM prose.
"""
from __future__ import annotations

from enum import Enum

from pydantic import BaseModel, Field


class WebsiteSource(str, Enum):
    PROVIDED = "provided"           # came straight from the input CSV
    WEB_SEARCH = "web_search"       # found via Groq compound-beta web search
    DOMAIN_GUESS = "domain_guess"   # derived from the company name + DNS-validated
    NOT_FOUND = "not_found"         # no website could be established at all


class EmailTier(str, Enum):
    """Worst to best. See pipeline.py docstring for what each one means."""

    PATTERN_UNVERIFIED = "pattern_unverified"
    PATTERN_CATCHALL = "pattern_catchall"
    PATTERN_SMTP_VERIFIED = "pattern_smtp_verified"
    SCRAPED_OFFSITE = "scraped_offsite"
    SCRAPED_VERIFIED = "scraped_verified"


TIER_CONFIDENCE: dict[EmailTier, float] = {
    EmailTier.PATTERN_UNVERIFIED: 0.20,
    EmailTier.PATTERN_CATCHALL: 0.40,
    EmailTier.PATTERN_SMTP_VERIFIED: 0.70,
    EmailTier.SCRAPED_OFFSITE: 0.60,
    EmailTier.SCRAPED_VERIFIED: 0.95,
}


class EmailLabel(str, Enum):
    GENERAL = "general"        # info@, contact@, hello@, enquiries@
    SALES = "sales"
    SUPPORT = "support"
    HR = "hr"                  # careers@, jobs@, hr@
    MEDIA = "media"            # press@, media@, pr@
    PERSONAL = "personal"      # firstname@ / firstname.lastname@ / anything else
    UNKNOWN = "unknown"


class EmailCandidate(BaseModel):
    email: str
    label: EmailLabel
    tier: EmailTier
    confidence: float
    source_page: str | None = Field(
        default=None, description="URL of the page this was scraped from, if scraped."
    )

    @property
    def rank_key(self) -> tuple:
        # Higher confidence first; within a tier, GENERAL labels win.
        return (self.confidence, self.label == EmailLabel.GENERAL)


class WebsiteDiscoveryResult(BaseModel):
    url: str | None = None
    source: WebsiteSource = WebsiteSource.NOT_FOUND
    detail: str | None = None  # e.g. which search/guess strategy worked


class EmailResult(BaseModel):
    company_id: str
    company_name: str
    location: str | None = None
    input_url: str | None = None

    resolved_url: str | None = None
    website_source: WebsiteSource = WebsiteSource.NOT_FOUND

    primary_email: str | None = None
    primary_label: EmailLabel | None = None
    primary_tier: EmailTier | None = None
    primary_confidence: float = 0.0
    primary_source_page: str | None = None

    alternate_emails: list[EmailCandidate] = Field(default_factory=list)
    pages_checked: list[str] = Field(default_factory=list)

    success: bool = True
    error: str | None = None
    processing_time_ms: int = 0
