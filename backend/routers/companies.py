from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.auth.jwt_verify import AuthenticatedUser, get_current_user
from backend.db.database import get_session
from backend.db.models import CompanyInput, Project
from backend.storage.duckdb_queries import get_products_for_company

router = APIRouter(prefix="/api/companies", tags=["companies"])


class CompanyOut(BaseModel):
    id: str
    row_index: int
    company_name: str
    location: str | None
    url: str | None
    status: str
    url_read_source: str | None
    url_read_success: bool | None
    url_error: str | None
    supply_chain_primary: str | None
    display_label: str | None
    classification_confidence: float | None
    is_multi: bool | None
    products_count: int
    processing_time_ms: int | None
    error_message: str | None

    class Config:
        from_attributes = True


class CompanyPage(BaseModel):
    companies: list[CompanyOut]
    next_cursor: str | None
    total: int


PAGE_SIZE_DEFAULT = 50
PAGE_SIZE_MAX = 200


@router.get("", response_model=CompanyPage)
async def list_companies(
    project_id: str,
    cursor: str | None = None,
    limit: int = Query(default=PAGE_SIZE_DEFAULT, le=PAGE_SIZE_MAX),
    status_filter: str | None = Query(default=None, alias="status"),
    user: AuthenticatedUser = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
):
    project = await session.get(Project, project_id)
    if project is None or project.user_id != user.id:
        raise HTTPException(404, "Project not found")

    # Keyset (cursor) pagination on row_index — O(1) at any scroll depth,
    # unlike OFFSET which degrades linearly with depth.
    query = select(CompanyInput).where(CompanyInput.project_id == project_id)
    if status_filter:
        query = query.where(CompanyInput.status == status_filter)
    if cursor:
        try:
            cursor_index = int(cursor)
        except ValueError:
            raise HTTPException(400, "Invalid cursor")
        query = query.where(CompanyInput.row_index > cursor_index)

    query = query.order_by(CompanyInput.row_index).limit(limit + 1)
    result = await session.execute(query)
    rows = list(result.scalars().all())

    has_more = len(rows) > limit
    page_rows = rows[:limit]
    next_cursor = str(page_rows[-1].row_index) if has_more and page_rows else None

    total_query = select(func.count()).select_from(CompanyInput).where(CompanyInput.project_id == project_id)
    if status_filter:
        total_query = total_query.where(CompanyInput.status == status_filter)
    total = (await session.execute(total_query)).scalar_one()

    return CompanyPage(
        companies=[CompanyOut.model_validate(c) for c in page_rows],
        next_cursor=next_cursor,
        total=total,
    )


@router.get("/{company_id}/products")
async def get_company_products(
    company_id: str,
    user: AuthenticatedUser = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
):
    company = await session.get(CompanyInput, company_id)
    if company is None:
        raise HTTPException(404, "Company not found")
    project = await session.get(Project, company.project_id)
    if project is None or project.user_id != user.id:
        raise HTTPException(404, "Company not found")

    products = get_products_for_company(project.id, company_id)
    return {"company_id": company_id, "products": products}
