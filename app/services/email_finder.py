"""
email_finder.py — Free contact discovery (C1, ticket #17).

Replaces paid Hunter lookups with a zero-key path built on the #15 posture:

  - Recon scope: the company's OWN public pages only (contact / team / about /
    people / imprint + homepage). One fetch per page, robots.txt respected
    (network failure while checking robots = blocked, the conservative
    posture), honest User-Agent, every fetch logged.
  - Zero LinkedIn / job-board URLs by construction (same forbidden list).
  - Extraction: mailto: links + plain-text emails from script/style-stripped
    HTML. Junk, placeholder, freemail and role addresses are classified and
    confidence-capped, not silently dropped.
  - Pattern inference: first.last@domain style candidates. When a name is
    supplied, the domain's own harvested emails teach the local-part
    convention (dot_two / dot_initial / plain / underscore_*) and the
    inferred address is scored accordingly.
  - MX verification: guarded dnspython import (no hard dependency). A domain
    that cannot resolve at all caps every candidate at 15.

Confidence model (0-100, honest by construction):
  mailto on own site 88 > page text 78 (+4 on contact-context pages)
  role caps: noreply-class 10, legal/privacy 30, support-class 55,
             recruiting 55, generic (info@/hello@) 70
  freemail cap 45; person-name match +8 (max 97)
  pattern with learned convention 65 > unlearned fallbacks 30-45
  MX ok +3; MX fail caps 15.

Output: ContactLead-shaped dicts (enrichment-consumable) plus a provenance
list of FoundEmail records. Every failure flows into the #4 degradations
channel — no silent empties.
"""
from __future__ import annotations

import re
import socket
from datetime import datetime, timezone
from dataclasses import dataclass, field
from typing import Any

import requests

from app.services.free_research import (
    FETCH_TIMEOUT_SECONDS,
    MAX_PAGE_BYTES,
    USER_AGENT,
    FetchRecord,
    is_forbidden_url,
)
from app.services.free_research import _robots_allowed, _strip_html

# Public-page candidates for contact discovery, in priority order. Static
# well-known paths only — one fetch each, no crawling.
CONTACT_PAGE_CANDIDATES = (
    "", "/contact", "/contact-us", "/team", "/about", "/about-us", "/people", "/imprint",
)

# Pages where a listed email is deliberately published contact info.
CONTACT_CONTEXT_PATHS = {"/contact", "/contact-us", "/team", "/people", "/imprint", "/about", "/about-us"}

EMAIL_RE = re.compile(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}")
MAILTO_RE = re.compile(r"mailto:([^\s\"'<>?\\]+)", re.IGNORECASE)

# TLDs that mark an asset path, not an address (logo.png@2x etc.).
ASSET_TLDS = {"png", "jpg", "jpeg", "gif", "webp", "svg", "css", "js", "json", "ico", "woff", "woff2"}

# Placeholder / platform domains that are never the company's real address.
PLACEHOLDER_DOMAINS = {
    "example.com", "example.org", "example.net", "domain.com", "yourdomain.com",
    "yourdomain.co", "email.com", "company.com", "site.com", "website.com",
    "test.com", "sentry.io", "wixpress.com", "sentry-next.wixpress.com",
    "godaddy.com", "squarespace.com", "cloudflare.com", "google.com",
}
PLACEHOLDER_LOCALS = {
    "email", "name", "user", "username", "yourname", "your-email", "youremail",
    "john.doe", "jane.doe", "johnsmith", "janesmith", "first.last", "first_last",
}

FREEMAIL_DOMAINS = {
    "gmail.com", "googlemail.com", "hotmail.com", "hotmail.co.uk", "outlook.com",
    "live.com", "msn.com", "icloud.com", "me.com", "mac.com", "aol.com",
    "protonmail.com", "proton.me", "pm.me", "mail.com", "gmx.com", "gmx.de",
    "web.de", "zoho.com", "fastmail.com", "yandex.com", "yandex.ru",
    "qq.com", "163.com", "126.com",
}

# Role local-parts by how useful they are for outbound.
ROLE_NEVER = {  # automated / infrastructure — never a person
    "noreply", "no-reply", "no_reply", "donotreply", "do-not-reply", "notreply",
    "postmaster", "webmaster", "hostmaster", "mailer-daemon", "bounce", "bounces",
}
ROLE_LEGAL = {"privacy", "gdpr", "legal", "dpo", "dataprotection", "compliance", "abuse", "security"}
ROLE_SUPPORT = {"support", "help", "helpdesk", "service", "care", "billing", "accounts", "invoice", "admin", "office"}
ROLE_RECRUIT = {"careers", "jobs", "hr", "talent", "recruitment", "people", "recruiting", "karriere", "werken"}
ROLE_GENERIC = {"info", "hello", "contact", "sales", "marketing", "press", "media", "team", "hi", "enquiries", "inquiry", "welcome"}

ROLE_CAPS: tuple[tuple[set[str], int], ...] = (
    (ROLE_NEVER, 10),
    (ROLE_LEGAL, 30),
    (ROLE_RECRUIT, 55),
    (ROLE_SUPPORT, 55),
    (ROLE_GENERIC, 70),
)

PATTERN_SOURCE = "pattern"


@dataclass
class FoundEmail:
    """One discovered address with full provenance."""

    email: str
    confidence: int
    source: str  # "mailto" | "page_text" | "pattern"
    source_url: str = ""
    kind: str = "other"  # "person" | "role" | "freemail" | "pattern" | "other"
    verification: str = "unknown"  # "mx_ok" | "mx_fail" | "pattern" | "unknown"
    note: str = ""


@dataclass
class EmailFinderResult:
    contacts: list[dict[str, Any]] = field(default_factory=list)  # ContactLead-shaped dicts
    emails: list[FoundEmail] = field(default_factory=list)
    degradations: list[str] = field(default_factory=list)
    fetch_log: list[FetchRecord] = field(default_factory=list)
    conventions: list[str] = field(default_factory=list)  # learned local-part shapes

    @property
    def pages_fetched(self) -> int:
        return sum(1 for r in self.fetch_log if r.outcome == "fetched")


# ---------------------------------------------------------------------------
# Extraction helpers
# ---------------------------------------------------------------------------
def _clean_candidate(raw: str) -> str:
    email = raw.strip().strip("<>").lower().strip("\"'.,;:!?)]}")
    return email


def _is_plausible(email: str) -> bool:
    if not EMAIL_RE.fullmatch(email):
        return False
    local, _, domain = email.partition("@")
    if not local or not domain:
        return False
    if domain.rsplit(".", 1)[-1] in ASSET_TLDS:
        return False
    if local in PLACEHOLDER_LOCALS or domain in PLACEHOLDER_DOMAINS:
        return False
    return True


def _base_confidence(source: str) -> int:
    """mailto links are deliberately-published contact points; text matches are softer."""
    return 88 if source == "mailto" else 78


def _apply_role_cap(email: str, confidence: int) -> tuple[int, str]:
    local = email.partition("@")[0]
    for roles, cap in ROLE_CAPS:
        if local in roles:
            return cap, "role"
    return confidence, "person_or_other"


def _apply_freemail_cap(email: str, confidence: int, kind: str) -> tuple[int, str]:
    if email.partition("@")[2] in FREEMAIL_DOMAINS:
        return min(confidence, 45), "freemail"
    return confidence, kind  # preserve role classification


def _name_bonus(email: str, first: str | None, last: str | None) -> bool:
    """True when the local part plausibly encodes the supplied person's name."""
    if not (first and last):
        return False
    local = email.partition("@")[0]
    f, l = first.lower(), last.lower()
    compact = local.replace(".", "").replace("_", "").replace("-", "")
    return (f in compact and l in compact) or compact == f"{f}{l}"


# ---------------------------------------------------------------------------
# Domain convention learning (for pattern inference)
# ---------------------------------------------------------------------------
def local_shape(local: str) -> str | None:
    """Classify a local part into a learnable shape, or None if ambiguous."""
    if "." in local:
        parts = local.split(".")
        if len(parts) == 2:
            if len(parts[0]) == 1:
                return "dot_initial"
            return "dot_two"
        return None  # dot_multi — too ambiguous to learn from
    if "_" in local:
        parts = local.split("_")
        if len(parts) == 2:
            if len(parts[0]) == 1:
                return "underscore_initial"
            return "underscore_two"
        return None
    if len(local) <= 2:
        return None
    return "plain"  # firstlast / last / first — mapped to firstlast when inferring


def learn_conventions(emails: list[str]) -> list[str]:
    """Learn the domain's local-part convention from harvested real addresses."""
    shapes: list[str] = []
    for email in emails:
        local = email.partition("@")[0]
        if local in ROLE_NEVER or local in ROLE_GENERIC or local in ROLE_LEGAL or local in ROLE_SUPPORT or local in ROLE_RECRUIT:
            continue
        domain = email.partition("@")[2]
        if domain in FREEMAIL_DOMAINS:
            continue
        shape = local_shape(local)
        if shape and shape not in shapes:
            shapes.append(shape)
    return shapes


def _candidates_for_shape(shape: str, first: str, last: str) -> str | None:
    if shape == "dot_two":
        return f"{first}.{last}"
    if shape == "dot_initial":
        return f"{first[0]}.{last}"
    if shape == "underscore_two":
        return f"{first}_{last}"
    if shape == "underscore_initial":
        return f"{first[0]}_{last}"
    if shape == "plain":
        return f"{first}{last}"
    return None


def infer_patterns(
    first: str,
    last: str,
    domain: str,
    learned: list[str] | None = None,
) -> list[FoundEmail]:
    """Pattern candidates for a supplied name, ranked by confidence.

    With a learned convention the primary candidate scores 65; unknown
    conventions fall back to the most common shapes at 30-45.
    """
    first = re.sub(r"[^a-z]", "", first.lower())
    last = re.sub(r"[^a-z]", "", last.lower())
    if not first or not last or not domain:
        return []
    domain = domain.removeprefix("https://").removeprefix("http://").strip("/").lower()

    ranked: list[tuple[str, int]] = []
    if learned:
        for shape in learned:
            cand = _candidates_for_shape(shape, first, last)
            if cand:
                ranked.append((cand, 65))
    fallbacks = [("dot_two", 45), ("dot_initial", 35), ("plain", 35), ("underscore_two", 30)]
    for shape, conf in fallbacks:
        cand = _candidates_for_shape(shape, first, last)
        if cand and cand not in [c for c, _ in ranked]:
            ranked.append((cand, conf))
    return [
        FoundEmail(email=f"{cand}@{domain}", confidence=conf, source=PATTERN_SOURCE,
                   kind="pattern", verification="pattern")
        for cand, conf in ranked[:6]
    ]


# ---------------------------------------------------------------------------
# MX verification (guarded — no hard dependency on dnspython)
# ---------------------------------------------------------------------------
def mx_status(domain: str) -> str:
    """"ok" | "fail" | "unknown" for the domain's ability to receive mail."""
    try:
        import dns.resolver  # type: ignore
    except ImportError:
        return "unknown"
    try:
        if dns.resolver.resolve(domain, "MX"):
            return "ok"
    except Exception:
        pass
    try:  # small domains sometimes receive on the A record
        socket.gethostbyname(domain)
        return "ok"
    except OSError:
        return "fail"


# ---------------------------------------------------------------------------
# Harvest
# ---------------------------------------------------------------------------
def _harvest_emails(html: str, source_url: str) -> list[tuple[str, str]]:
    """(email, source) pairs from one page — mailto links first, then text."""
    cleaned = re.sub(r"(?is)<(script|style|noscript)[^>]*>.*?</\1>", " ", html)
    found: list[tuple[str, str]] = []
    for match in MAILTO_RE.findall(cleaned):
        found.append((match, "mailto"))
    text = _strip_html(cleaned)
    for match in EMAIL_RE.findall(text):
        found.append((match, "page_text"))
    return found


@dataclass
class _Accum:
    best: dict[str, FoundEmail] = field(default_factory=dict)
    harvested_locals: list[str] = field(default_factory=list)

    def offer(self, candidate: FoundEmail) -> None:
        key = candidate.email
        existing = self.best.get(key)
        if existing is None or candidate.confidence > existing.confidence:
            self.best[key] = candidate


def find_emails(
    company: str,
    domain: str,
    first_name: str | None = None,
    last_name: str | None = None,
    session: requests.Session | None = None,
) -> EmailFinderResult:
    """Free contact discovery for one company domain. Zero API keys."""
    result = EmailFinderResult()
    own_session = session is None
    session = session or requests.Session()
    session.headers.update({"User-Agent": USER_AGENT})

    domain = (domain or "").strip().lower().removeprefix("https://").removeprefix("http://").strip("/")
    if not domain:
        result.degradations.append(f"Email finder: no domain known for {company!r} — contact discovery skipped")
        if own_session:
            session.close()
        return result

    base = f"https://{domain}"
    accum = _Accum()
    mx_cache: str | None = None  # lazily resolved once per run

    for path in CONTACT_PAGE_CANDIDATES:
        url = f"{base}{path}"
        now = FetchRecord(url, datetime.now(timezone.utc).isoformat(timespec="seconds"), "pending")

        if is_forbidden_url(url):
            now.outcome = "skipped_forbidden"
            result.fetch_log.append(now)
            result.degradations.append(f"Email finder: {url} excluded (forbidden URL class)")
            continue

        allowed, note = _robots_allowed(url, session)
        if not allowed:
            now.outcome = "robots_disallowed"
            now.detail = note or "robots.txt disallows"
            result.fetch_log.append(now)
            result.degradations.append(f"Email finder: {url} blocked by robots.txt ({now.detail})")
            continue

        try:
            resp = session.get(url, timeout=FETCH_TIMEOUT_SECONDS, stream=True)
        except requests.RequestException as exc:
            now.outcome = "network_error"
            now.detail = type(exc).__name__
            result.fetch_log.append(now)
            result.degradations.append(f"Email finder: {url} unreachable ({type(exc).__name__})")
            continue

        if resp.status_code >= 400:
            now.outcome = "http_error"
            now.detail = f"HTTP {resp.status_code}"
            result.fetch_log.append(now)
            continue

        content_type = resp.headers.get("Content-Type", "")
        if "text/html" not in content_type and "text/plain" not in content_type:
            now.outcome = "non_html"
            now.detail = content_type[:60]
            result.fetch_log.append(now)
            continue

        raw = resp.raw.read(MAX_PAGE_BYTES + 1, decode_content=True) if resp.raw else b""
        if not raw:
            raw = resp.content[: MAX_PAGE_BYTES + 1]
        if len(raw) > MAX_PAGE_BYTES:
            now.outcome = "too_large"
            now.detail = f"{len(raw)} bytes"
            result.fetch_log.append(now)
            continue

        html = raw.decode(resp.encoding or "utf-8", errors="replace")
        pairs = _harvest_emails(html, url)
        now.outcome = "fetched"
        now.detail = f"{len(pairs)} email candidate(s)"
        result.fetch_log.append(now)

        for raw_email, source in pairs:
            email = _clean_candidate(raw_email)
            if not _is_plausible(email):
                continue
            conf = _base_confidence(source)
            conf, kind = _apply_role_cap(email, conf)
            conf, kind = _apply_freemail_cap(email, conf, kind)
            if path in CONTACT_CONTEXT_PATHS and conf >= 80:
                conf = min(conf + 4, 93)
            if _name_bonus(email, first_name, last_name):
                conf = min(conf + 8, 97)
                kind = "person"
            accum.offer(FoundEmail(
                email=email, confidence=conf, source=source, source_url=url, kind=kind,
            ))
            if kind == "person_or_other" and email.partition("@")[2] not in FREEMAIL_DOMAINS:
                accum.harvested_locals.append(email)

    # Pattern inference (needs a supplied name; convention learned from harvest)
    conventions = learn_conventions(accum.harvested_locals)
    result.conventions = conventions
    patterns = infer_patterns(first_name or "", last_name or "", domain, conventions) if (first_name and last_name) else []
    for candidate in patterns:
        if candidate.email in accum.best:
            continue
        accum.offer(candidate)

    # MX verification once per run, applied to every candidate
    harvested = list(accum.best.values())
    if harvested:
        mx_cache = mx_status(domain)
        if mx_cache == "fail":
            result.degradations.append(f"Email finder: domain {domain} has no MX/A record — all addresses capped at 15")
        for found in harvested:
            if mx_cache == "ok":
                found.verification = "mx_ok"
                found.confidence = min(found.confidence + 3, 99)
            elif mx_cache == "fail":
                found.verification = "mx_fail"
                found.confidence = min(found.confidence, 15)

    ordered = sorted(accum.best.values(), key=lambda f: f.confidence, reverse=True)
    result.emails = ordered

    result.contacts = [
        {
            "name": f"{first_name} {last_name}".strip() if f.source == PATTERN_SOURCE and first_name else "",
            "email": f.email,
            "position": "",
            "seniority": "",
            "department": "",
            "confidence": int(f.confidence),
            "linkedin_url": "",
            "source": f.source,
            "verification": f.verification,
        }
        for f in ordered[:5]
    ]

    if not ordered:
        result.degradations.append(
            f"Email finder: no public email found for {company!r} @ {domain} — "
            + ("pattern inference needs a supplied name" if not patterns else "no candidates produced")
        )
    elif not any(f.kind in ("person", "person_or_other") and f.source != PATTERN_SOURCE for f in ordered):
        result.degradations.append(
            f"Email finder: only pattern-inferred addresses for {company!r} — verify before sending"
        )

    if own_session:
        session.close()
    return result
