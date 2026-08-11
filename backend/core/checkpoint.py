"""
Crash-safe per-company checkpointing. Every completed (or permanently
failed) company is appended as one JSON line. On resume, the job engine
reads this file to know which company IDs to skip.
"""
from __future__ import annotations

import json
from pathlib import Path

import anyio

from backend.config import DATA_DIR

CHECKPOINT_DIR = DATA_DIR / "checkpoints"
CHECKPOINT_DIR.mkdir(parents=True, exist_ok=True)


def checkpoint_path(job_id: str) -> Path:
    return CHECKPOINT_DIR / f"{job_id}.jsonl"


async def append_checkpoint(job_id: str, company_id: str, status: str, error: str | None = None) -> None:
    line = json.dumps({"company_id": company_id, "status": status, "error": error}) + "\n"
    path = checkpoint_path(job_id)
    async with await anyio.open_file(path, mode="a", encoding="utf-8") as f:
        await f.write(line)


async def load_completed_ids(job_id: str) -> set[str]:
    path = checkpoint_path(job_id)
    if not path.exists():
        return set()

    completed: set[str] = set()
    async with await anyio.open_file(path, mode="r", encoding="utf-8") as f:
        async for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                record = json.loads(line)
                completed.add(record["company_id"])
            except (json.JSONDecodeError, KeyError):
                continue
    return completed


def clear_checkpoint(job_id: str) -> None:
    path = checkpoint_path(job_id)
    if path.exists():
        path.unlink()
