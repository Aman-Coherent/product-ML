from __future__ import annotations

import csv
import io
import re
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, UploadFile
from pydantic import BaseModel
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.auth.jwt_verify import AuthenticatedUser, get_current_user
from backend.db.database import get_session
from backend.db.models import CompanyInput, Job, Project
from backend.storage.parquet_writer import clear_project_products

router = APIRouter(prefix="/api/projects", tags=["projects"])

MAX_ROWS = 200_000

# Real-world company lists rarely use our exact internal field names, so we
# fuzzy-match a wide range of common header variants instead of requiring an
# exact "company_name" / "location" / "url" header. Order matters: aliases
# are tried in this priority order for exact matches first, then substring
# matches, so the most specific/common variant wins over a looser guess.
COMPANY_NAME_ALIASES = [
    "company_name", "companyname", "company", "business_name", "businessname",
    "business", "organization_name", "organisation_name", "organization",
    "organisation", "org_name", "orgname", "supplier_name", "suppliername",
    "supplier", "firm_name", "firmname", "firm", "vendor_name", "vendorname",
    "vendor", "manufacturer_name", "manufacturer", "client_name", "clientname",
    "client", "account_name", "accountname", "entity_name", "name",
]
URL_ALIASES = [
    "url", "website", "website_url", "web_site", "site", "domain", "homepage",
    "home_page", "web_page", "webpage", "link", "company_url", "company_website",
    "web_address", "www",
]
LOCATION_ALIASES = [
    "location", "address", "full_address", "fulladdress", "mailing_address",
    "hq_location", "headquarters", "hq", "city", "town", "state", "province",
    "region", "country", "location_name", "city_country",
]

# A column whose normalized name contains one of these tokens holds
# metadata ABOUT the data (a count, id, flag, ...) rather than the data
# itself — even if it also happens to contain an alias as a substring. A
# real-world export can easily have a "location_count" or "location_entries"
# column sitting right next to "main_city"/"main_state"/"main_country"; the
# loose `alias in norm` substring check below would otherwise happily fold
# that integer into the combined location string (e.g. "Paris, France, 1").
# This only guards the fuzzy substring fallback — an exact alias match is
# still always accepted, since a column literally named e.g. "location" is
# unambiguous regardless.
_METADATA_TOKENS = {
    "count", "counts", "id", "ids", "num", "number", "numbers", "total",
    "totals", "qty", "quantity", "flag", "flags", "entries", "entry",
    "index", "idx", "amount", "amounts", "size", "length", "len",
}


def _normalize_header(header: str) -> str:
    header = header.strip().lower()
    header = re.sub(r"[^a-z0-9]+", "_", header)
    return header.strip("_")


def _is_metadata_column(norm: str) -> bool:
    return any(token in _METADATA_TOKENS for token in norm.split("_"))


def _resolve_columns(fieldnames: list[str]) -> tuple[str | None, str | None, list[str]]:
    """
    Maps a CSV's actual headers onto (company_name_column, url_column,
    location_columns). `location_columns` can be multiple headers (e.g.
    separate City/State/Country columns) which get combined into one string
    per row, since that's how most real-world exports are structured.
    """
    pool = [(h, _normalize_header(h)) for h in fieldnames]

    def take_exact(aliases: list[str]) -> str | None:
        for alias in aliases:
            for i, (orig, norm) in enumerate(pool):
                if norm == alias:
                    return pool.pop(i)[0]
        return None

    def take_fuzzy(aliases: list[str]) -> str | None:
        for alias in aliases:
            for i, (orig, norm) in enumerate(pool):
                if norm and not _is_metadata_column(norm) and (alias in norm or norm in alias):
                    return pool.pop(i)[0]
        return None

    company_col = take_exact(COMPANY_NAME_ALIASES) or take_fuzzy(COMPANY_NAME_ALIASES)
    url_col = take_exact(URL_ALIASES) or take_fuzzy(URL_ALIASES)

    location_cols: list[str] = []
    for alias in LOCATION_ALIASES:
        for i in range(len(pool) - 1, -1, -1):
            orig, norm = pool[i]
            if not norm:
                continue
            is_exact = norm == alias
            is_fuzzy = not _is_metadata_column(norm) and (alias in norm or norm in alias)
            if is_exact or is_fuzzy:
                location_cols.append(orig)
                pool.pop(i)

    # Re-sort to match the CSV's original left-to-right column order so a
    # combined "City, State, Country" string reads naturally.
    location_cols = [h for h in fieldnames if h in location_cols]

    return company_col, url_col, location_cols


class ProjectOut(BaseModel):
    id: str
    name: str
    description: str | None
    mode: str
    total_companies: int
    created_at: datetime
    updated_at: datetime
    latest_job_status: str | None = None

    class Config:
        from_attributes = True


class CreateProjectRequest(BaseModel):
    name: str
    description: str | None = None
    mode: str  # classification | generation | both


@router.get("", response_model=list[ProjectOut])
async def list_projects(
    user: AuthenticatedUser = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
):
    result = await session.execute(
        select(Project).where(Project.user_id == user.id).order_by(Project.updated_at.desc())
    )
    projects = result.scalars().all()

    out = []
    for p in projects:
        job_result = await session.execute(
            select(Job).where(Job.project_id == p.id).order_by(Job.created_at.desc()).limit(1)
        )
        latest_job = job_result.scalars().first()
        item = ProjectOut.model_validate(p)
        item.latest_job_status = latest_job.status if latest_job else None
        out.append(item)
    return out


@router.post("", response_model=ProjectOut)
async def create_project(
    body: CreateProjectRequest,
    user: AuthenticatedUser = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
):
    if body.mode not in ("classification", "generation", "both"):
        raise HTTPException(400, "mode must be one of: classification, generation, both")

    project = Project(user_id=user.id, name=body.name, description=body.description, mode=body.mode)
    session.add(project)
    await session.commit()
    await session.refresh(project)
    return ProjectOut.model_validate(project)


@router.get("/{project_id}", response_model=ProjectOut)
async def get_project(
    project_id: str,
    user: AuthenticatedUser = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
):
    project = await _get_owned_project(session, project_id, user.id)
    return ProjectOut.model_validate(project)


@router.delete("/{project_id}")
async def delete_project(
    project_id: str,
    user: AuthenticatedUser = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
):
    project = await _get_owned_project(session, project_id, user.id)
    await session.execute(delete(Project).where(Project.id == project.id))
    await session.commit()
    return {"status": "deleted"}


class CsvValidationError(BaseModel):
    row: int
    message: str


class UploadCsvResponse(BaseModel):
    total_rows: int
    preview: list[dict]
    errors: list[CsvValidationError]
    detected_columns: dict[str, str | None]


@router.post("/{project_id}/upload-csv", response_model=UploadCsvResponse)
async def upload_csv(
    project_id: str,
    file: UploadFile,
    user: AuthenticatedUser = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
):
    project = await _get_owned_project(session, project_id, user.id)

    raw = await file.read()
    try:
        text = raw.decode("utf-8-sig")
    except UnicodeDecodeError:
        raise HTTPException(400, "CSV must be UTF-8 encoded")

    reader = csv.DictReader(io.StringIO(text))
    if not reader.fieldnames:
        raise HTTPException(400, "CSV file is empty")

    fieldnames = [f for f in reader.fieldnames if f is not None]
    company_col, url_col, location_cols = _resolve_columns(fieldnames)

    if company_col is None:
        raise HTTPException(
            400,
            "Could not find a company name column in your CSV. Found these columns: "
            f"{', '.join(fieldnames)}. Rename one of them to something like 'Company Name', "
            "'Company', or 'Business Name'.",
        )

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

    # Replace existing companies for this project with the new upload. New
    # CompanyInput rows get brand new auto-generated IDs, so any Parquet
    # product data already written for the OLD company_id values becomes
    # permanently unreachable garbage that would otherwise silently inflate
    # every future stats/export query for this project - clear it now.
    await session.execute(delete(CompanyInput).where(CompanyInput.project_id == project.id))
    clear_project_products(project.id)

    for idx, row in enumerate(rows):
        session.add(
            CompanyInput(
                project_id=project.id,
                row_index=idx,
                company_name=row["company_name"],
                location=row["location"],
                url=row["url"],
            )
        )

    project.total_companies = len(rows)
    await session.commit()

    return UploadCsvResponse(
        total_rows=len(rows),
        preview=rows[:5],
        errors=errors[:20],
        detected_columns=detected_columns,
    )


async def _get_owned_project(session: AsyncSession, project_id: str, user_id: str) -> Project:
    project = await session.get(Project, project_id)
    if project is None or project.user_id != user_id:
        raise HTTPException(404, "Project not found")
    return project
