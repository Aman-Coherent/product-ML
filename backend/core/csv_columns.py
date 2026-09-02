"""
Fuzzy CSV header detection, shared by routers/projects.py (product
generation) and routers/email_finder.py (email finder) - both need to turn
an arbitrary real-world company-list CSV into (company_name, url, location)
columns, and drift between two independently-maintained copies of this
logic would mean the two features silently disagree on which column is
"the" company name in the exact same file.

Extracted verbatim from the original routers/projects.py implementation.
"""
from __future__ import annotations

import re

# Real-world company lists rarely use our exact internal field names, so we
# fuzzy-match a wide range of common header variants instead of requiring an
# exact "company_name" / "location" / "url" header. Order matters: aliases
# are tried in this priority order for exact matches first, then substring
# matches, so the most specific/common variant wins over a looser guess.
COMPANY_NAME_ALIASES = [
    "company_name", "companyname", "company", "business_name", "businessname",
    "business", "organization_name", "organisation_name", "organization",
    "organisation", "org_name", "orgname", "supplier_name", "suppliername",
    "supplier", "firm_name", "firmname", "firm", "vendor_name", "vendorname",
    "vendor", "manufacturer_name", "manufacturer", "client_name", "clientname",
    "client", "account_name", "accountname", "entity_name", "name",
]
URL_ALIASES = [
    "url", "website", "website_url", "web_site", "site", "domain", "homepage",
    "home_page", "web_page", "webpage", "link", "company_url", "company_website",
    "web_address", "www",
]
LOCATION_ALIASES = [
    "location", "address", "full_address", "fulladdress", "mailing_address",
    "hq_location", "headquarters", "hq", "city", "town", "state", "province",
    "region", "country", "location_name", "city_country",
]

# A column whose normalized name contains one of these tokens holds
# metadata ABOUT the data (a count, id, flag, ...) rather than the data
# itself — even if it also happens to contain an alias as a substring. A
# real-world export can easily have a "location_count" or "location_entries"
# column sitting right next to "main_city"/"main_state"/"main_country"; the
# loose `alias in norm` substring check below would otherwise happily fold
# that integer into the combined location string (e.g. "Paris, France, 1").
# This only guards the fuzzy substring fallback — an exact alias match is
# still always accepted, since a column literally named e.g. "location" is
# unambiguous regardless.
_METADATA_TOKENS = {
    "count", "counts", "id", "ids", "num", "number", "numbers", "total",
    "totals", "qty", "quantity", "flag", "flags", "entries", "entry",
    "index", "idx", "amount", "amounts", "size", "length", "len",
}


def normalize_header(header: str) -> str:
    header = header.strip().lower()
    header = re.sub(r"[^a-z0-9]+", "_", header)
    return header.strip("_")


def _is_metadata_column(norm: str) -> bool:
    return any(token in _METADATA_TOKENS for token in norm.split("_"))


def resolve_columns(fieldnames: list[str]) -> tuple[str | None, str | None, list[str]]:
    """
    Maps a CSV's actual headers onto (company_name_column, url_column,
    location_columns). `location_columns` can be multiple headers (e.g.
    separate City/State/Country columns) which get combined into one string
    per row, since that's how most real-world exports are structured.
    """
    pool = [(h, normalize_header(h)) for h in fieldnames]

    def take_exact(aliases: list[str]) -> str | None:
        for alias in aliases:
            for i, (orig, norm) in enumerate(pool):
                if norm == alias:
                    return pool.pop(i)[0]
        return None

    def take_fuzzy(aliases: list[str]) -> str | None:
        for alias in aliases:
            for i, (orig, norm) in enumerate(pool):
                if norm and not _is_metadata_column(norm) and (alias in norm or norm in alias):
                    return pool.pop(i)[0]
        return None

    company_col = take_exact(COMPANY_NAME_ALIASES) or take_fuzzy(COMPANY_NAME_ALIASES)
    url_col = take_exact(URL_ALIASES) or take_fuzzy(URL_ALIASES)

    location_cols: list[str] = []
    for alias in LOCATION_ALIASES:
        for i in range(len(pool) - 1, -1, -1):
            orig, norm = pool[i]
            if not norm:
                continue
            is_exact = norm == alias
            is_fuzzy = not _is_metadata_column(norm) and (alias in norm or norm in alias)
            if is_exact or is_fuzzy:
                location_cols.append(orig)
                pool.pop(i)

    # Re-sort to match the CSV's original left-to-right column order so a
    # combined "City, State, Country" string reads naturally.
    location_cols = [h for h in fieldnames if h in location_cols]

    return company_col, url_col, location_cols
