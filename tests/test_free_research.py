"""
Offline tests for the free-research prototype (ticket #15).

No network: every test injects a FakeSession whose routes map URLs to
responses or exceptions. Locks in the ToS posture (robots respected,
forbidden URL classes never fetched) and the evidence/degradation contracts.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest
import requests

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.services.free_research import (  # noqa: E402
    FORBIDDEN_URL_MARKERS,
    PAGE_CANDIDATES,
    candidate_urls,
    is_forbidden_url,
    research_company_public_pages,
)

HTML = (
    "<html><head><title>Acme — {page}</title></head><body>"
    "Acme builds industrial widgets for teams worldwide since 1998. "
    "Our headquarters are in Rotterdam and we serve 40 countries.</body></html>"
)


class FakeResponse:
    def __init__(self, status=200, text=HTML, content_type="text/html; charset=utf-8"):
        self.status_code = status
        self.headers = {"Content-Type": content_type}
        self.text = text
        self.encoding = "utf-8"
        self.raw = None
        self.content = text.encode("utf-8")


class FakeSession:
    """Route table stand-in for requests.Session."""

    def __init__(self, routes=None):
        self.routes = routes or {}
        self.headers: dict[str, str] = {}
        self.requested: list[str] = []

    def get(self, url, **kwargs):
        self.requested.append(url)
        item = self.routes.get(url)
        if isinstance(item, Exception):
            raise item
        if item is None:
            return FakeResponse(status=404, text="")
        return item

    def close(self):
        pass


def routes_for(domain: str, **overrides):
    """robots-allow + one HTML page per candidate path."""
    base = f"https://{domain}"
    routes = {"https://" + domain + "/robots.txt": FakeResponse(text="User-agent: *\nAllow: /\n")}
    for path in PAGE_CANDIDATES:
        routes[base + path] = FakeResponse(text=HTML.format(page=path or "home"))
    routes.update(overrides)
    return routes


def test_candidate_urls_never_contain_forbidden_classes():
    for domain in ("acme.com", "https://news.acme.org"):
        for url in candidate_urls("Acme", domain):
            assert not is_forbidden_url(url)
    for marker in ("linkedin.com", "indeed.com", "glassdoor.com"):
        assert marker in FORBIDDEN_URL_MARKERS
        assert is_forbidden_url(f"https://www.{marker}/company/acme")


def test_happy_path_produces_livesignal_shaped_evidence():
    session = FakeSession(routes_for("acme.com"))
    result = research_company_public_pages("Acme", "acme.com", session=session)
    assert result.pages_fetched == len(PAGE_CANDIDATES)
    assert len(result.signals) == len(PAGE_CANDIDATES)
    for signal in result.signals:
        assert set(signal) == {"title", "url", "snippet"}
        assert signal["url"].startswith("https://acme.com")
        assert signal["snippet"]
    assert result.degradations == []


def test_robots_disallow_blocks_every_fetch_and_degrades():
    session = FakeSession(routes_for("acme.com", **{"https://acme.com/robots.txt": FakeResponse(text="User-agent: *\nDisallow: /\n")}))
    result = research_company_public_pages("Acme", "acme.com", session=session)
    assert result.pages_blocked == len(PAGE_CANDIDATES)
    assert result.pages_fetched == 0
    assert all(r.outcome == "robots_disallowed" for r in result.fetch_log)
    assert any("robots.txt" in d for d in result.degradations)
    # The page itself was never requested — only robots.txt was.
    assert all(u.endswith("robots.txt") for u in session.requested)


def test_robots_unreachable_fails_closed():
    session = FakeSession(routes_for("acme.com", **{"https://acme.com/robots.txt": requests.ConnectionError("dns down")}))
    result = research_company_public_pages("Acme", "acme.com", session=session)
    assert result.pages_blocked == len(PAGE_CANDIDATES)
    assert result.pages_fetched == 0
    assert any("failing closed" in d for d in result.degradations)


def test_robots_missing_fails_open_but_pages_still_individually_logged():
    session = FakeSession(routes_for("acme.com", **{"https://acme.com/robots.txt": FakeResponse(status=404, text="not found")}))
    result = research_company_public_pages("Acme", "acme.com", session=session)
    assert result.pages_fetched == len(PAGE_CANDIDATES)


def test_http_and_network_failures_degrade_without_killing_the_rest():
    routes = routes_for(
        "acme.com",
        **{
            "https://acme.com/blog": FakeResponse(status=500, text="boom"),
            "https://acme.com/careers": requests.Timeout("slow"),
        },
    )
    result = research_company_public_pages("Acme", "acme.com", session=FakeSession(routes))
    outcomes = {r.url: r.outcome for r in result.fetch_log}
    assert outcomes["https://acme.com/blog"] == "http_error"
    assert outcomes["https://acme.com/careers"] == "network_error"
    assert outcomes["https://acme.com"] == "fetched"
    assert result.pages_fetched == len(PAGE_CANDIDATES) - 2
    assert any("HTTP 500" in d for d in result.degradations)
    assert any("Timeout" in d for d in result.degradations)


def test_empty_domain_degrades_without_fetching():
    session = FakeSession()
    result = research_company_public_pages("Mystery Co", "", session=session)
    assert session.requested == []
    assert result.signals == []
    assert any("no domain known" in d for d in result.degradations)


def test_one_fetch_per_page_no_crawling():
    session = FakeSession(routes_for("acme.com"))
    research_company_public_pages("Acme", "acme.com", session=session)
    pages = [u for u in session.requested if not u.endswith("robots.txt")]
    assert len(pages) == len(set(pages)), "each candidate page fetched at most once"


def test_total_failure_appends_no_evidence_degradation():
    routes = {f"https://acme.com{p}": FakeResponse(status=404, text="") for p in PAGE_CANDIDATES}
    routes["https://acme.com/robots.txt"] = FakeResponse(text="User-agent: *\nAllow: /\n")
    result = research_company_public_pages("Acme", "acme.com", session=FakeSession(routes))
    assert result.signals == []
    assert any("no public pages yielded evidence" in d for d in result.degradations)


@pytest.mark.parametrize("url,expected", [
    ("https://stripe.com/careers", False),
    ("https://www.linkedin.com/company/stripe", True),
    ("https://indeed.com/viewjob?jk=1", True),
])
def test_is_forbidden_url_classification(url, expected):
    assert is_forbidden_url(url) is expected
