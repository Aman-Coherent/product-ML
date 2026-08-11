"""
Central application settings, loaded from the shared `env` file at the
repository root (one level above `backend/`).
"""
from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

ROOT_DIR = Path(__file__).resolve().parent.parent
ENV_FILE = ROOT_DIR / "env"
DATA_DIR = Path(__file__).resolve().parent / "data"
DATA_DIR.mkdir(parents=True, exist_ok=True)
(DATA_DIR / "checkpoints").mkdir(parents=True, exist_ok=True)
(DATA_DIR / "projects").mkdir(parents=True, exist_ok=True)


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=str(ENV_FILE), extra="ignore")

    # LLM keys (comma-separated, rotated across requests)
    GROQ_API_KEYS: str = ""
    MISTRAL_API_KEY: str = ""
    JINA_API_KEY: str = ""

    # Infra
    REDIS_URL: str = "redis://localhost:6379/0"
    DATABASE_URL: str = f"sqlite+aiosqlite:///{(DATA_DIR / 'app.db').as_posix()}"
    ENCRYPTION_KEY: str = ""

    # Auth
    NEXTAUTH_SECRET: str = ""
    NEXTAUTH_URL: str = "http://localhost:3000"

    # OAuth (used only by the frontend, kept here for completeness/validation)
    GOOGLE_CLIENT_ID: str = ""
    GOOGLE_CLIENT_SECRET: str = ""
    GITHUB_CLIENT_ID: str = ""
    GITHUB_CLIENT_SECRET: str = ""

    BACKEND_URL: str = "http://localhost:8000"
    CORS_ORIGINS: str = "http://localhost:3000"

    @field_validator("DATABASE_URL")
    @classmethod
    def _resolve_relative_sqlite_path(cls, v: str) -> str:
        """Relative sqlite paths (e.g. `sqlite+aiosqlite:///./data/app.db`) must
        resolve against the repo root, not the process's CWD, otherwise the
        engine fails with 'unable to open database file' when the server is
        started from a different directory."""
        prefix = "sqlite+aiosqlite:///"
        if v.startswith(prefix) and not v.startswith(f"{prefix}/"):
            rel = v[len(prefix):]
            if not Path(rel).is_absolute():
                abs_path = (ROOT_DIR / rel).resolve()
                abs_path.parent.mkdir(parents=True, exist_ok=True)
                return f"{prefix}{abs_path.as_posix()}"
        return v

    @property
    def groq_keys(self) -> list[str]:
        return [k.strip() for k in self.GROQ_API_KEYS.split(",") if k.strip()]

    @property
    def mistral_keys(self) -> list[str]:
        return [k.strip() for k in self.MISTRAL_API_KEY.split(",") if k.strip()]

    @property
    def cors_origins_list(self) -> list[str]:
        return [o.strip() for o in self.CORS_ORIGINS.split(",") if o.strip()]


@lru_cache
def get_settings() -> Settings:
    return Settings()
