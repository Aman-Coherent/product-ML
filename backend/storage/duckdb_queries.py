"""
Analytical queries over a project's Parquet part-files using DuckDB.
Used for: per-company product listing, export (CSV/Excel/JSON), and
aggregate stats. DuckDB pushes filters/columns down into Parquet, so this
stays fast even at 10M+ rows.
"""
from __future__ import annotations

from pathlib import Path
from typing import Literal

import duckdb

from backend.storage.parquet_writer import project_dir, project_parquet_glob


def _has_parquet_files(project_id: str) -> bool:
    return any(project_dir(project_id).glob("products_part_*.parquet"))


def get_products_for_company(project_id: str, company_id: str) -> list[dict]:
    if not _has_parquet_files(project_id):
        return []
    glob = project_parquet_glob(project_id)
    query = f"""
        SELECT product_name, product_category
        FROM read_parquet('{glob}')
        WHERE company_id = ? AND product_name IS NOT NULL
        ORDER BY product_name
    """
    con = duckdb.connect(":memory:")
    result = con.execute(query, [company_id]).fetchall()
    cols = ["name", "category"]
    return [dict(zip(cols, row)) for row in result]


def export_project(
    project_id: str,
    job_id: str | None,
    out_path: str,
    fmt: Literal["csv", "json"] = "csv",
) -> Path:
    """Streams the full product set for a project (optionally scoped to one
    job) out to disk using DuckDB's native COPY, which never materializes
    the whole result set in Python memory."""
    if not _has_parquet_files(project_id):
        Path(out_path).write_text("")
        return Path(out_path)

    glob = project_parquet_glob(project_id)
    con = duckdb.connect(":memory:")

    where_clause = "WHERE job_id = ?" if job_id else ""
    params = [job_id] if job_id else []

    base_query = f"""
        SELECT company_name, location, url, url_read_source,
               display_label, supply_chain_primary, supply_chain_all,
               classification_confidence, is_multi,
               product_name, product_category
        FROM read_parquet('{glob}')
        {where_clause}
        ORDER BY company_name
    """

    out = Path(out_path)
    out.parent.mkdir(parents=True, exist_ok=True)

    if fmt == "csv":
        copy_sql = f"COPY ({base_query}) TO '{out.as_posix()}' (HEADER, DELIMITER ',')"
    else:
        copy_sql = f"COPY ({base_query}) TO '{out.as_posix()}' (FORMAT JSON, ARRAY true)"

    con.execute(copy_sql, params)
    return out


def get_stats(project_id: str, job_id: str | None = None) -> dict:
    if not _has_parquet_files(project_id):
        return {"total_companies": 0, "total_products": 0, "by_category": {}}

    glob = project_parquet_glob(project_id)
    con = duckdb.connect(":memory:")
    where_clause = "WHERE job_id = ?" if job_id else ""
    params = [job_id] if job_id else []

    total_companies = con.execute(
        f"SELECT COUNT(DISTINCT company_id) FROM read_parquet('{glob}') {where_clause}", params
    ).fetchone()[0]
    total_products = con.execute(
        f"SELECT COUNT(*) FROM read_parquet('{glob}') {where_clause} AND product_name IS NOT NULL"
        if where_clause
        else f"SELECT COUNT(*) FROM read_parquet('{glob}') WHERE product_name IS NOT NULL",
        params,
    ).fetchone()[0]
    by_category_rows = con.execute(
        f"""
        SELECT supply_chain_primary, COUNT(DISTINCT company_id)
        FROM read_parquet('{glob}') {where_clause}
        GROUP BY supply_chain_primary
        """,
        params,
    ).fetchall()

    return {
        "total_companies": total_companies,
        "total_products": total_products,
        "by_category": {row[0]: row[1] for row in by_category_rows if row[0]},
    }
