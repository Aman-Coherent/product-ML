"""Shared helper for loading a user's active API keys - used by every job
engine that needs to respect the "user's own key always takes priority over
the shared system pool" rule (see llm_router.py's module docstring).
Extracted from job_engine.py so EmailJobEngine follows the exact same
priority rule instead of silently defaulting to the system Groq pool."""
from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.db.models import UserApiKey


async def load_user_keys(session: AsyncSession, user_id: str) -> list[dict]:
    result = await session.execute(
        select(UserApiKey).where(UserApiKey.user_id == user_id, UserApiKey.is_active.is_(True))
    )
    keys = result.scalars().all()
    return [
        {
            "id": k.id,
            "provider": k.provider,
            "api_key": k.api_key,
            "model_name": k.model_name,
            "base_url": k.base_url,
        }
        for k in keys
    ]
