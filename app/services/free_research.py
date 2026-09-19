"""
free_research.py — Free research mode prototype (ticket #15, exploration).

Scope guardrails (from the ticket, non-negotiable)
--------------------------------------------------
- The prospect's OWN public pages only: homepage, newsroom/blog, careers.
- robots.txt respected for every fetch; robots-disallowed = a logged
  degradation, never a workaround.
- One fetch per page. No crawling, no retries beyond none, no login-walled
  content, honest User-Agent identifying the research purpose.
- ZERO LinkedIn or job-board URLs — ToS-prohibited, excluded by construction
  (see _FORBIDDEN_URL_MARKERS and the candidate-path list).
- Blocked/failed fetches flow into the #4 degradations channel.

Output shape mirrors the paid pipeline's research stage: a list of
``LiveSignal``-shaped evidence (title/url/snippet) so downstream prompts and
the benchmark harness can compare arms like-for-like. This module is a
prototype for the D1 adapter surface — it does not replace enrichment.py.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any
from urllib.parse import urljoin, urlparse
from urllib.robotparser import RobotFileParser

import requests

USER_AGENT = (
    "BDR-Pipeline-FreeResearch/0.1 (public-pages research prototype; "
    "robots-respecting; contact: tenant operator)"
)
FETCH_TIMEOUT_SECONDS = 10
MAX_PAGE_BYTES = 1_000_000  # read at most 1MB per page (text extraction keeps only ~600 chars)
MAX_SNIPPET_CHARS = 600

# Public-page candidates, in priority order. Static, well-known paths only —
# no link crawling, one fetch per candidate at most.
PAGE_CANDIDATES = ("", "/newsroom", "/blog", "/careers", "/about")

# Hard exclusions — URL markers that must never be fetched or produced.
FORBIDDEN_URL_MARKERS = (
    "linkedin.com", "indeed.com", "glassdoor.com", "ziprecruiter.com",
    "monster.com", "weworkremotely", "remoteok", "wellfound",
)


@dataclass
class FetchRecord:
    """Audit log for one fetch attempt (AC: every fetch logged with URL + time)."""

    url: str
    timestamp_utc: str
    outcome: str  # "fetched" | "robots_disallowed" | "http_error" | "network_error" | "non_html" | "empty" | "too_large" | "skipped_forbidden"
    detail: str = ""
    bytes_read: int = 0


@dataclass
class FreeResearchResult:
    signals: list[Any] = field(default_factory=list)  # LiveSignal-shaped dicts
    degradations: list[str] = field(default_factory=list)
    fetch_log: list[FetchRecord] = field(default_factory=list)

    @property
    def pages_fetched(self) -> int:
        return sum(1 for r in self.fetch_log if r.outcome == "fetched")

    @property
    def pages_blocked(self) -> int:
        return sum(1 for r in self.fetch_log if r.outcome == "robots_disallowed")

    @property
    def pages_failed(self) -> int:
        return sum(
            1 for r in self.fetch_log
            if r.outcome in ("http_error", "network_error", "non_html", "empty", "too_large")
        )


def is_forbidden_url(url: str) -> bool:
    """True for any URL the research posture hard-excludes (LinkedIn, job boards)."""
    lowered = (url or "").lower()
    return any(marker in lowered for marker in FORBIDDEN_URL_MARKERS)


def candidate_urls(company: str, domain: str) -> list[str]:
    """Public-page candidate URLs for a company (homepage + known sections)."""
    domain = (domain or "").strip().lower()
    if not domain:
        return []
    if not domain.startswith(("http://", "https://")):
        domain = f"https://{domain}"
    base = f"{domain.rstrip('/')}"
    return [f"{base}{path}" for path in PAGE_CANDIDATES]


def _robots_allowed(url: str, session: requests.Session) -> tuple[bool, str]:
    """Check robots.txt for our UA. Network or parse problems fail CLOSED
    (treated as disallowed) — the conservative posture for an exploration."""
    parsed = urlparse(url)
    robots_url = f"{parsed.scheme}://{parsed.netloc}/robots.txt"
    parser = RobotFileParser()
    try:
        resp = session.get(robots_url, timeout=FETCH_TIMEOUT_SECONDS)
        if resp.status_code >= 400:
            # No readable robots.txt: be conservative for this prototype.
            return True, f"robots.txt HTTP {resp.status_code} (failing open: no restrictions parseable)"
        parser.parse(resp.text.splitlines())
    except requests.RequestException as exc:
        return False, f"robots.txt unreachable ({type(exc).__name__}) — failing closed"
    try:
        allowed = parser.can_fetch(USER_AGENT, url)
    except Exception:
        allowed = False
    return bool(allowed), ""


def _strip_html(html: str) -> str:
    """Crude text extraction — enough for evidence snippets, no parsing tricks."""
    html = re.sub(r"(?is)<(script|style|noscript)[^>]*>.*?</\1>", " ", html)
    text = re.sub(r"(?s)<[^>]+>", " ", html)
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def _title_of(html: str) -> str:
    match = re.search(r"(?is)<title[^>]*>(.*?)</title>", html)
    return re.sub(r"\s+", " ", match.group(1)).strip() if match else ""


def _new_signal(url: str, title: str, text: str) -> dict[str, Any]:
    return {
        "title": title or url,
        "url": url,
        "snippet": text[:MAX_SNIPPET_CHARS],
    }


def research_company_public_pages(
    company: str,
    domain: str,
    session: requests.Session | None = None,
) -> FreeResearchResult:
    """Fetch the company's own public pages and shape them as evidence.

    Every candidate fetch is attempted at most once and logged. Robots-
    disallowed and failed fetches append to degradations (the #4 channel).
    """
    result = FreeResearchResult()
    own_session = session is None
    session = session or requests.Session()
    session.headers.update({"User-Agent": USER_AGENT})

    urls = candidate_urls(company, domain)
    if not urls:
        result.degradations.append(
            f"Free research: no domain known for {company!r} — public-page fetch skipped"
        )
        if own_session:
            session.close()
        return result

    for url in urls:
        now = datetime.now(timezone.utc).isoformat(timespec="seconds")

        if is_forbidden_url(url):
            result.fetch_log.append(FetchRecord(url, now, "skipped_forbidden"))
            result.degradations.append(f"Free research: {url} excluded (forbidden URL class)")
            continue

        allowed, note = _robots_allowed(url, session)
        if not allowed:
            detail = note or "robots.txt disallows"
            result.fetch_log.append(FetchRecord(url, now, "robots_disallowed", detail))
            result.degradations.append(
                f"Free research: {url} blocked by robots.txt — page not fetched ({detail})"
            )
            continue

        try:
            resp = session.get(url, timeout=FETCH_TIMEOUT_SECONDS, stream=True)
        except requests.RequestException as exc:
            result.fetch_log.append(
                FetchRecord(url, now, "network_error", type(exc).__name__)
            )
            result.degradations.append(
                f"Free research: {url} unreachable ({type(exc).__name__})"
            )
            continue

        if resp.status_code >= 400:
            result.fetch_log.append(
                FetchRecord(url, now, "http_error", f"HTTP {resp.status_code}")
            )
            result.degradations.append(f"Free research: {url} returned HTTP {resp.status_code}")
            continue

        content_type = resp.headers.get("Content-Type", "")
        if "text/html" not in content_type and "text/plain" not in content_type:
            result.fetch_log.append(
                FetchRecord(url, now, "non_html", content_type[:60])
            )
            result.degradations.append(f"Free research: {url} is non-HTML ({content_type[:40]})")
            continue

        raw = resp.raw.read(MAX_PAGE_BYTES + 1, decode_content=True) if resp.raw else b""
        if not raw:
            raw = resp.content[: MAX_PAGE_BYTES + 1]
        if len(raw) > MAX_PAGE_BYTES:
            result.fetch_log.append(FetchRecord(url, now, "too_large", f"{len(raw)} bytes"))
            result.degradations.append(f"Free research: {url} exceeded {MAX_PAGE_BYTES}B cap")
            continue

        html = raw.decode(resp.encoding or "utf-8", errors="replace")
        text = _strip_html(html)
        if len(text) < 40:
            result.fetch_log.append(FetchRecord(url, now, "empty"))
            result.degradations.append(f"Free research: {url} produced no usable text")
            continue

        result.fetch_log.append(
            FetchRecord(url, now, "fetched", bytes_read=len(raw))
        )
        result.signals.append(_new_signal(url, _title_of(html), text))

    if own_session:
        session.close()

    if not result.signals:
        result.degradations.append(
            f"Free research: no public pages yielded evidence for {company!r}"
        )
    return result
