"""
Verifies the short-lived HS256 JWT that the Next.js frontend mints (signed
with the shared NEXTAUTH_SECRET) and forwards as `Authorization: Bearer
<token>` on every backend request. This keeps the backend stateless with
respect to auth — it never talks to Auth.js/Prisma directly, it just
trusts a token signed with a secret only the frontend and backend share.

On first sight of a given user id, a corresponding row is created in the
backend's own `users` table so projects/jobs can be scoped with a normal
foreign key.
"""
from __future__ import annotations

import jwt
from fastapi import Depends, HTTPException, Query, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.ext.asyncio import AsyncSession

from backend.config import get_settings
from backend.db.database import get_session
from backend.db.models import User

_bearer = HTTPBearer(auto_error=False)


def _decode(token: str, settings) -> dict:
    return jwt.decode(token, settings.NEXTAUTH_SECRET, algorithms=["HS256"])


class AuthenticatedUser:
    def __init__(self, id: str, email: str, name: str | None):
        self.id = id
        self.email = email
        self.name = name


async def get_current_user(
    credentials: HTTPAuthorizationCredentials | None = Depends(_bearer),
    token_qs: str | None = Query(default=None, alias="token"),
    session: AsyncSession = Depends(get_session),
) -> AuthenticatedUser:
    """
    Accepts the JWT either as `Authorization: Bearer <token>` (normal REST
    calls) or as a `?token=` query parameter (needed for the SSE stream
    endpoint, since the native browser EventSource API cannot set custom
    headers).
    """
    settings = get_settings()
    if not settings.NEXTAUTH_SECRET:
        raise HTTPException(status.HTTP_500_INTERNAL_SERVER_ERROR, "Server auth secret not configured")

    raw_token = credentials.credentials if credentials else token_qs
    if not raw_token:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Missing Authorization header or token")

    try:
        payload = _decode(raw_token, settings)
    except jwt.ExpiredSignatureError:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Session expired, please sign in again")
    except jwt.InvalidTokenError:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid session token")

    user_id = payload.get("sub")
    email = payload.get("email")
    name = payload.get("name")
    if not user_id or not email:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Malformed session token")

    user = await session.get(User, user_id)
    if user is None:
        user = User(id=user_id, email=email, name=name)
        session.add(user)
        await session.commit()

    return AuthenticatedUser(id=user_id, email=email, name=name)
