"""
Generates candidate company-level local-parts to try against a
DNS-validated domain, ordered by how likely each one is to be a real,
actively-monitored company inbox. This is only reached when real scraping
(crawler.py + extractor.py) found nothing - see pipeline.py.
"""
from __future__ import annotations

from backend.core.email_finder.models import EmailLabel

# (local_part, label) - ordered most-likely-to-exist first. Kept to company-
# level generic inboxes only (never firstname.lastname@ guesses) since this
# module has no idea who works there - guessing a *person's* email would be
# pure hallucination with zero grounding, which is exactly what this whole
# feature is designed to avoid.
CANDIDATE_LOCAL_PARTS: list[tuple[str, EmailLabel]] = [
    ("info", EmailLabel.GENERAL),
    ("contact", EmailLabel.GENERAL),
    ("hello", EmailLabel.GENERAL),
    ("sales", EmailLabel.SALES),
    ("support", EmailLabel.SUPPORT),
    ("enquiries", EmailLabel.GENERAL),
    ("enquiry", EmailLabel.GENERAL),
    ("office", EmailLabel.GENERAL),
    ("admin", EmailLabel.GENERAL),
    ("mail", EmailLabel.GENERAL),
    ("business", EmailLabel.SALES),
]


def generate_candidates(domain: str, limit: int = 6) -> list[tuple[str, EmailLabel]]:
    return [(f"{local}@{domain}", label) for local, label in CANDIDATE_LOCAL_PARTS[:limit]]
