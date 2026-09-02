"""
Runs one email-finding batch to completion. Structurally a simplified
sibling of backend/core/job_engine.py: same fan-out-with-semaphore shape,
same Redis-backed pause/cancel/run-lock guarantees (imported straight from
job_control.py - not reimplemented), same checkpoint-on-completion +
live SSE progress pattern. Simpler than JobEngine in two ways: there's only
one processing step (no classify/research/generate branching by mode), and
results are small enough to write directly to the DB row instead of a
separate Parquet/DuckDB layer.
"""
from __future__ import annotations

import asyncio
import contextlib
import json
import logging
from datetime import datetime, timezone

from redis.asyncio import Redis
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from backend.core.checkpoint import append_checkpoint, load_completed_ids
from backend.core.email_finder.models import EmailResult
from backend.core.email_finder.pipeline import find_company_email
from backend.core.job_control import (
    acquire_run_lock,
    get_control,
    heartbeat_run_lock,
    release_run_lock,
    request_resume,
)
from backend.core.llm_router import pick_groq_fallback_key
from backend.core.models import SSEEvent, SSEEventType
from backend.core.user_keys import load_user_keys
from backend.db.email_models import EmailBatch, EmailCompanyInput
from backend.workers.sse_publisher import publish_event

logger = logging.getLogger("email_job_engine")


class EmailJobEngine:
    def __init__(self, session_factory: async_sessionmaker[AsyncSession], redis: Redis, concurrency: int = 10):
        self.session_factory = session_factory
        self.redis = redis
        self.concurrency = concurrency
        self._counters_lock = asyncio.Lock()
        self._checkpoint_lock = asyncio.Lock()

    async def _publish(self, batch_id: str, event_type: SSEEventType, data: dict) -> None:
        # Reuses the exact same SSEEvent schema/Redis channel scheme as
        # product-generation jobs (see workers/sse_publisher.py) - it's
        # keyed purely by an opaque id string, so a batch id works exactly
        # like a job id and the frontend's existing SSE-consuming hook
        # pattern (useSSEJob) can be reused for batches with zero changes
        # to that plumbing.
        event = SSEEvent(event=event_type, job_id=batch_id, data=data)
        await publish_event(self.redis, event)

    async def run(self, batch_id: str) -> None:
        lock_token = await acquire_run_lock(self.redis, batch_id)
        if lock_token is None:
            logger.warning("Email batch %s already has an active run elsewhere; skipping.", batch_id)
            return

        heartbeat_task = asyncio.create_task(heartbeat_run_lock(self.redis, batch_id, lock_token))
        try:
            await self._run_locked(batch_id)
        except Exception as exc:
            logger.exception("Email batch %s crashed with an unhandled exception", batch_id)
            await self._mark_failed(batch_id, str(exc) or exc.__class__.__name__)
            raise
        finally:
            heartbeat_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await heartbeat_task
            await release_run_lock(self.redis, batch_id, lock_token)

    async def _mark_failed(self, batch_id: str, error: str) -> None:
        try:
            async with self.session_factory() as session:
                batch = await session.get(EmailBatch, batch_id)
                if batch is None or batch.status in ("COMPLETED", "CANCELLED"):
                    return
                batch.status = "FAILED"
                batch.error_message = error[:2000]
                batch.finished_at = datetime.now(timezone.utc)
                await session.commit()
                done, failed, total = batch.done, batch.failed, batch.total
            await self._publish(
                batch_id, SSEEventType.ERROR, {"error": error[:2000], "done": done, "failed": failed, "total": total}
            )
        except Exception:
            logger.exception("Failed to persist crash state for email batch %s", batch_id)

    async def _run_locked(self, batch_id: str) -> None:
        async with self.session_factory() as session:
            batch = await session.get(EmailBatch, batch_id)
            if batch is None:
                logger.error("Email batch %s not found", batch_id)
                return

            if batch.status == "CANCELLED":
                logger.info("Email batch %s was cancelled before it started running; skipping.", batch_id)
                return

            await request_resume(self.redis, batch_id)
            batch.status = "RUNNING"
            batch.started_at = datetime.now(timezone.utc)
            await session.commit()

            companies_result = await session.execute(
                select(EmailCompanyInput).where(
                    EmailCompanyInput.batch_id == batch.id, EmailCompanyInput.status != "done"
                )
            )
            companies = list(companies_result.scalars().all())
            concurrency = batch.concurrency or self.concurrency
            # Same "user's own key always wins over the shared system pool"
            # rule every other Groq call in this app follows (see
            # llm_router.py's module docstring) - a user who's added their
            # own Groq key in Settings must never have their website-search
            # calls silently billed against the shared system pool instead.
            user_keys = await load_user_keys(session, batch.user_id)

        await self._publish(batch_id, SSEEventType.STATUS_CHANGE, {"status": "RUNNING"})

        completed_ids = await load_completed_ids(batch_id)
        pending = [c for c in companies if c.id not in completed_ids]

        groq_api_key, _ = pick_groq_fallback_key(user_keys)

        semaphore = asyncio.Semaphore(concurrency)
        state = {"paused": False, "cancelled": False}
        tasks: list[asyncio.Task] = []

        async def _watch_control() -> None:
            while True:
                control = await get_control(self.redis, batch_id)
                if control == "cancel":
                    state["cancelled"] = True
                    for t in tasks:
                        t.cancel()
                    return
                if control == "pause":
                    state["paused"] = True
                    for t in tasks:
                        t.cancel()
                    return
                await asyncio.sleep(1)

        async def _process_one(company: EmailCompanyInput) -> None:
            try:
                async with semaphore:
                    control = await get_control(self.redis, batch_id)
                    if control == "cancel":
                        state["cancelled"] = True
                        return
                    if control == "pause":
                        state["paused"] = True
                        return

                    result: EmailResult = await find_company_email(
                        company.id,
                        company.company_name,
                        company.location,
                        company.url,
                        redis=self.redis,
                        groq_api_key=groq_api_key,
                    )

                    async with self._checkpoint_lock:
                        await append_checkpoint(
                            batch_id, company.id, "done" if result.success else "failed", result.error
                        )

                    async with self._counters_lock:
                        async with self.session_factory() as write_session:
                            batch_row, done, failed, total = await self._apply_result(
                                write_session, batch_id, company.id, result
                            )

                    await self._publish(
                        batch_id,
                        SSEEventType.COMPANY_DONE if result.success else SSEEventType.COMPANY_FAILED,
                        {
                            "company_id": company.id,
                            "company_name": company.company_name,
                            "website_source": result.website_source.value,
                            "primary_email": result.primary_email,
                            "primary_tier": result.primary_tier.value if result.primary_tier else None,
                            "error": result.error,
                        },
                    )
                    pct = round(100 * (done + failed) / total, 2) if total else 0.0
                    await self._publish(
                        batch_id, SSEEventType.PROGRESS, {"done": done, "failed": failed, "total": total, "percent": pct}
                    )
            except asyncio.CancelledError:
                return

        tasks.extend(asyncio.create_task(_process_one(c)) for c in pending)
        watcher_task = asyncio.create_task(_watch_control()) if tasks else None
        try:
            if tasks:
                await asyncio.gather(*tasks)
        finally:
            if watcher_task is not None:
                watcher_task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await watcher_task

        async with self.session_factory() as session:
            batch = await session.get(EmailBatch, batch_id)
            if state["cancelled"]:
                batch.status = "CANCELLED"
            elif state["paused"]:
                batch.status = "PAUSED"
            else:
                batch.status = "COMPLETED"
            batch.finished_at = datetime.now(timezone.utc)
            await session.commit()
            final_status, done, failed, total = batch.status, batch.done, batch.failed, batch.total

        await self._publish(
            batch_id,
            SSEEventType.COMPLETE if final_status == "COMPLETED" else SSEEventType.STATUS_CHANGE,
            {"status": final_status, "done": done, "failed": failed, "total": total},
        )

    @staticmethod
    async def _apply_result(
        session: AsyncSession, batch_id: str, company_id: str, result: EmailResult
    ) -> tuple[EmailBatch, int, int, int]:
        batch = await session.get(EmailBatch, batch_id)
        company = await session.get(EmailCompanyInput, company_id)

        company.status = "done" if result.success else "failed"
        company.resolved_url = result.resolved_url
        company.website_source = result.website_source.value
        company.primary_email = result.primary_email
        company.primary_label = result.primary_label.value if result.primary_label else None
        company.primary_tier = result.primary_tier.value if result.primary_tier else None
        company.primary_confidence = result.primary_confidence
        company.primary_source_page = result.primary_source_page
        company.alternate_emails_json = json.dumps([c.model_dump(mode="json") for c in result.alternate_emails])
        company.pages_checked_json = json.dumps(result.pages_checked)
        company.processing_time_ms = result.processing_time_ms
        company.last_batch_id = batch_id
        if not result.success:
            company.error_message = result.error
        else:
            company.error_message = result.error  # e.g. "no_email_found" - informational, not a failure

        batch.done = (batch.done or 0) + (1 if result.success else 0)
        batch.failed = (batch.failed or 0) + (0 if result.success else 1)

        await session.commit()
        return batch, batch.done, batch.failed, batch.total
