"""
Publishes job progress events to Redis pub/sub (for live SSE subscribers)
and appends them to a bounded history list (for Last-Event-ID replay when
a browser tab reconnects after being offline).
"""
from __future__ import annotations

from redis.asyncio import Redis

from backend.core.models import SSEEvent

HISTORY_TTL_SECONDS = 24 * 3600
HISTORY_MAX_LEN = 5000


def channel_name(job_id: str) -> str:
    return f"sse:{job_id}"


def history_key(job_id: str) -> str:
    return f"sse_history:{job_id}"


def seq_counter_key(job_id: str) -> str:
    return f"sse_seq:{job_id}"


async def next_seq(redis: Redis, job_id: str) -> int:
    """
    Monotonic sequence number stored in Redis (not per-process memory) so
    that resuming a paused job in a brand-new ARQ worker/JobEngine instance
    continues the same sequence instead of restarting at 1, which would
    break Last-Event-ID replay for reconnecting browser tabs.
    """
    seq = await redis.incr(seq_counter_key(job_id))
    await redis.expire(seq_counter_key(job_id), HISTORY_TTL_SECONDS)
    return int(seq)


async def publish_event(redis: Redis, event: SSEEvent) -> None:
    event.seq = await next_seq(redis, event.job_id)
    payload = event.model_dump_json()
    await redis.publish(channel_name(event.job_id), payload)

    key = history_key(event.job_id)
    await redis.rpush(key, payload)
    await redis.ltrim(key, -HISTORY_MAX_LEN, -1)
    await redis.expire(key, HISTORY_TTL_SECONDS)


async def get_events_since(redis: Redis, job_id: str, last_seq: int) -> list[SSEEvent]:
    """Replays every buffered event with seq > last_seq (used on SSE reconnect)."""
    raw_events = await redis.lrange(history_key(job_id), 0, -1)
    events: list[SSEEvent] = []
    for raw in raw_events:
        raw_str = raw.decode() if isinstance(raw, bytes) else raw
        event = SSEEvent.model_validate_json(raw_str)
        if event.seq > last_seq:
            events.append(event)
    return events
