"""
SQLAlchemy models for the email-finder feature - deliberately separate
tables from Project/Job/CompanyInput (backend/db/models.py). This feature
has its own trust model and its own lifecycle; sharing tables with the
product-generation pipeline would mean every email-specific column (tier,
confidence, source page, alternates...) either has to be bolted onto
CompanyInput regardless of project mode, or every product-generation query
has to filter it back out. A clean split keeps both simple.

Uses the SAME `Base` (and therefore the same MetaData/engine/create_all
call) as db/models.py - see db/database.py's init_db - so no separate
migration wiring is needed; these tables are created automatically
alongside the existing ones.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone

import sqlalchemy as sa
from sqlalchemy.orm import Mapped, mapped_column, relationship

from backend.db.models import Base


def _uuid() -> str:
    return uuid.uuid4().hex


def _now() -> datetime:
    return datetime.now(timezone.utc)


class EmailBatch(Base):
    """One CSV upload + run, analogous to Project+Job combined - kept as a
    single entity here (rather than Project/Job's two-table split) since
    this feature has no notion of "re-running the same project with a
    different mode"; a batch is upload-once, run-to-completion."""

    __tablename__ = "email_batches"

    id: Mapped[str] = mapped_column(sa.String(32), primary_key=True, default=_uuid)
    user_id: Mapped[str] = mapped_column(sa.ForeignKey("users.id", ondelete="CASCADE"), index=True)
    name: Mapped[str] = mapped_column(sa.String(255))

    status: Mapped[str] = mapped_column(sa.String(32), default="PENDING", index=True)
    # PENDING | QUEUED | RUNNING | PAUSED | COMPLETED | FAILED | CANCELLED

    concurrency: Mapped[int] = mapped_column(sa.Integer, default=10)
    # Lower default than product-generation Jobs (20) - this pipeline does
    # up to 3 page fetches + SMTP probes per company, not one LLM call, so
    # the same concurrency puts more simultaneous load on THIRD-PARTY mail
    # servers, not just our own LLM provider keys. See smtp_verifier.py's
    # own additional app-wide cap on concurrent SMTP connections specifically.

    total: Mapped[int] = mapped_column(sa.Integer, default=0)
    done: Mapped[int] = mapped_column(sa.Integer, default=0)
    failed: Mapped[int] = mapped_column(sa.Integer, default=0)

    error_message: Mapped[str | None] = mapped_column(sa.Text, nullable=True)
    started_at: Mapped[datetime | None] = mapped_column(sa.DateTime(timezone=True), nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(sa.DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(sa.DateTime(timezone=True), default=_now)
    updated_at: Mapped[datetime] = mapped_column(sa.DateTime(timezone=True), default=_now, onupdate=_now)

    companies: Mapped[list["EmailCompanyInput"]] = relationship(
        back_populates="batch", cascade="all, delete-orphan"
    )


class EmailCompanyInput(Base):
    """One row from the uploaded CSV, plus its email-finding result."""

    __tablename__ = "email_company_inputs"

    id: Mapped[str] = mapped_column(sa.String(32), primary_key=True, default=_uuid)
    batch_id: Mapped[str] = mapped_column(sa.ForeignKey("email_batches.id", ondelete="CASCADE"))
    row_index: Mapped[int] = mapped_column(sa.Integer)

    company_name: Mapped[str] = mapped_column(sa.String(255))
    location: Mapped[str | None] = mapped_column(sa.String(255), nullable=True)
    url: Mapped[str | None] = mapped_column(sa.String(500), nullable=True)

    status: Mapped[str] = mapped_column(sa.String(32), default="pending", index=True)
    # pending | running | done | failed

    # Where the site we actually crawled came from.
    resolved_url: Mapped[str | None] = mapped_column(sa.String(500), nullable=True)
    website_source: Mapped[str | None] = mapped_column(sa.String(32), nullable=True)
    # provided | web_search | domain_guess | not_found

    # The single best result.
    primary_email: Mapped[str | None] = mapped_column(sa.String(255), nullable=True)
    primary_label: Mapped[str | None] = mapped_column(sa.String(32), nullable=True)
    primary_tier: Mapped[str | None] = mapped_column(sa.String(32), nullable=True)
    primary_confidence: Mapped[float | None] = mapped_column(sa.Float, nullable=True)
    primary_source_page: Mapped[str | None] = mapped_column(sa.String(500), nullable=True)

    # JSON list of {email, label, tier, confidence, source_page} - small
    # (capped at 10 in pipeline.py), so a plain JSON column is simpler than
    # a whole extra child table for what's always displayed as one blob
    # anyway (see CompanyTable's expandable row in the frontend).
    alternate_emails_json: Mapped[str | None] = mapped_column(sa.Text, nullable=True)
    pages_checked_json: Mapped[str | None] = mapped_column(sa.Text, nullable=True)

    processing_time_ms: Mapped[int | None] = mapped_column(sa.Integer, nullable=True)
    error_message: Mapped[str | None] = mapped_column(sa.Text, nullable=True)

    last_batch_id: Mapped[str | None] = mapped_column(sa.String(32), nullable=True)
    created_at: Mapped[datetime] = mapped_column(sa.DateTime(timezone=True), default=_now)
    updated_at: Mapped[datetime] = mapped_column(sa.DateTime(timezone=True), default=_now, onupdate=_now)

    batch: Mapped[EmailBatch] = relationship(back_populates="companies")

    __table_args__ = (
        # Same cursor-pagination + status-filter rationale as CompanyInput's
        # indexes in db/models.py.
        sa.Index("ix_email_company_batch_row_index", "batch_id", "row_index"),
        sa.Index("ix_email_company_batch_status", "batch_id", "status"),
    )
