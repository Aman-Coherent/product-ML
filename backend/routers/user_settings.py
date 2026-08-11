from __future__ import annotations

import re
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.auth.jwt_verify import AuthenticatedUser, get_current_user
from backend.db.database import get_session
from backend.db.models import UserApiKey

router = APIRouter(prefix="/api/settings/keys", tags=["settings"])

VALID_PROVIDERS = {"groq", "mistral", "jina", "claude", "openai", "custom"}

_KEY_SPLIT_RE = re.compile(r"[,\n]+")


def _split_keys(raw: str) -> list[str]:
    """Splits a pasted comma- and/or newline-separated blob of keys (the
    same format used for GROQ_API_KEYS/MISTRAL_API_KEY in the system env
    file) into a de-duplicated, order-preserving list of individual keys."""
    seen: set[str] = set()
    result: list[str] = []
    for part in _KEY_SPLIT_RE.split(raw):
        key = part.strip()
        if key and key not in seen:
            seen.add(key)
            result.append(key)
    return result


def _mask(key: str) -> str:
    if len(key) <= 8:
        return "●" * len(key)
    return f"{key[:4]}{'●' * 8}{key[-4:]}"


class ApiKeyOut(BaseModel):
    id: str
    provider: str
    label: str
    masked_key: str
    model_name: str | None
    base_url: str | None
    is_active: bool
    created_at: datetime


class CreateApiKeyRequest(BaseModel):
    provider: str
    label: str
    api_key: str
    model_name: str | None = None
    base_url: str | None = None


class CreateApiKeyBulkRequest(BaseModel):
    provider: str
    label: str
    api_keys: str  # comma and/or newline separated; one or many keys
    model_name: str | None = None
    base_url: str | None = None


@router.get("", response_model=list[ApiKeyOut])
async def list_keys(
    user: AuthenticatedUser = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
):
    result = await session.execute(select(UserApiKey).where(UserApiKey.user_id == user.id))
    keys = result.scalars().all()
    return [
        ApiKeyOut(
            id=k.id,
            provider=k.provider,
            label=k.label,
            masked_key=_mask(k.api_key),
            model_name=k.model_name,
            base_url=k.base_url,
            is_active=k.is_active,
            created_at=k.created_at,
        )
        for k in keys
    ]


@router.post("", response_model=ApiKeyOut)
async def add_key(
    body: CreateApiKeyRequest,
    user: AuthenticatedUser = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
):
    if body.provider not in VALID_PROVIDERS:
        raise HTTPException(400, f"provider must be one of: {', '.join(sorted(VALID_PROVIDERS))}")
    if not body.api_key.strip():
        raise HTTPException(400, "api_key cannot be empty")

    key = UserApiKey(
        user_id=user.id,
        provider=body.provider,
        label=body.label,
        api_key=body.api_key.strip(),
        model_name=body.model_name,
        base_url=body.base_url,
    )
    session.add(key)
    await session.commit()
    await session.refresh(key)

    return ApiKeyOut(
        id=key.id,
        provider=key.provider,
        label=key.label,
        masked_key=_mask(body.api_key),
        model_name=key.model_name,
        base_url=key.base_url,
        is_active=key.is_active,
        created_at=key.created_at,
    )


@router.post("/bulk", response_model=list[ApiKeyOut])
async def add_keys_bulk(
    body: CreateApiKeyBulkRequest,
    user: AuthenticatedUser = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
):
    """
    Accepts one label plus one-or-more comma/newline-separated API keys
    (mirroring the system's own GROQ_API_KEYS/MISTRAL_API_KEY rotation
    format). A single key keeps the label exactly as typed; multiple keys
    are auto-numbered "<label> 1", "<label> 2", ... so a user can paste an
    entire batch of keys at once instead of adding them one by one.
    """
    if body.provider not in VALID_PROVIDERS:
        raise HTTPException(400, f"provider must be one of: {', '.join(sorted(VALID_PROVIDERS))}")

    keys = _split_keys(body.api_keys)
    if not keys:
        raise HTTPException(400, "No API key(s) provided")

    base_label = body.label.strip() or body.provider.capitalize()

    existing_result = await session.execute(
        select(UserApiKey.label).where(UserApiKey.user_id == user.id, UserApiKey.provider == body.provider)
    )
    existing_labels = {row[0] for row in existing_result.all()}

    if len(keys) == 1 and base_label in existing_labels:
        raise HTTPException(400, f"A key labeled '{base_label}' already exists for {body.provider}")

    created: list[UserApiKey] = []
    for raw_key in keys:
        if len(keys) == 1:
            label = base_label
        else:
            n = 1
            label = f"{base_label} {n}"
            while label in existing_labels:
                n += 1
                label = f"{base_label} {n}"
        existing_labels.add(label)

        created.append(
            UserApiKey(
                user_id=user.id,
                provider=body.provider,
                label=label,
                api_key=raw_key,
                model_name=body.model_name,
                base_url=body.base_url,
            )
        )

    session.add_all(created)
    await session.commit()
    for k in created:
        await session.refresh(k)

    return [
        ApiKeyOut(
            id=k.id,
            provider=k.provider,
            label=k.label,
            masked_key=_mask(k.api_key),
            model_name=k.model_name,
            base_url=k.base_url,
            is_active=k.is_active,
            created_at=k.created_at,
        )
        for k in created
    ]


@router.patch("/{key_id}/toggle")
async def toggle_key(
    key_id: str,
    user: AuthenticatedUser = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
):
    key = await session.get(UserApiKey, key_id)
    if key is None or key.user_id != user.id:
        raise HTTPException(404, "Key not found")
    key.is_active = not key.is_active
    await session.commit()
    return {"id": key.id, "is_active": key.is_active}


@router.delete("/{key_id}")
async def delete_key(
    key_id: str,
    user: AuthenticatedUser = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
):
    key = await session.get(UserApiKey, key_id)
    if key is None or key.user_id != user.id:
        raise HTTPException(404, "Key not found")
    await session.delete(key)
    await session.commit()
    return {"status": "deleted"}
