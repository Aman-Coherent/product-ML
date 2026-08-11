"""
SQLAlchemy 2.0 ORM models — OLTP state only (users, projects, jobs, per
company status, API keys). Bulk product data lives in Parquet, queried
via DuckDB (see backend/storage/).
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone

import sqlalchemy as sa
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

from backend.db.encrypted_type import EncryptedString


def _uuid() -> str:
    return uuid.uuid4().hex


def _now() -> datetime:
    return datetime.now(timezone.utc)


class Base(DeclarativeBase):
    pass


class User(Base):
    """
    Mirrors the Auth.js user. Auth.js owns its own Prisma/SQLite tables on
    the frontend; this row is created/looked-up by external `id` (the
    Auth.js user id, forwarded inside the verified JWT) the first time the
    user hits the backend, so every project/job can be scoped to them.
    """

    __tablename__ = "users"

    id: Mapped[str] = mapped_column(sa.String(64), primary_key=True)
    email: Mapped[str] = mapped_column(sa.String(255), unique=True, index=True)
    name: Mapped[str | None] = mapped_column(sa.String(255), nullable=True)
    created_at: Mapped[datetime] = mapped_column(sa.DateTime(timezone=True), default=_now)

    projects: Mapped[list["Project"]] = relationship(back_populates="owner", cascade="all, delete-orphan")
    api_keys: Mapped[list["UserApiKey"]] = relationship(back_populates="owner", cascade="all, delete-orphan")


class UserApiKey(Base):
    """A user-supplied LLM API key, encrypted at rest."""

    __tablename__ = "user_api_keys"

    id: Mapped[str] = mapped_column(sa.String(32), primary_key=True, default=_uuid)
    user_id: Mapped[str] = mapped_column(sa.ForeignKey("users.id", ondelete="CASCADE"), index=True)
    provider: Mapped[str] = mapped_column(sa.String(32))  # groq | mistral | jina | claude | openai | custom
    label: Mapped[str] = mapped_column(sa.String(120))
    api_key: Mapped[str] = mapped_column(EncryptedString)
    model_name: Mapped[str | None] = mapped_column(sa.String(120), nullable=True)  # for custom providers
    base_url: Mapped[str | None] = mapped_column(sa.String(255), nullable=True)  # for custom OpenAI-compatible
    is_active: Mapped[bool] = mapped_column(sa.Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(sa.DateTime(timezone=True), default=_now)

    owner: Mapped[User] = relationship(back_populates="api_keys")

    __table_args__ = (sa.UniqueConstraint("user_id", "provider", "label", name="uq_user_provider_label"),)


class Project(Base):
    __tablename__ = "projects"

    id: Mapped[str] = mapped_column(sa.String(32), primary_key=True, default=_uuid)
    user_id: Mapped[str] = mapped_column(sa.ForeignKey("users.id", ondelete="CASCADE"), index=True)
    name: Mapped[str] = mapped_column(sa.String(255))
    description: Mapped[str | None] = mapped_column(sa.Text, nullable=True)
    mode: Mapped[str] = mapped_column(sa.String(32))  # classification | generation | both
    total_companies: Mapped[int] = mapped_column(sa.Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(sa.DateTime(timezone=True), default=_now)
    updated_at: Mapped[datetime] = mapped_column(sa.DateTime(timezone=True), default=_now, onupdate=_now)

    owner: Mapped[User] = relationship(back_populates="projects")
    jobs: Mapped[list["Job"]] = relationship(back_populates="project", cascade="all, delete-orphan")
    companies: Mapped[list["CompanyInput"]] = relationship(back_populates="project", cascade="all, delete-orphan")


class Job(Base):
    """One execution run (FSM) over a project's companies."""

    __tablename__ = "jobs"

    id: Mapped[str] = mapped_column(sa.String(32), primary_key=True, default=_uuid)
    project_id: Mapped[str] = mapped_column(sa.ForeignKey("projects.id", ondelete="CASCADE"), index=True)
    user_id: Mapped[str] = mapped_column(sa.String(64), index=True)

    status: Mapped[str] = mapped_column(sa.String(32), default="PENDING", index=True)
    # PENDING | QUEUED | RUNNING | PAUSED | COMPLETED | FAILED | CANCELLED

    mode: Mapped[str] = mapped_column(sa.String(32))  # classification | generation | both
    concurrency: Mapped[int] = mapped_column(sa.Integer, default=50)

    total: Mapped[int] = mapped_column(sa.Integer, default=0)
    done: Mapped[int] = mapped_column(sa.Integer, default=0)
    failed: Mapped[int] = mapped_column(sa.Integer, default=0)
    skipped: Mapped[int] = mapped_column(sa.Integer, default=0)

    model_preferences: Mapped[str | None] = mapped_column(sa.Text, nullable=True)  # JSON blob

    error_message: Mapped[str | None] = mapped_column(sa.Text, nullable=True)
    started_at: Mapped[datetime | None] = mapped_column(sa.DateTime(timezone=True), nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(sa.DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(sa.DateTime(timezone=True), default=_now)
    updated_at: Mapped[datetime] = mapped_column(sa.DateTime(timezone=True), default=_now, onupdate=_now)

    project: Mapped[Project] = relationship(back_populates="jobs")


class CompanyInput(Base):
    """One row from the uploaded CSV, plus its processing result/status."""

    __tablename__ = "company_inputs"

    id: Mapped[str] = mapped_column(sa.String(32), primary_key=True, default=_uuid)
    # No standalone index on project_id: the composite ix_company_project_row_index
    # and ix_company_project_status indexes below both start with project_id, so
    # either one already serves plain project_id lookups via its leftmost prefix.
    project_id: Mapped[str] = mapped_column(sa.ForeignKey("projects.id", ondelete="CASCADE"))
    row_index: Mapped[int] = mapped_column(sa.Integer)

    company_name: Mapped[str] = mapped_column(sa.String(255))
    location: Mapped[str | None] = mapped_column(sa.String(255), nullable=True)
    url: Mapped[str | None] = mapped_column(sa.String(500), nullable=True)

    status: Mapped[str] = mapped_column(sa.String(32), default="pending", index=True)
    # pending | running | done | failed

    # URL read tracking
    url_read_source: Mapped[str | None] = mapped_column(sa.String(32), nullable=True)
    # jina_reader | compound_beta | name_location | none
    url_read_success: Mapped[bool | None] = mapped_column(sa.Boolean, nullable=True)
    url_markdown_tokens: Mapped[int | None] = mapped_column(sa.Integer, nullable=True)
    url_error: Mapped[str | None] = mapped_column(sa.String(64), nullable=True)

    # Classification result
    supply_chain_primary: Mapped[str | None] = mapped_column(sa.String(32), nullable=True)
    supply_chain_all: Mapped[str | None] = mapped_column(sa.Text, nullable=True)  # JSON list
    display_label: Mapped[str | None] = mapped_column(sa.String(120), nullable=True)
    classification_confidence: Mapped[float | None] = mapped_column(sa.Float, nullable=True)
    is_multi: Mapped[bool | None] = mapped_column(sa.Boolean, nullable=True)

    products_count: Mapped[int] = mapped_column(sa.Integer, default=0)
    processing_time_ms: Mapped[int | None] = mapped_column(sa.Integer, nullable=True)
    error_message: Mapped[str | None] = mapped_column(sa.Text, nullable=True)

    last_job_id: Mapped[str | None] = mapped_column(sa.String(32), nullable=True)
    created_at: Mapped[datetime] = mapped_column(sa.DateTime(timezone=True), default=_now)
    updated_at: Mapped[datetime] = mapped_column(sa.DateTime(timezone=True), default=_now, onupdate=_now)

    project: Mapped[Project] = relationship(back_populates="companies")

    __table_args__ = (
        # The company table's cursor pagination filters by project_id and
        # orders/seeks by row_index together (`WHERE project_id = ? AND
        # row_index > ? ORDER BY row_index`) — a composite index lets that
        # resolve in O(log n) at any scroll depth. The standalone
        # `project_id` index alone would still need an in-memory sort over
        # every row in the project on each page fetch, which stops being
        # free once a project has 100k+ companies.
        sa.Index("ix_company_project_row_index", "project_id", "row_index"),
        sa.Index("ix_company_project_status", "project_id", "status"),
    )
