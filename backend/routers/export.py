from __future__ import annotations

import tempfile
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import FileResponse
from sqlalchemy.ext.asyncio import AsyncSession

from backend.auth.jwt_verify import AuthenticatedUser, get_current_user
from backend.db.database import get_session
from backend.db.models import Project
from backend.storage.duckdb_queries import export_project, get_stats

router = APIRouter(prefix="/api/export", tags=["export"])


@router.get("/{project_id}")
async def export_products(
    project_id: str,
    fmt: str = Query(default="csv", pattern="^(csv|json)$"),
    job_id: str | None = None,
    user: AuthenticatedUser = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
):
    project = await session.get(Project, project_id)
    if project is None or project.user_id != user.id:
        raise HTTPException(404, "Project not found")

    tmp_dir = Path(tempfile.gettempdir()) / "product_generator_exports"
    tmp_dir.mkdir(parents=True, exist_ok=True)
    out_path = tmp_dir / f"{project.name.replace(' ', '_')}_{project_id[:8]}.{fmt}"

    export_project(project_id, job_id, str(out_path), fmt=fmt)  # type: ignore[arg-type]

    media_type = "text/csv" if fmt == "csv" else "application/json"
    return FileResponse(out_path, media_type=media_type, filename=out_path.name)


@router.get("/{project_id}/stats")
async def project_stats(
    project_id: str,
    job_id: str | None = None,
    user: AuthenticatedUser = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
):
    project = await session.get(Project, project_id)
    if project is None or project.user_id != user.id:
        raise HTTPException(404, "Project not found")
    return get_stats(project_id, job_id)
