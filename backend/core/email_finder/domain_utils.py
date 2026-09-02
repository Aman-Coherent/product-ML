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


def organization_name(domain: str) -> str:
    """The brand/SLD portion only, e.g. "bosch" for both "bosch.com" and
    "bosch.de". Used to recognize a multinational's own country-market
    site (a different TLD, same company) as still fully trustworthy,
    rather than exact-domain-matching them into a lower "offsite" tier.

    Confirmed live why this matters: bosch.com's US-localized contact page
    has zero visible emails (pure support-ticket form), while bosch.de's
    legally-mandated Impressum has a real one (kontakt@bosch.de) - treating
    that as "off-domain, less trustworthy" just because ".de" != ".com"
    would undersell a result that's actually Bosch's own official page.
    Good enough for this comparison specifically (it doesn't need to be a
    full public-suffix-list-correct split for anything else) - two
    unrelated companies coincidentally sharing an exact brand-name string
    across different TLDs is vanishingly rare, and even if it happened the
    cost is a slightly-too-generous confidence label, not a wrong email."""
    return registrable_domain(domain).split(".")[0]


def same_organization(domain_a: str, domain_b: str) -> bool:
    a, b = organization_name(domain_a), organization_name(domain_b)
    return bool(a) and a == b
