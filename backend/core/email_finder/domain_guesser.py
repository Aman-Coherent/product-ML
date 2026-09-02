"""
Turns a company name (+ optional location) into a ranked list of candidate
domain names, in the same spirit as the "guess the domain" step every
commercial email-finder tool (Hunter.io, Clearbit Connect, RocketReach)
does when it has no known URL. This ONLY generates candidates - nothing
here is trusted until dns_utils confirms the domain actually resolves and
crawler.py either finds a real email on it or smtp_verifier confirms a
guessed address, so a wrong guess here just costs one cheap DNS lookup.
"""
from __future__ import annotations

import re

# Pure legal-entity suffixes only - NOT generic business words like
# "Technologies"/"Systems"/"Solutions", which are frequently part of the
# actual brand (e.g. "Acme Solutions" really is acmesolutions.com, not
# acme.com) and stripping them would guess the wrong domain far more often
# than it fixes one.
_LEGAL_SUFFIXES = [
    "incorporated", "inc", "llc", "l.l.c", "ltd", "limited", "plc", "llp",
    "corporation", "corp", "co", "company", "gmbh", "ag", "sa", "sas",
    "s.a", "pvt ltd", "pvt. ltd", "private limited", "pty ltd", "pty. ltd",
    "group", "holdings", "enterprises", "industries", "international", "intl",
    # German legal forms - previously missing entirely, which mattered: a
    # real 50-company German test run showed names like "Henkel AG & Co.
    # KGaA" or "Rittal GmbH & Co. KG" guessing garbage domains (e.g.
    # "henkelagcokgaa.com") because none of these tokens were recognized.
    # Compound forms listed explicitly (so they strip in one pass as a
    # whole unit) alongside the single-word forms they're built from (so
    # e.g. a bare "... KG" or "... SE" on its own still strips correctly).
    "gmbh & co kg", "gmbh & co kgaa", "ag & co kgaa", "ag & co kg",
    "se & co kg", "se & co kgaa", "se + co kg", "se + co kgaa",
    "kgaa", "kg", "ohg", "mbh", "se", "e.v",
]

# Ordered roughly by global usage share; ccTLDs are only tried when the
# location string gives a strong signal (see cctld_for_location).
_GENERIC_TLDS = ["com", "co", "net", "org", "io"]

# Deliberately small and high-confidence only - a wrong ccTLD guess is a
# wasted DNS lookup, but an overly aggressive/ambiguous mapping (e.g.
# matching "Georgia" the US state to the country) actively produces wrong
# guesses. Keyed on lowercase substrings checked against the whole location
# string, most-specific first (e.g. "united kingdom" before a bare "uk").
_CCTLD_MAP: list[tuple[str, str]] = [
    ("united kingdom", "co.uk"), ("uk", "co.uk"), ("england", "co.uk"),
    ("scotland", "co.uk"), ("wales", "co.uk"),
    ("india", "in"),
    ("australia", "com.au"),
    ("canada", "ca"),
    ("germany", "de"),
    ("france", "fr"),
    ("china", "cn"),
    ("japan", "co.jp"),
    ("brazil", "com.br"),
    ("singapore", "sg"),
    ("united arab emirates", "ae"), ("dubai", "ae"), ("uae", "ae"),
    ("south africa", "co.za"),
    ("netherlands", "nl"),
    ("spain", "es"),
    ("italy", "it"),
    ("mexico", "mx"),
    ("switzerland", "ch"),
    ("sweden", "se"),
    ("new zealand", "co.nz"),
]


def _strip_legal_suffix(words: list[str]) -> list[str]:
    """Drops a trailing legal-entity suffix (possibly multi-word, e.g.
    "Pvt Ltd") if the name ends with one. Only ever strips from the end -
    a suffix-like word in the middle of a name is presumably part of the
    actual brand.

    Loops (bounded to 2 passes) rather than stopping after one strip,
    because German company names routinely stack TWO legal-form markers
    (e.g. "Claas KGaA mbH") - stripping only "mbH" once would leave "KGaA"
    still glued onto the core name, guessing "claaskgaa.com" instead of the
    real "claas.com". Two passes is enough for every real-world case seen
    so far without risking eating into the actual brand name on an
    unrelated short trailing word."""
    lowered = [w.lower().strip(".,") for w in words]
    for _ in range(2):
        stripped_one = False
        for suffix in sorted(_LEGAL_SUFFIXES, key=len, reverse=True):
            suffix_words = suffix.split()
            n = len(suffix_words)
            if n <= len(lowered) and lowered[-n:] == suffix_words:
                words = words[:-n]
                lowered = lowered[:-n]
                stripped_one = True
                break
        if not stripped_one:
            break
    return words


def cctld_for_location(location: str | None) -> str | None:
    if not location:
        return None
    loc = location.lower()
    for needle, tld in _CCTLD_MAP:
        if needle in loc:
            return tld
    return None


def _slugify(words: list[str], sep: str = "") -> str:
    joined = sep.join(re.sub(r"[^a-z0-9]", "", w.lower()) for w in words)
    return joined


def generate_domain_candidates(company_name: str, location: str | None = None, limit: int = 8) -> list[str]:
    """
    Returns ranked candidate domains, most-likely first. Callers are
    expected to DNS-validate before trusting any of these - see
    dns_utils.first_resolving_domain.
    """
    raw_words = [w for w in re.split(r"\s+", company_name.strip()) if w]
    if not raw_words:
        return []

    core_words = _strip_legal_suffix(raw_words) or raw_words

    concat = _slugify(core_words)
    hyphenated = _slugify(core_words, sep="-")

    if not concat:
        return []

    tlds = [t for t in [cctld_for_location(location)] if t] + _GENERIC_TLDS
    # De-dupe while preserving order (a ccTLD could coincidentally match a
    # generic one, e.g. neither list actually overlaps today, but stay safe).
    seen_tlds: set[str] = set()
    ordered_tlds = [t for t in tlds if not (t in seen_tlds or seen_tlds.add(t))]

    candidates: list[str] = []
    for tld in ordered_tlds:
        candidates.append(f"{concat}.{tld}")
        if hyphenated != concat:
            candidates.append(f"{hyphenated}.{tld}")

    # Acronym guess (e.g. "International Business Machines" -> "ibm.com") is
    # low-precision - only worth trying for genuinely multi-word names, and
    # only on .com, ranked last.
    if len(core_words) >= 3:
        acronym = "".join(w[0].lower() for w in core_words if w)
        if acronym and f"{acronym}.com" not in candidates:
            candidates.append(f"{acronym}.com")

    return candidates[:limit]
