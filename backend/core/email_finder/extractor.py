"""
Pulls real email addresses out of already-fetched page content (markdown or
HTML). Purely deterministic regex/parsing - no LLM involved anywhere in
this file, which is the whole point: an address only ends up here if it
was literally present on the page.
"""
from __future__ import annotations

import re

from backend.core.email_finder.domain_utils import registrable_domain
from backend.core.email_finder.models import TIER_CONFIDENCE, EmailCandidate, EmailLabel, EmailTier

EMAIL_REGEX = re.compile(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,24}")
MAILTO_REGEX = re.compile(r"mailto:([^\s\"')\]?>]+)", re.IGNORECASE)
# Cloudflare's "email protection" replaces the real address with a stub like
# <a class="__cf_email__" data-cfemail="4c2d...">[email&#160;protected]</a>
# and decodes it client-side via a tiny XOR-with-first-byte cipher. Jina's
# headless-Chrome render usually already executes that JS and shows the
# real address in the markdown - this is a safety net for when it doesn't
# (e.g. the page was fetched as raw HTML some other way).
CF_EMAIL_REGEX = re.compile(r'data-cfemail="([0-9a-fA-F]+)"')

# Domains that show up in scraped pages but are never a real company contact
# address - tracking pixels, platform/CMS boilerplate, and common
# placeholder/template domains (form `placeholder="you@company.com"` text is
# an extremely common source of false positives - these look like valid
# emails and pass every other check, so they need to be named explicitly).
_DOMAIN_BLACKLIST = {
    "sentry.io", "sentry-next.io", "wixpress.com", "godaddy.com",
    "cloudflare.com", "schema.org", "example.com", "example.org",
    "example.net", "w3.org", "googleusercontent.com", "gravatar.com",
    "wordpress.com", "wp.com", "githubusercontent.com", "cdn.jsdelivr.net",
    "company.com", "yourcompany.com", "domain.com", "yourdomain.com",
    "email.com", "test.com", "site.com", "website.com", "mysite.com",
    "sample.com", "acme.com", "placeholder.com",
}
_LOCAL_PART_BLACKLIST_PREFIXES = ("noreply", "no-reply", "donotreply", "do-not-reply", "postmaster", "mailer-daemon")
# Exact-match only (not substring) - these are common template placeholder
# local-parts, but "test" or "example" as a SUBSTRING of a real word
# ("latest@...", "testimonials@...") must not be caught by this.
_LOCAL_PART_BLACKLIST_EXACT = {"example", "test", "sample", "yourname", "username", "youremail", "someone", "name"}

_LABEL_KEYWORDS: list[tuple[str, EmailLabel]] = [
    ("info", EmailLabel.GENERAL), ("contact", EmailLabel.GENERAL), ("hello", EmailLabel.GENERAL),
    ("enquir", EmailLabel.GENERAL), ("inquir", EmailLabel.GENERAL), ("office", EmailLabel.GENERAL),
    ("admin", EmailLabel.GENERAL), ("general", EmailLabel.GENERAL),
    ("sales", EmailLabel.SALES), ("business", EmailLabel.SALES), ("partnership", EmailLabel.SALES),
    ("support", EmailLabel.SUPPORT), ("help", EmailLabel.SUPPORT), ("service", EmailLabel.SUPPORT),
    ("hr", EmailLabel.HR), ("career", EmailLabel.HR), ("job", EmailLabel.HR), ("recruit", EmailLabel.HR),
    ("press", EmailLabel.MEDIA), ("media", EmailLabel.MEDIA), ("pr@", EmailLabel.MEDIA),
]


def _decode_cf_email(hexstr: str) -> str | None:
    try:
        raw = bytes.fromhex(hexstr)
    except ValueError:
        return None
    if len(raw) < 2:
        return None
    key = raw[0]
    decoded = bytes(b ^ key for b in raw[1:])
    try:
        return decoded.decode("utf-8")
    except UnicodeDecodeError:
        return None


def _label_for(local_part: str) -> EmailLabel:
    lowered = local_part.lower()
    for keyword, label in _LABEL_KEYWORDS:
        if keyword in lowered:
            return label
    # A local part that looks like a real person's name (contains a dot
    # separating two word-like chunks, e.g. "jane.doe") is a personal
    # mailbox, not a company inbox - useful to know but ranked lower as a
    # primary contact.
    if re.match(r"^[a-z]+\.[a-z]+$", lowered):
        return EmailLabel.PERSONAL
    return EmailLabel.UNKNOWN


def is_valid_candidate(email: str) -> bool:
    email = email.strip().strip(".,;:")
    if not EMAIL_REGEX.fullmatch(email):
        return False
    local, _, domain = email.partition("@")
    domain = domain.lower()
    if domain in _DOMAIN_BLACKLIST or any(domain.endswith(f".{d}") for d in _DOMAIN_BLACKLIST):
        return False
    if local.lower().startswith(_LOCAL_PART_BLACKLIST_PREFIXES):
        return False
    if local.lower() in _LOCAL_PART_BLACKLIST_EXACT:
        return False
    # Filenames like "logo@2x.png" or version strings matched by accident.
    if re.search(r"\.(png|jpe?g|gif|svg|webp|css|js)$", domain):
        return False
    return True


def extract_emails(
    text: str, page_url: str, site_domain: str | None, allow_offsite: bool = True
) -> list[EmailCandidate]:
    """`site_domain` is the company's own resolved website domain (if known)
    - used to decide SCRAPED_VERIFIED vs SCRAPED_OFFSITE.

    `allow_offsite` controls whether an off-domain address (e.g. a gmail
    address, or - the risky case - a THIRD PARTY's address such as a
    hosting provider's default/parked-page contact) is even returned at
    all. This must be False whenever `site_domain` itself isn't confidently
    established as really being this company's site (a guessed domain, or
    one an LLM web search merely suggested) - otherwise a wrong-domain
    guess plus an off-domain email on that wrong page combine into a
    confidently-labeled result that's actually a stranger's contact info.
    Only PROVIDED URLs (the user told us directly) are trusted enough to
    accept an off-domain match - see pipeline.py."""
    found: dict[str, EmailCandidate] = {}
    site_domain_norm = registrable_domain(site_domain) if site_domain else None

    raw_matches = list(MAILTO_REGEX.findall(text)) + list(EMAIL_REGEX.findall(text))
    for hexstr in CF_EMAIL_REGEX.findall(text):
        decoded = _decode_cf_email(hexstr)
        if decoded:
            raw_matches.append(decoded)

    for raw in raw_matches:
        email = raw.split("?")[0].strip()  # mailto:x@y.com?subject=... -> x@y.com
        if not is_valid_candidate(email):
            continue
        key = email.lower()
        if key in found:
            continue

        local, _, domain = email.partition("@")
        same_domain = site_domain_norm is not None and registrable_domain(domain) == site_domain_norm
        if not same_domain and not allow_offsite:
            continue
        tier = EmailTier.SCRAPED_VERIFIED if same_domain else EmailTier.SCRAPED_OFFSITE

        found[key] = EmailCandidate(
            email=email,
            label=_label_for(local),
            tier=tier,
            confidence=TIER_CONFIDENCE[tier],
            source_page=page_url,
        )

    return list(found.values())
