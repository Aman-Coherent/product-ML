from __future__ import annotations

import csv
import io
import json
import tempfile
import time
from datetime import datetime
from pathlib import Path

from arq import ArqRedis
from fastapi import APIRouter, Depends, HTTPException, Query, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel
from redis.asyncio import Redis
from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.auth.jwt_verify import AuthenticatedUser, get_current_user
from backend.core.csv_columns import resolve_columns
from backend.core.job_control import request_cancel, request_pause, request_resume
from backend.db.database import get_session
from backend.db.email_models import EmailBatch, EmailCompanyInput
from backend.dependencies import arq_dependency, redis_dependency

router = APIRouter(prefix="/api/email-finder", tags=["email-finder"])

MAX_ROWS = 200_000
PAGE_SIZE_DEFAULT = 50
PAGE_SIZE_MAX = 200


# ─────────────────────────── schemas ───────────────────────────

class EmailBatchOut(BaseModel):
    id: str
    name: str
    status: str
    concurrency: int
    total: int
    done: int
    failed: int
    error_message: str | None
    started_at: datetime | None
    finished_at: datetime | None
    created_at: datetime

    class Config:
        from_attributes = True


class CreateBatchRequest(BaseModel):
    name: str


class CsvValidationError(BaseModel):
    row: int
    message: str


class UploadCsvResponse(BaseModel):
    total_rows: int
    preview: list[dict]
    errors: list[CsvValidationError]
    detected_columns: dict[str, str | None]


class StartBatchRequest(BaseModel):
    concurrency: int = 10


class EmailCandidateOut(BaseModel):
    email: str
    label: str
    tier: str
    confidence: float
    source_page: str | None = None


class EmailCompanyOut(BaseModel):
    id: str
    row_index: int
    company_name: str
    location: str | None
    url: str | None
    status: str
    resolved_url: str | None
    website_source: str | None
    primary_email: str | None
    primary_label: str | None
    primary_tier: str | None
    primary_confidence: float | None
    primary_source_page: str | None
    alternate_emails: list[EmailCandidateOut]
    processing_time_ms: int | None
    error_message: str | None

    @classmethod
    def from_row(cls, row: EmailCompanyInput) -> "EmailCompanyOut":
        alternates = json.loads(row.alternate_emails_json) if row.alternate_emails_json else []
        return cls(
            id=row.id,
            row_index=row.row_index,
            company_name=row.company_name,
            location=row.location,
            url=row.url,
            status=row.status,
            resolved_url=row.resolved_url,
            website_source=row.website_source,
            primary_email=row.primary_email,
            primary_label=row.primary_label,
            primary_tier=row.primary_tier,
            primary_confidence=row.primary_confidence,
            primary_source_page=row.primary_source_page,
            alternate_emails=[EmailCandidateOut(**c) for c in alternates],
            processing_time_ms=row.processing_time_ms,
            error_message=row.error_message,
        )


class EmailCompanyPage(BaseModel):
    companies: list[EmailCompanyOut]
    next_cursor: str | None
    total: int


# ─────────────────────────── batches ───────────────────────────

@router.get("/batches", response_model=list[EmailBatchOut])
async def list_batches(
    user: AuthenticatedUser = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
):
    result = await session.execute(
        select(EmailBatch).where(EmailBatch.user_id == user.id).order_by(EmailBatch.updated_at.desc())
    )
    return [EmailBatchOut.model_validate(b) for b in result.scalars().all()]


@router.post("/batches", response_model=EmailBatchOut)
async def create_batch(
    body: CreateBatchRequest,
    user: AuthenticatedUser = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
):
    batch = EmailBatch(user_id=user.id, name=body.name)
    session.add(batch)
    await session.commit()
    await session.refresh(batch)
    return EmailBatchOut.model_validate(batch)


@router.get("/batches/{batch_id}", response_model=EmailBatchOut)
async def get_batch(
    batch_id: str,
    user: AuthenticatedUser = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
):
    batch = await _get_owned_batch(session, batch_id, user.id)
    return EmailBatchOut.model_validate(batch)


@router.delete("/batches/{batch_id}")
async def delete_batch(
    batch_id: str,
    user: AuthenticatedUser = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
):
    batch = await _get_owned_batch(session, batch_id, user.id)
    await session.execute(delete(EmailBatch).where(EmailBatch.id == batch.id))
    await session.commit()
    return {"status": "deleted"}


# ─────────────────────────── CSV upload ───────────────────────────

@router.post("/batches/{batch_id}/upload-csv", response_model=UploadCsvResponse)
async def upload_csv(
    batch_id: str,
    file: UploadFile,
    user: AuthenticatedUser = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
):
    batch = await _get_owned_batch(session, batch_id, user.id)

    raw = await file.read()
    try:
        text = raw.decode("utf-8-sig")
    except UnicodeDecodeError:
        raise HTTPException(400, "CSV must be UTF-8 encoded")

    reader = csv.DictReader(io.StringIO(text))
    if not reader.fieldnames:
        raise HTTPException(400, "CSV file is empty")

    fieldnames = [f for f in reader.fieldnames if f is not None]
    company_col, url_col, location_cols = resolve_columns(fieldnames)

    if company_col is None:
        raise HTTPException(
            400,
            "Could not find a company name column in your CSV. Found these columns: "
            f"{', '.join(fieldnames)}. Rename one of them to something like 'Company Name', "
            "'Company', or 'Business Name'.",
        )
    # Unlike product generation, a URL column is NOT required here - the
    # email finder's whole point is to also handle rows with no known
    # website (see backend/core/email_finder/website_discovery.py +
    # domain_guesser.py). Location is still useful even without a URL
    # (helps pick the right ccTLD when guessing a domain), so it stays
    # optional-but-encouraged rather than required either.

    detected_columns = {
        "company_name": company_col,
        "url": url_col,
        "location": " + ".join(location_cols) if location_cols else None,
    }

    errors: list[CsvValidationError] = []
    rows: list[dict] = []
    for idx, raw_row in enumerate(reader):
        company_name = (raw_row.get(company_col) or "").strip()
        if not company_name:
            errors.append(CsvValidationError(row=idx + 2, message="Missing company name"))
            continue

        url_val = (raw_row.get(url_col) or "").strip() if url_col else ""
        location_val = ", ".join(
            part for c in location_cols if (part := (raw_row.get(c) or "").strip())
        )

        rows.append(
            {
                "company_name": company_name,
                "location": location_val or None,
                "url": url_val or None,
            }
        )
        if len(rows) > MAX_ROWS:
            raise HTTPException(400, f"CSV exceeds the maximum of {MAX_ROWS:,} rows")

    # Replace any previous upload for this batch, same reasoning as
    # routers/projects.py upload_csv (fresh company_id values on re-upload).
    await session.execute(delete(EmailCompanyInput).where(EmailCompanyInput.batch_id == batch.id))

    for idx, row in enumerate(rows):
        session.add(
            EmailCompanyInput(
                batch_id=batch.id,
                row_index=idx,
                company_name=row["company_name"],
                location=row["location"],
                url=row["url"],
            )
        )

    batch.done = 0
    batch.failed = 0
    await session.commit()

    return UploadCsvResponse(
        total_rows=len(rows),
        preview=rows[:5],
        errors=errors[:20],
        detected_columns=detected_columns,
    )


# ─────────────────────────── run control ───────────────────────────

@router.post("/batches/{batch_id}/start", response_model=EmailBatchOut)
async def start_batch(
    batch_id: str,
    body: StartBatchRequest,
    user: AuthenticatedUser = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
    arq_pool: ArqRedis = Depends(arq_dependency),
):
    batch = await _get_owned_batch(session, batch_id, user.id)

    count_result = await session.execute(select(EmailCompanyInput).where(EmailCompanyInput.batch_id == batch.id))
    companies = count_result.scalars().all()
    if not companies:
        raise HTTPException(400, "Upload a CSV of companies before starting")

    if batch.status in ("RUNNING", "QUEUED"):
        raise HTTPException(400, f"Batch is already {batch.status.lower()}")

    batch.concurrency = max(1, min(body.concurrency, 50))
    batch.total = len(companies)
    batch.status = "PENDING"
    batch.error_message = None
    for c in companies:
        if c.status != "done":
            c.status = "pending"
    await session.commit()
    await session.refresh(batch)

    await arq_pool.enqueue_job("process_email_batch", batch.id, _job_id=f"email_batch_{batch.id}")
    batch.status = "QUEUED"
    await session.commit()

    return EmailBatchOut.model_validate(batch)


@router.post("/batches/{batch_id}/pause")
async def pause_batch(
    batch_id: str,
    user: AuthenticatedUser = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
    redis: Redis = Depends(redis_dependency),
):
    batch = await _get_owned_batch(session, batch_id, user.id)
    if batch.status != "RUNNING":
        raise HTTPException(400, f"Cannot pause a batch in status {batch.status}")
    await request_pause(redis, batch_id)
    return {"status": "pause_requested"}


@router.post("/batches/{batch_id}/resume", response_model=EmailBatchOut)
async def resume_batch(
    batch_id: str,
    user: AuthenticatedUser = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
    redis: Redis = Depends(redis_dependency),
    arq_pool: ArqRedis = Depends(arq_dependency),
):
    batch = await _get_owned_batch(session, batch_id, user.id)
    if batch.status not in ("PAUSED", "FAILED"):
        raise HTTPException(400, f"Cannot resume a batch in status {batch.status}")

    await request_resume(redis, batch_id)
    batch.status = "QUEUED"
    batch.error_message = None
    await session.commit()

    await arq_pool.enqueue_job("process_email_batch", batch.id, _job_id=f"email_batch_{batch.id}_resume_{int(time.time())}")
    return EmailBatchOut.model_validate(batch)


@router.post("/batches/{batch_id}/cancel")
async def cancel_batch(
    batch_id: str,
    user: AuthenticatedUser = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
    redis: Redis = Depends(redis_dependency),
):
    batch = await _get_owned_batch(session, batch_id, user.id)
    if batch.status not in ("RUNNING", "QUEUED", "PAUSED"):
        raise HTTPException(400, f"Cannot cancel a batch in status {batch.status}")
    await request_cancel(redis, batch_id)
    if batch.status != "RUNNING":
        batch.status = "CANCELLED"
        await session.commit()
    return {"status": "cancel_requested"}


@router.post("/batches/{batch_id}/retry-failed", response_model=EmailBatchOut)
async def retry_failed_companies(
    batch_id: str,
    user: AuthenticatedUser = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
    arq_pool: ArqRedis = Depends(arq_dependency),
):
    batch = await _get_owned_batch(session, batch_id, user.id)
    if batch.status not in ("COMPLETED", "FAILED"):
        raise HTTPException(400, f"Cannot retry failed companies while the batch is {batch.status}")

    all_companies = list(
        (await session.execute(select(EmailCompanyInput).where(EmailCompanyInput.batch_id == batch.id)))
        .scalars()
        .all()
    )
    failed_companies = [c for c in all_companies if c.status == "failed"]
    if not failed_companies:
        raise HTTPException(400, "No failed companies to retry")

    for c in failed_companies:
        c.status = "pending"
    batch.status = "PENDING"
    batch.error_message = None
    await session.commit()
    await session.refresh(batch)

    await arq_pool.enqueue_job("process_email_batch", batch.id, _job_id=f"email_batch_{batch.id}_retry_{int(time.time())}")
    batch.status = "QUEUED"
    await session.commit()

    return EmailBatchOut.model_validate(batch)


# ─────────────────────────── companies + export ───────────────────────────

_CATEGORY_FILTERS = {
    # Mirrors EmailTierBadge.tsx's categorize() exactly - see batch_stats'
    # by_category comment for why this 3-way split exists.
    "found_given": lambda q: q.where(
        EmailCompanyInput.primary_tier.in_(("scraped_verified", "scraped_offsite")),
        EmailCompanyInput.website_source == "provided",
    ),
    "found_discovered": lambda q: q.where(
        EmailCompanyInput.primary_tier.in_(("scraped_verified", "scraped_offsite")),
        EmailCompanyInput.website_source != "provided",
    ),
    "guessed": lambda q: q.where(
        EmailCompanyInput.primary_tier.in_(("pattern_smtp_verified", "pattern_catchall", "pattern_unverified"))
    ),
    "not_found": lambda q: q.where(
        EmailCompanyInput.status == "done", EmailCompanyInput.primary_tier.is_(None)
    ),
}


@router.get("/batches/{batch_id}/companies", response_model=EmailCompanyPage)
async def list_companies(
    batch_id: str,
    cursor: str | None = None,
    limit: int = Query(default=PAGE_SIZE_DEFAULT, le=PAGE_SIZE_MAX),
    status_filter: str | None = Query(default=None, alias="status"),
    category: str | None = Query(default=None, pattern="^(found_given|found_discovered|guessed|not_found)$"),
    user: AuthenticatedUser = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
):
    await _get_owned_batch(session, batch_id, user.id)

    query = select(EmailCompanyInput).where(EmailCompanyInput.batch_id == batch_id)
    if status_filter:
        query = query.where(EmailCompanyInput.status == status_filter)
    if category:
        query = _CATEGORY_FILTERS[category](query)
    if cursor:
        try:
            cursor_index = int(cursor)
        except ValueError:
            raise HTTPException(400, "Invalid cursor")
        query = query.where(EmailCompanyInput.row_index > cursor_index)

    query = query.order_by(EmailCompanyInput.row_index).limit(limit + 1)
    result = await session.execute(query)
    rows = list(result.scalars().all())

    has_more = len(rows) > limit
    page_rows = rows[:limit]
    next_cursor = str(page_rows[-1].row_index) if has_more and page_rows else None

    total_query = select(func.count()).select_from(EmailCompanyInput).where(EmailCompanyInput.batch_id == batch_id)
    if status_filter:
        total_query = total_query.where(EmailCompanyInput.status == status_filter)
    if category:
        total_query = _CATEGORY_FILTERS[category](total_query)
    total = (await session.execute(total_query)).scalar_one()

    return EmailCompanyPage(
        companies=[EmailCompanyOut.from_row(c) for c in page_rows],
        next_cursor=next_cursor,
        total=total,
    )


@router.get("/batches/{batch_id}/stats")
async def batch_stats(
    batch_id: str,
    user: AuthenticatedUser = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
):
    await _get_owned_batch(session, batch_id, user.id)

    total = (
        await session.execute(
            select(func.count()).select_from(EmailCompanyInput).where(EmailCompanyInput.batch_id == batch_id)
        )
    ).scalar_one()
    with_email = (
        await session.execute(
            select(func.count())
            .select_from(EmailCompanyInput)
            .where(EmailCompanyInput.batch_id == batch_id, EmailCompanyInput.primary_email.is_not(None))
        )
    ).scalar_one()
    by_tier_rows = (
        await session.execute(
            select(EmailCompanyInput.primary_tier, func.count())
            .where(EmailCompanyInput.batch_id == batch_id, EmailCompanyInput.primary_tier.is_not(None))
            .group_by(EmailCompanyInput.primary_tier)
        )
    ).all()
    by_source_rows = (
        await session.execute(
            select(EmailCompanyInput.website_source, func.count())
            .where(EmailCompanyInput.batch_id == batch_id)
            .group_by(EmailCompanyInput.website_source)
        )
    ).all()

    # Same 3-way simplification as the frontend badge (EmailTierBadge.tsx) -
    # computed here in one grouped query rather than in Python over every
    # row, since a batch can have tens of thousands of companies and the
    # UI needs this instantly, not after scanning the whole table.
    by_category_rows = (
        await session.execute(
            select(EmailCompanyInput.primary_tier, EmailCompanyInput.website_source, func.count())
            .where(EmailCompanyInput.batch_id == batch_id, EmailCompanyInput.primary_tier.is_not(None))
            .group_by(EmailCompanyInput.primary_tier, EmailCompanyInput.website_source)
        )
    ).all()
    by_category = {"found_given": 0, "found_discovered": 0, "guessed": 0, "not_found": 0}
    for tier, source, count in by_category_rows:
        if tier in ("scraped_verified", "scraped_offsite"):
            by_category["found_given" if source == "provided" else "found_discovered"] += count
        else:
            by_category["guessed"] += count

    # A completed row with NO tier at all (see pipeline.py: primary_tier
    # only ends up set once find_company_email produces some candidate -
    # scraped or guessed) means no website could be established at all,
    # nothing to guess a pattern against either. Previously invisible in
    # this breakdown entirely - the three counts above didn't add up to
    # "done" on a real batch, with no way to tell why (see this router's
    # docstring history / the confirmed real case that surfaced it).
    by_category["not_found"] = (
        await session.execute(
            select(func.count())
            .select_from(EmailCompanyInput)
            .where(
                EmailCompanyInput.batch_id == batch_id,
                EmailCompanyInput.status == "done",
                EmailCompanyInput.primary_tier.is_(None),
            )
        )
    ).scalar_one()

    return {
        "total_companies": total,
        "with_email": with_email,
        "by_tier": {row[0]: row[1] for row in by_tier_rows if row[0]},
        "by_website_source": {row[0]: row[1] for row in by_source_rows if row[0]},
        "by_category": by_category,
    }


@router.get("/batches/{batch_id}/export")
async def export_batch(
    batch_id: str,
    fmt: str = Query(default="csv", pattern="^(csv|json)$"),
    user: AuthenticatedUser = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
):
    batch = await _get_owned_batch(session, batch_id, user.id)

    result = await session.execute(
        select(EmailCompanyInput).where(EmailCompanyInput.batch_id == batch_id).order_by(EmailCompanyInput.row_index)
    )
    rows = list(result.scalars().all())

    tmp_dir = Path(tempfile.gettempdir()) / "email_finder_exports"
    tmp_dir.mkdir(parents=True, exist_ok=True)
    out_path = tmp_dir / f"{batch.name.replace(' ', '_')}_{batch_id[:8]}.{fmt}"

    # row_id (the row's position in the originally uploaded CSV, 1-based to
    # match how a person counts rows in a spreadsheet) and id (this row's
    # own permanent database id) are both included so an export can always
    # be matched back to the source file OR re-looked-up later, even after
    # re-sorting/filtering in Excel - previously the export had NO
    # identifying column at all, so once opened in a spreadsheet there was
    # no reliable way to tell which output row corresponded to which input
    # row.
    fields = [
        "row_id", "id", "company_name", "location", "input_url", "resolved_url", "website_source",
        "primary_email", "primary_label", "primary_tier", "primary_confidence",
        "primary_source_page", "alternate_emails", "status", "error_message",
    ]

    def _row_dict(c: EmailCompanyInput) -> dict:
        alternates = json.loads(c.alternate_emails_json) if c.alternate_emails_json else []
        return {
            "row_id": c.row_index + 1,
            "id": c.id,
            "company_name": c.company_name,
            "location": c.location,
            "input_url": c.url,
            "resolved_url": c.resolved_url,
            "website_source": c.website_source,
            "primary_email": c.primary_email,
            "primary_label": c.primary_label,
            "primary_tier": c.primary_tier,
            "primary_confidence": c.primary_confidence,
            "primary_source_page": c.primary_source_page,
            "alternate_emails": "; ".join(a["email"] for a in alternates),
            "status": c.status,
            "error_message": c.error_message,
        }

    if fmt == "csv":
        with open(out_path, "w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=fields)
            writer.writeheader()
            for c in rows:
                writer.writerow(_row_dict(c))
        media_type = "text/csv"
    else:
        out_path.write_text(json.dumps([_row_dict(c) for c in rows], indent=2), encoding="utf-8")
        media_type = "application/json"

    return FileResponse(out_path, media_type=media_type, filename=out_path.name)


async def _get_owned_batch(session: AsyncSession, batch_id: str, user_id: str) -> EmailBatch:
    batch = await session.get(EmailBatch, batch_id)
    if batch is None or batch.user_id != user_id:
        raise HTTPException(404, "Email batch not found")
    return batch
