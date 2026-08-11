from __future__ import annotations

import json
import time
from datetime import datetime

from arq import ArqRedis
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from redis.asyncio import Redis
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.auth.jwt_verify import AuthenticatedUser, get_current_user
from backend.core.job_engine import request_cancel, request_pause, request_resume
from backend.db.database import get_session
from backend.db.models import CompanyInput, Job, Project
from backend.dependencies import arq_dependency, redis_dependency

router = APIRouter(prefix="/api/jobs", tags=["jobs"])


class JobOut(BaseModel):
    id: str
    project_id: str
    status: str
    mode: str
    concurrency: int
    total: int
    done: int
    failed: int
    skipped: int
    error_message: str | None
    started_at: datetime | None
    finished_at: datetime | None
    created_at: datetime

    class Config:
        from_attributes = True


class CreateJobRequest(BaseModel):
    project_id: str
    mode: str | None = None  # defaults to project.mode
    concurrency: int = 20


@router.post("", response_model=JobOut)
async def create_and_start_job(
    body: CreateJobRequest,
    user: AuthenticatedUser = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
    arq_pool: ArqRedis = Depends(arq_dependency),
):
    project = await session.get(Project, body.project_id)
    if project is None or project.user_id != user.id:
        raise HTTPException(404, "Project not found")

    count_result = await session.execute(
        select(CompanyInput).where(CompanyInput.project_id == project.id)
    )
    companies = count_result.scalars().all()
    if not companies:
        raise HTTPException(400, "Upload a CSV of companies before starting a job")

    concurrency = max(1, min(body.concurrency, 200))
    already_done = sum(1 for c in companies if c.status == "done")

    job = Job(
        project_id=project.id,
        user_id=user.id,
        status="PENDING",
        mode=body.mode or project.mode,
        concurrency=concurrency,
        total=len(companies),
        # Pre-seed with work already completed in prior runs on this project,
        # so the progress bar reflects reality immediately instead of
        # dropping back to 0 while the engine silently skips those companies.
        done=already_done,
    )
    session.add(job)
    # Only normalize companies that haven't successfully completed yet. The
    # job engine skips anything with status == "done" (backend/core/job_engine.py),
    # so resetting *every* company here would silently discard all progress
    # from prior runs on this project and force a full, wasteful reprocess.
    # Previously "failed" companies are intentionally re-queued as "pending"
    # so a new run gives them another chance automatically.
    for c in companies:
        if c.status != "done":
            c.status = "pending"
    await session.commit()
    await session.refresh(job)

    await arq_pool.enqueue_job("process_job", job.id, _job_id=f"job_{job.id}")
    job.status = "QUEUED"
    await session.commit()

    return JobOut.model_validate(job)


@router.get("/{job_id}", response_model=JobOut)
async def get_job(
    job_id: str,
    user: AuthenticatedUser = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
):
    job = await _get_owned_job(session, job_id, user.id)
    return JobOut.model_validate(job)


@router.get("/project/{project_id}", response_model=list[JobOut])
async def list_jobs_for_project(
    project_id: str,
    user: AuthenticatedUser = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
):
    result = await session.execute(
        select(Job).where(Job.project_id == project_id, Job.user_id == user.id).order_by(Job.created_at.desc())
    )
    return [JobOut.model_validate(j) for j in result.scalars().all()]


@router.post("/{job_id}/pause")
async def pause_job(
    job_id: str,
    user: AuthenticatedUser = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
    redis: Redis = Depends(redis_dependency),
):
    job = await _get_owned_job(session, job_id, user.id)
    if job.status != "RUNNING":
        raise HTTPException(400, f"Cannot pause a job in status {job.status}")
    await request_pause(redis, job_id)
    return {"status": "pause_requested"}


@router.post("/{job_id}/resume")
async def resume_job(
    job_id: str,
    user: AuthenticatedUser = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
    redis: Redis = Depends(redis_dependency),
    arq_pool: ArqRedis = Depends(arq_dependency),
):
    job = await _get_owned_job(session, job_id, user.id)
    if job.status not in ("PAUSED", "FAILED"):
        raise HTTPException(400, f"Cannot resume a job in status {job.status}")

    await request_resume(redis, job_id)
    job.status = "QUEUED"
    job.error_message = None
    await session.commit()

    await arq_pool.enqueue_job("process_job", job.id, _job_id=f"job_{job.id}_resume_{int(time.time())}")
    return {"status": "resumed"}


@router.post("/{job_id}/cancel")
async def cancel_job(
    job_id: str,
    user: AuthenticatedUser = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
    redis: Redis = Depends(redis_dependency),
):
    job = await _get_owned_job(session, job_id, user.id)
    if job.status not in ("RUNNING", "QUEUED", "PAUSED"):
        raise HTTPException(400, f"Cannot cancel a job in status {job.status}")
    await request_cancel(redis, job_id)
    if job.status != "RUNNING":
        job.status = "CANCELLED"
        await session.commit()
    return {"status": "cancel_requested"}


@router.post("/{job_id}/retry-failed", response_model=JobOut)
async def retry_failed_companies(
    job_id: str,
    user: AuthenticatedUser = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
    arq_pool: ArqRedis = Depends(arq_dependency),
):
    """Re-runs only the companies that failed in a previous job, as a brand
    new Job row (never reusing the old job_id).

    Reusing the old job_id would silently do nothing: the checkpoint file is
    keyed by job_id and already contains a "failed" entry for every one of
    these companies from the original run, so the engine's
    `id not in completed_ids` resume-guard would skip them all over again -
    a "retry" that retries nothing. A fresh job_id means a fresh (empty)
    checkpoint, so the retried companies are guaranteed to actually run.
    """
    old_job = await _get_owned_job(session, job_id, user.id)
    if old_job.status not in ("COMPLETED", "FAILED"):
        raise HTTPException(
            400,
            f"Cannot retry failed companies while the job is {old_job.status}. "
            "Wait for it to finish, or use Pause/Resume/Stop first.",
        )

    all_companies = list(
        (
            await session.execute(select(CompanyInput).where(CompanyInput.project_id == old_job.project_id))
        )
        .scalars()
        .all()
    )
    failed_companies = [c for c in all_companies if c.status == "failed"]
    if not failed_companies:
        raise HTTPException(400, "No failed companies to retry")

    # "Retry failed" must only ever touch failed companies - never silently
    # sweep up companies that were never attempted at all. That can only
    # happen if the *original* job never finished its full pass (e.g. it was
    # cancelled early), which the COMPLETED/FAILED status check above should
    # already rule out, but we double-check explicitly for safety.
    never_attempted = sum(1 for c in all_companies if c.status not in ("done", "failed"))
    if never_attempted:
        raise HTTPException(
            400,
            f"{never_attempted} companies in this project were never processed yet. "
            "Use Resume or Start new run to finish those first, then retry failed ones.",
        )

    already_done = sum(1 for c in all_companies if c.status == "done")
    for c in failed_companies:
        c.status = "pending"

    new_job = Job(
        project_id=old_job.project_id,
        user_id=user.id,
        status="PENDING",
        mode=old_job.mode,
        concurrency=old_job.concurrency,
        total=len(all_companies),
        done=already_done,
    )
    session.add(new_job)
    await session.commit()
    await session.refresh(new_job)

    await arq_pool.enqueue_job("process_job", new_job.id, _job_id=f"job_{new_job.id}")
    new_job.status = "QUEUED"
    await session.commit()

    return JobOut.model_validate(new_job)


@router.get("/circuit-status/jina")
async def jina_circuit_status(
    user: AuthenticatedUser = Depends(get_current_user),
    redis: Redis = Depends(redis_dependency),
):
    raw = await redis.get("circuit_status:jina_reader")
    if raw is None:
        return {"state": "CLOSED", "failures": 0, "reset_in_s": 0.0}
    return json.loads(raw)


async def _get_owned_job(session: AsyncSession, job_id: str, user_id: str) -> Job:
    job = await session.get(Job, job_id)
    if job is None or job.user_id != user_id:
        raise HTTPException(404, "Job not found")
    return job
