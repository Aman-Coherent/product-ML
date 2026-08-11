from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from backend.config import get_settings
from backend.core.usage_tracker import enable_usage_capture
from backend.db.database import init_db
from backend.dependencies import close_connections
from backend.routers import companies, export, jobs, projects, stream, usage, user_settings

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("main")


@asynccontextmanager
async def lifespan(app: FastAPI):
    await init_db()
    enable_usage_capture()
    logger.info("Database initialized")
    yield
    await close_connections()
    logger.info("Connections closed")


app = FastAPI(
    title="AI Product Generator API",
    description="Production backend for URL/name-driven supply-chain classification and product generation.",
    version="1.0.0",
    lifespan=lifespan,
)

settings = get_settings()
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins_list,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(projects.router)
app.include_router(jobs.router)
app.include_router(stream.router)
app.include_router(companies.router)
app.include_router(export.router)
app.include_router(user_settings.router)
app.include_router(usage.router)


@app.get("/api/health")
async def health():
    return {"status": "ok"}
