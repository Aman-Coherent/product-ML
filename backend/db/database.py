from __future__ import annotations

import sqlite3
from collections.abc import AsyncIterator

from sqlalchemy import event
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from backend.config import get_settings
from backend.db.models import Base

# `Base.metadata.create_all` (see init_db below) only creates tables for
# model classes that have actually been imported somewhere by the time it
# runs - it doesn't scan the filesystem. email_models.py lives in a
# separate module from models.py (deliberately - see its docstring), so
# without this import here, `create_all` would silently skip EmailBatch/
# EmailCompanyInput entirely unless some OTHER import happened to pull it
# in first (e.g. main.py's router import) - fragile and order-dependent.
# Importing it right here, next to Base itself, guarantees every entry
# point that calls init_db() (FastAPI app, ARQ worker, ad-hoc scripts)
# gets both table sets registered, with zero reliance on import order
# elsewhere. Confirmed this was a real gap, not a theoretical one: a
# standalone init_db() call without backend.main ever having been imported
# created every table except these two.
from backend.db import email_models  # noqa: F401

settings = get_settings()

# SQLite defaults are hostile to a job engine with 20+ concurrent async
# writers hammering CompanyInput/Job rows: the default rollback-journal mode
# locks the *whole* file for the duration of any write, and busy_timeout
# defaults to 0ms, so a second writer that shows up while another write is
# in flight fails IMMEDIATELY with "database is locked" instead of waiting.
# That crashed real job runs mid-flight (see Job.error_message history).
# WAL mode lets readers proceed concurrently with a single writer, and a
# generous busy_timeout makes writers queue/retry instead of erroring out.
_IS_SQLITE = settings.DATABASE_URL.startswith("sqlite")

engine = create_async_engine(settings.DATABASE_URL, echo=False, future=True)

if _IS_SQLITE:

    @event.listens_for(engine.sync_engine, "connect")
    def _set_sqlite_pragmas(dbapi_connection: sqlite3.Connection, _connection_record: object) -> None:
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.execute("PRAGMA busy_timeout=30000")
        cursor.execute("PRAGMA synchronous=NORMAL")
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

SessionLocal = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)


async def init_db() -> None:
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)


async def get_session() -> AsyncIterator[AsyncSession]:
    async with SessionLocal() as session:
        yield session
