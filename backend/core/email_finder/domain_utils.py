"""Tiny shared helper - split out so extractor.py, crawler.py, and
pipeline.py all agree on what "the domain" of a URL/email means (in
particular, always stripping a leading "www.") instead of each rolling
its own slightly-different version. A previous version of this drifted:
pipeline.py fed a raw netloc (including "www.") straight into the
pattern-guesser, which then generated nonsense addresses like
"info@www.caterpillar.com"."""
from __future__ import annotations

from urllib.parse import urlparse


def registrable_domain(url_or_domain: str) -> str:
    if "://" not in url_or_domain:
        url_or_domain = f"//{url_or_domain}"
    netloc = urlparse(url_or_domain, scheme="https").netloc or url_or_domain
    host = netloc.split(":")[0].lower()
    return host[4:] if host.startswith("www.") else host
