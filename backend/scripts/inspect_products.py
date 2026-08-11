"""One-off inspection script: sample generated product names from a project's
Parquet output for manual quality review. Not part of the app; safe to delete."""
import sys

import duckdb

project_id = sys.argv[1] if len(sys.argv) > 1 else "1eb59ebf2aa84173835c97c0a072336b"
glob = f"backend/data/projects/{project_id}/products_part_*.parquet"

con = duckdb.connect()

total = con.execute(f"SELECT count(*) FROM read_parquet('{glob}')").fetchone()[0]
distinct_companies = con.execute(f"SELECT count(DISTINCT company_id) FROM read_parquet('{glob}')").fetchone()[0]
null_rows = con.execute(f"SELECT count(*) FROM read_parquet('{glob}') WHERE product_name IS NULL").fetchone()[0]

print(f"total product rows: {total}")
print(f"distinct companies: {distinct_companies}")
print(f"rows with NULL product_name (failed/empty companies): {null_rows}")
print()

print("--- companies with product counts ---")
rows = con.execute(
    f"""
    SELECT company_name, url, supply_chain_primary, count(*) as n
    FROM read_parquet('{glob}')
    WHERE product_name IS NOT NULL
    GROUP BY company_name, url, supply_chain_primary
    ORDER BY company_name
    LIMIT 40
    """
).fetchall()
for r in rows:
    name = (r[0] or "").encode("ascii", "replace").decode()
    url = (r[1] or "").encode("ascii", "replace").decode()
    print(f"{name} | {url} | {r[2]} | {r[3]} products")

print()
print("--- full product list for first 8 companies ---")
companies = con.execute(
    f"""
    SELECT DISTINCT company_name FROM read_parquet('{glob}')
    WHERE product_name IS NOT NULL ORDER BY company_name LIMIT 8
    """
).fetchall()
for (cname,) in companies:
    print(f"\n== {cname.encode('ascii','replace').decode()} ==")
    prods = con.execute(
        f"""
        SELECT product_name, product_category FROM read_parquet('{glob}')
        WHERE company_name = ? AND product_name IS NOT NULL
        """,
        [cname],
    ).fetchall()
    for p in prods:
        pname = (p[0] or "").encode("ascii", "replace").decode()
        print(f"  - {pname}  [{p[1]}]")

print()
print("--- suspicious patterns ---")
susp = con.execute(
    f"""
    SELECT product_name, count(*) as n
    FROM read_parquet('{glob}')
    WHERE product_name IS NOT NULL
    GROUP BY product_name
    HAVING count(*) > 3
    ORDER BY n DESC
    LIMIT 20
    """
).fetchall()
for r in susp:
    pname = (r[0] or "").encode("ascii", "replace").decode()
    print(f"  '{pname}' appears {r[1]} times across different companies")

print()
print('--- companies producing Paracetamol 500mg Tablets, with url_read_source ---')
rows2 = con.execute(f'''SELECT company_name, url, url_read_source, supply_chain_primary FROM read_parquet('{glob}') WHERE product_name = 'Paracetamol 500mg Tablets' LIMIT 15''').fetchall()
for r in rows2:
    print([str(x).encode('ascii','replace').decode() if x else x for x in r])
