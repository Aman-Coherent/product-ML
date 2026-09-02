"""
ARQ worker process definition. Run with:

    arq backend.workers.arq_worker.WorkerSettings

This runs in a SEPARATE OS process from the FastAPI server. If it crashes,
ARQ automatically retries the job (max_tries=3); the job engine's
checkpoint file ensures already-completed companies are skipped on retry.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone

from arq.connections import RedisSettings
from redis.asyncio import Redis
from sqlalchemy import select

from backend.config import get_settings
from backend.core.email_job_engine import EmailJobEngine
from backend.core.job_control import run_lock_key as _run_lock_key
from backend.core.job_engine import JobEngine
from backend.core.usage_tracker import enable_usage_capture
from backend.db.database import SessionLocal, init_db
from backend.db.email_models import EmailBatch
from backend.db.models import Job

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("arq_worker")


async def process_job(ctx: dict, job_id: str) -> None:
    """The single ARQ task function: runs a job end-to-end."""
    redis: Redis = ctx["redis"]
    logger.info("Starting job %s", job_id)
    engine = JobEngine(SessionLocal, redis, concurrency=20)
    try:
        await engine.run(job_id)
    except Exception:
        logger.exception("Job %s failed with an unhandled exception", job_id)
        raise
    logger.info("Finished job %s", job_id)


async def process_email_batch(ctx: dict, batch_id: str) -> None:
    """ARQ task for the email-finder feature - see EmailJobEngine's
    docstring for how this mirrors process_job above."""
    redis: Redis = ctx["redis"]
    logger.info("Starting email batch %s", batch_id)
    engine = EmailJobEngine(SessionLocal, redis, concurrency=10)
    try:
        await engine.run(batch_id)
    except Exception:
        logger.exception("Email batch %s failed with an unhandled exception", batch_id)
        raise
    logger.info("Finished email batch %s", batch_id)


async def _reconcile_orphaned_jobs(redis: Redis) -> None:
    """Detects jobs left stuck at status="RUNNING" forever because the OS
    process that was running them was killed outright (taskkill, a crashed
    terminal, `predev.ps1` freeing a port, a manual process kill, etc.)
    instead of exiting cleanly.

    `job_run_lock:{id}` (see job_engine.py) is ALWAYS held, with a
    heartbeat-renewed short TTL, for the entire duration a real
    `JobEngine._run_locked()` is actively processing a job - so if a job's
    DB status is "RUNNING" but that lock doesn't exist, there is, with
    certainty, no live process anywhere actually working on it: either it
    already expired (the process died) or it never existed in this process's
    lifetime. Left alone, the UI shows that job as "RUNNING" forever with a
    Stop button that does nothing, because there's no live loop left to ever
    read the Redis control flag it sets.

    This only runs once per worker startup (not a background poller) - a
    fresh worker process is exactly the moment stale state from a previous,
    now-dead worker process needs to be swept up before anything new tries
    to interact with those jobs again.
    """
    async with SessionLocal() as session:
        result = await session.execute(select(Job).where(Job.status == "RUNNING"))
        running_jobs = list(result.scalars().all())
        if not running_jobs:
            return

        orphaned = []
        for job in running_jobs:
            if await redis.exists(_run_lock_key(job.id)):
                continue  # a live JobEngine somewhere genuinely still holds this
            job.status = "FAILED"
            job.error_message = (
                "Processing was interrupted (the worker process stopped unexpectedly) "
                "and never resumed. Use Resume or Retry failed to continue."
            )
            job.finished_at = datetime.now(timezone.utc)
            orphaned.append(job.id)

        if orphaned:
            await session.commit()
            logger.warning(
                "Marked %d orphaned job(s) as FAILED on worker startup (no active run lock found): %s",
                len(orphaned),
                orphaned,
            )


async def _reconcile_orphaned_email_batches(redis: Redis) -> None:
    """Same reasoning as _reconcile_orphaned_jobs above, for EmailBatch."""
    async with SessionLocal() as session:
        result = await session.execute(select(EmailBatch).where(EmailBatch.status == "RUNNING"))
        running_batches = list(result.scalars().all())
        if not running_batches:
            return

        orphaned = []
        for batch in running_batches:
            if await redis.exists(_run_lock_key(batch.id)):
                continue
            batch.status = "FAILED"
            batch.error_message = (
                "Processing was interrupted (the worker process stopped unexpectedly) "
                "and never resumed. Use Resume or Retry failed to continue."
            )
            batch.finished_at = datetime.now(timezone.utc)
            orphaned.append(batch.id)

        if orphaned:
            await session.commit()
            logger.warning(
                "Marked %d orphaned email batch(es) as FAILED on worker startup (no active run lock found): %s",
                len(orphaned),
                orphaned,
            )


async def startup(ctx: dict) -> None:
    await init_db()
    enable_usage_capture()
    settings = get_settings()
    ctx["redis"] = Redis.from_url(settings.REDIS_URL, decode_responses=False)
    await _reconcile_orphaned_jobs(ctx["redis"])
    await _reconcile_orphaned_email_batches(ctx["redis"])
    logger.info("ARQ worker started")


async def shutdown(ctx: dict) -> None:
    redis: Redis | None = ctx.get("redis")
    if redis is not None:
        await redis.aclose()
    logger.info("ARQ worker shut down")


class WorkerSettings:
    functions = [process_job, process_email_batch]
    on_startup = startup
    on_shutdown = shutdown
    redis_settings = RedisSettings.from_dsn(get_settings().REDIS_URL)
    max_jobs = 10          # up to 10 jobs (projects) processed concurrently
    job_timeout = 86400    # 24h — enough for a 200K-company job
    max_tries = 3          # auto-retry on crash; checkpoint makes retries cheap
    keep_result = 3600
