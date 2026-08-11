"""
Batches CompanyResult -> product rows and appends them to a per-project
Parquet file using Polars. Batching (every N companies) avoids the
overhead of writing a tiny Parquet file per company while still bounding
memory to one batch at a time.
"""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import polars as pl

from backend.config import DATA_DIR
from backend.core.models import CompanyResult

PROJECTS_DIR = DATA_DIR / "projects"
PROJECTS_DIR.mkdir(parents=True, exist_ok=True)

SCHEMA = {
    "job_id": pl.Utf8,
    "project_id": pl.Utf8,
    "company_id": pl.Utf8,
    "company_name": pl.Utf8,
    "location": pl.Utf8,
    "url": pl.Utf8,
    "url_read_source": pl.Utf8,
    "url_markdown_tokens": pl.Int64,
    "supply_chain_primary": pl.Utf8,
    "supply_chain_all": pl.Utf8,
    "display_label": pl.Utf8,
    "classification_confidence": pl.Float64,
    "is_multi": pl.Boolean,
    "product_name": pl.Utf8,
    "product_category": pl.Utf8,
    "created_at": pl.Utf8,
}


def project_dir(project_id: str) -> Path:
    d = PROJECTS_DIR / project_id
    d.mkdir(parents=True, exist_ok=True)
    return d


def project_parquet_glob(project_id: str) -> str:
    """DuckDB/Polars glob pattern matching every part file for a project."""
    return (project_dir(project_id) / "products_part_*.parquet").as_posix()


def clear_project_products(project_id: str) -> int:
    """Deletes every existing product part-file for a project.

    Must be called whenever a project's CompanyInput rows are replaced
    (fresh/re-upload CSV, which assigns brand new company_id values - see
    routers/projects.py upload_csv). Without this, previously-written Parquet
    rows keep referencing company_ids that no longer exist anywhere in the
    DB, permanently inflating every future stats/export query for this
    project with orphaned "ghost" companies and products that can never be
    matched back to a real row again.
    """
    deleted = 0
    for path in project_dir(project_id).glob("products_part_*.parquet"):
        path.unlink(missing_ok=True)
        deleted += 1
    return deleted


def _rows_for_result(job_id: str, project_id: str, result: CompanyResult) -> list[dict]:
    now = datetime.now(timezone.utc).isoformat()
    base = {
        "job_id": job_id,
        "project_id": project_id,
        "company_id": result.company_id,
        "company_name": result.company_name,
        "location": result.location,
        "url": result.url,
        "url_read_source": result.url_read.source.value if result.url_read else None,
        "url_markdown_tokens": result.url_read.token_estimate if result.url_read else 0,
        "supply_chain_primary": result.classification.primary_category.value if result.classification else None,
        "supply_chain_all": (
            ",".join(c.value for c in result.classification.all_categories) if result.classification else None
        ),
        "display_label": result.classification.display_label if result.classification else None,
        "classification_confidence": result.classification.confidence if result.classification else None,
        "is_multi": result.classification.is_multi if result.classification else None,
        "created_at": now,
    }

    if not result.products or not result.products.products:
        return [{**base, "product_name": None, "product_category": None}]

    return [
        {**base, "product_name": product.name, "product_category": product.category.value}
        for product in result.products.products
    ]


class ParquetBatchWriter:
    """
    Accumulates CompanyResult rows and flushes to a NEW part file every
    `batch_size` companies (e.g. products_part_000042.parquet). This avoids
    the O(n) read-modify-write-whole-file cost that a single growing
    Parquet file would incur at 200K-company scale. DuckDB/Polars queries
    read all part files via a glob pattern with zero extra code.
    """

    def __init__(self, job_id: str, project_id: str, batch_size: int = 100):
        self.job_id = job_id
        self.project_id = project_id
        self.batch_size = batch_size
        self._buffer: list[dict] = []
        self._part_index = self._next_part_index()

    def _next_part_index(self) -> int:
        existing = list(project_dir(self.project_id).glob("products_part_*.parquet"))
        if not existing:
            return 0
        indices = [int(p.stem.split("_")[-1]) for p in existing]
        return max(indices) + 1

    def add(self, result: CompanyResult) -> None:
        self._buffer.extend(_rows_for_result(self.job_id, self.project_id, result))
        if len(self._buffer) >= self.batch_size:
            self.flush()

    def flush(self) -> None:
        if not self._buffer:
            return
        df = pl.DataFrame(self._buffer, schema=SCHEMA)
        path = project_dir(self.project_id) / f"products_part_{self._part_index:06d}.parquet"
        df.write_parquet(path)
        self._part_index += 1
        self._buffer.clear()
