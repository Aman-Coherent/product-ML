from __future__ import annotations

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncSession

from backend.auth.jwt_verify import AuthenticatedUser, get_current_user
from backend.core.usage_tracker import get_usage_overview, get_usage_summary
from backend.db.database import get_session
from backend.dependencies import redis_dependency

router = APIRouter(prefix="/api/settings/usage", tags=["settings"])


class ModelUsageOut(BaseModel):
    tag: str
    requests_today: int
    tokens_today: int
    cached_tokens_today: int
    requests_month: int
    tokens_month: int
    limit_requests_per_day: int | None
    limit_tokens_per_day: int | None
    remaining_requests_today: int | None
    remaining_tokens_today: int | None
    live_remaining_tokens: int | None
    live_limit_tokens: int | None
    live_reset_tokens_s: float | None
    last_used_at: str | None


class KeyUsageOut(BaseModel):
    key_ref: str
    provider: str
    label: str
    masked_key: str
    is_system: bool
    requests_today: int
    tokens_today: int
    requests_month: int
    tokens_month: int
    last_used_at: str | None
    models: list[ModelUsageOut]


class ProviderUsageSummaryOut(BaseModel):
    provider: str
    key_count: int
    requests_today: int
    tokens_today: int
    requests_month: int
    tokens_month: int


@router.get("", response_model=list[KeyUsageOut])
async def usage_overview(
    user: AuthenticatedUser = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
    redis: Redis = Depends(redis_dependency),
):
    return await get_usage_overview(redis, session, user.id)


@router.get("/summary", response_model=list[ProviderUsageSummaryOut])
async def usage_summary(
    user: AuthenticatedUser = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
    redis: Redis = Depends(redis_dependency),
):
    items = await get_usage_overview(redis, session, user.id)
    return get_usage_summary(items)
