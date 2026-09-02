"""
Redis-backed run-lock + pause/cancel control flags, shared by every
background-job engine in this app (JobEngine for product generation,
EmailJobEngine for the email finder). Everything here is generic over a
plain string run id - it has no knowledge of what table that id belongs to
- so a second job engine can reuse the exact same crash-safety guarantees
without duplicating them.

Extracted from job_engine.py, which was the original (and, before the
email finder, only) caller - see its module docstring for the job-run
lifecycle this plugs into.
"""
from __future__ import annotations

import asyncio
import contextlib
import logging
import uuid

from redis.asyncio import Redis

logger = logging.getLogger("job_control")

RUN_LOCK_TTL_SECONDS = 90
RUN_LOCK_HEARTBEAT_SECONDS = 30


def control_key(run_id: str) -> str:
    return f"job_control:{run_id}"  # "running" | "pause" | "cancel"


def run_lock_key(run_id: str) -> str:
    return f"job_run_lock:{run_id}"


async def acquire_run_lock(redis: Redis, run_id: str) -> str | None:
    """Prevents two concurrent engine runs for the same run_id (e.g. an ARQ
    auto-redelivery overlapping a manually re-enqueued resume). Uses a
    short, heartbeat-renewed TTL rather than one long enough to cover a
    whole run: if this process is hard-killed, the lock frees itself within
    RUN_LOCK_TTL_SECONDS instead of blocking every future resume attempt."""
    token = uuid.uuid4().hex
    acquired = await redis.set(run_lock_key(run_id), token, nx=True, ex=RUN_LOCK_TTL_SECONDS)
    return token if acquired else None


async def release_run_lock(redis: Redis, run_id: str, token: str) -> None:
    lua = """
    if redis.call("get", KEYS[1]) == ARGV[1] then
        return redis.call("del", KEYS[1])
    end
    return 0
    """
    try:
        await redis.eval(lua, 1, run_lock_key(run_id), token)
    except Exception:
        logger.warning("Failed to release run lock for %s", run_id, exc_info=True)


async def heartbeat_run_lock(redis: Redis, run_id: str, token: str) -> None:
    """Runs alongside the job, periodically extending the lock TTL so a
    long-running job never has its own lock expire out from under it."""
    try:
        while True:
            await asyncio.sleep(RUN_LOCK_HEARTBEAT_SECONDS)
            lua = """
            if redis.call("get", KEYS[1]) == ARGV[1] then
                return redis.call("expire", KEYS[1], ARGV[2])
            end
            return 0
            """
            await redis.eval(lua, 1, run_lock_key(run_id), token, RUN_LOCK_TTL_SECONDS)
    except asyncio.CancelledError:
        raise
    except Exception:
        logger.warning("Run-lock heartbeat failed for %s", run_id, exc_info=True)


@contextlib.asynccontextmanager
async def run_lock_guard(redis: Redis, run_id: str):
    """Convenience wrapper: acquires the lock + starts the heartbeat, yields
    True/False (whether the lock was actually acquired), and always cleans
    up on exit. Callers that fail to acquire should skip their run entirely
    (see JobEngine.run / EmailJobEngine.run for the "already running
    elsewhere" log-and-return pattern)."""
    token = await acquire_run_lock(redis, run_id)
    if token is None:
        yield False
        return

    heartbeat_task = asyncio.create_task(heartbeat_run_lock(redis, run_id, token))
    try:
        yield True
    finally:
        heartbeat_task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await heartbeat_task
        await release_run_lock(redis, run_id, token)


async def request_pause(redis: Redis, run_id: str) -> None:
    await redis.set(control_key(run_id), "pause")


async def request_cancel(redis: Redis, run_id: str) -> None:
    await redis.set(control_key(run_id), "cancel")


async def request_resume(redis: Redis, run_id: str) -> None:
    await redis.set(control_key(run_id), "running")


async def get_control(redis: Redis, run_id: str) -> str:
    value = await redis.get(control_key(run_id))
    return value.decode() if isinstance(value, bytes) else (value or "running")
