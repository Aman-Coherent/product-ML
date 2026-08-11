from __future__ import annotations

import json

from fastapi import APIRouter, Depends, Header, Request
from redis.asyncio import Redis
from sse_starlette.sse import EventSourceResponse

from backend.auth.jwt_verify import AuthenticatedUser, get_current_user
from backend.dependencies import redis_dependency
from backend.workers.sse_publisher import channel_name, get_events_since

router = APIRouter(prefix="/api/stream", tags=["stream"])


@router.get("/{job_id}")
async def stream_job(
    job_id: str,
    request: Request,
    last_event_id_header: str | None = Header(default=None, alias="Last-Event-ID"),
    last_event_id: str | None = None,  # query param fallback — native EventSource can't set custom headers
    user: AuthenticatedUser = Depends(get_current_user),
    redis: Redis = Depends(redis_dependency),
):
    async def event_generator():
        effective_last_id = last_event_id_header or last_event_id
        last_seq = int(effective_last_id) if effective_last_id and effective_last_id.isdigit() else 0

        # Replay anything missed while the browser tab was disconnected.
        missed = await get_events_since(redis, job_id, last_seq)
        for event in missed:
            yield {"id": str(event.seq), "event": event.event.value, "data": event.model_dump_json()}

        pubsub = redis.pubsub()
        await pubsub.subscribe(channel_name(job_id))
        try:
            while True:
                if await request.is_disconnected():
                    break
                message = await pubsub.get_message(ignore_subscribe_messages=True, timeout=15.0)
                if message is None:
                    yield {"event": "heartbeat", "data": "{}"}
                    continue
                raw = message["data"]
                raw_str = raw.decode() if isinstance(raw, bytes) else raw
                parsed = json.loads(raw_str)
                yield {"id": str(parsed.get("seq", 0)), "event": parsed.get("event", "message"), "data": raw_str}
        finally:
            await pubsub.unsubscribe(channel_name(job_id))
            await pubsub.aclose()

    return EventSourceResponse(event_generator())
