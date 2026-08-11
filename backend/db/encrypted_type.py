"""
SQLAlchemy TypeDecorator that transparently encrypts/decrypts a string
column at rest using Fernet (AES128-CBC + HMAC). Used for user-provided
LLM API keys so a stolen SQLite file never leaks plaintext secrets.
"""
from __future__ import annotations

from cryptography.fernet import Fernet, InvalidToken
import sqlalchemy as sa

from backend.config import get_settings


def _fernet() -> Fernet:
    key = get_settings().ENCRYPTION_KEY
    if not key:
        raise RuntimeError(
            "ENCRYPTION_KEY is not set in the env file. Generate one with:\n"
            "python -c \"import base64,secrets; "
            "print(base64.urlsafe_b64encode(secrets.token_bytes(32)).decode())\""
        )
    return Fernet(key.encode())


class EncryptedString(sa.TypeDecorator):
    """Stores strings encrypted with Fernet; decrypts transparently on read."""

    impl = sa.Text
    cache_ok = True

    def process_bind_param(self, value: str | None, dialect) -> str | None:
        if value is None:
            return None
        return _fernet().encrypt(value.encode()).decode()

    def process_result_value(self, value: str | None, dialect) -> str | None:
        if value is None:
            return None
        try:
            return _fernet().decrypt(value.encode()).decode()
        except InvalidToken:
            # Value was stored before encryption was enabled, or key rotated.
            return None
