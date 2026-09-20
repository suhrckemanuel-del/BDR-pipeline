"""
Offline tests for the free email finder (C1, ticket #17).

No network, no API keys: every test injects a FakeSession (same pattern as
test_free_research) and monkeypatches the MX verifier. Locks in the ToS
posture, the confidence-cap table, convention learning, and the
ContactLead-shaped output contract.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest
import requests

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import app.services.email_finder as ef  # noqa: E402
from app.services.email_finder import (  # noqa: E402
    CONTACT_PAGE_CANDIDATES,
    find_emails,
    infer_patterns,
    learn_conventions,
    local_shape,
)

HTML = """<html><head><title>Acme — {page}</title></head><body>
Acme builds industrial widgets. {body}</body></html>"""


class FakeResponse:
    def __init__(self, status=200, text=HTML, content_type="text/html; charset=utf-8"):
        self.status_code = status
        self.headers = {"Content-Type": content_type}
        self.text = text
        self.encoding = "utf-8"
        self.raw = None
        self.content = text.encode("utf-8")


class FakeSession:
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
    routes = {f"{base}/robots.txt": FakeResponse(text="User-agent: *\nAllow: /\n")}
    for path in CONTACT_PAGE_CANDIDATES:
        routes[base + path] = FakeResponse(text=HTML.format(page=path or "home", body="Acme since 1998 in Rotterdam."))
    routes.update(overrides)
    return routes


@pytest.fixture(autouse=True)
def no_mx_network(monkeypatch):
    """Default every test to MX-unknown so no DNS happens offline."""
    monkeypatch.setattr(ef, "mx_status", lambda domain: "unknown")


# ---------------------------------------------------------------------------
# Extraction + filtering
# ---------------------------------------------------------------------------
def test_mailto_and_text_emails_extracted_with_correct_confidence():
    contact_html = (
        '<a href="mailto:jane.smith@acme.com">Email Jane</a>'
        "<p>General enquiries: info@acme.com</p>"
    )
    routes = routes_for("acme.com", **{"https://acme.com/contact": FakeResponse(text=HTML.format(page="contact", body=contact_html))})
    result = find_emails("Acme", "acme.com", session=FakeSession(routes))
    by_email = {f.email: f for f in result.emails}
    jane = by_email["jane.smith@acme.com"]
    info = by_email["info@acme.com"]
    assert jane.source == "mailto" and jane.confidence == 92  # 88 + 4 contact context
    assert info.confidence == 70  # generic-role cap; context bonus gated above 80
    assert info.kind == "role"
    assert result.conventions == ["dot_two"]  # learned from jane.smith
    assert [c["email"] for c in result.contacts][:2] == ["jane.smith@acme.com", "info@acme.com"]


def test_junk_candidates_filtered_not_reported():
    junk_html = (
        "<img src='logo.png'>"
        "<p>Contact us at hello@example.com or fill in name@domain.com</p>"
        "<p>Errors go to sentry via sentry.io</p>"
        "<p>Bounces: noreply@acme.com</p>"
    )
    routes = routes_for("acme.com", **{"https://acme.com/contact": FakeResponse(text=HTML.format(page="contact", body=junk_html))})
    result = find_emails("Acme", "acme.com", session=FakeSession(routes))
    emails = [f.email for f in result.emails]
    assert "noreply@acme.com" in emails  # kept but capped...
    noreply = next(f for f in result.emails if f.email == "noreply@acme.com")
    assert noreply.confidence <= 10 and noreply.kind == "role"
    assert "example.com" not in emails and "name@domain.com" not in emails
    assert not any("png" in e for e in emails)
    assert "sentry.io" not in emails


def test_freemail_capped_at_45():
    routes = routes_for("acme.com", **{
        "https://acme.com/team": FakeResponse(text=HTML.format(page="team", body="Founder: <a href='mailto:dave@gmail.com'>Dave</a>")),
    })
    result = find_emails("Acme", "acme.com", session=FakeSession(routes))
    dave = next(f for f in result.emails if f.email == "dave@gmail.com")
    assert dave.confidence == 45 and dave.kind == "freemail"


def test_legal_and_support_role_caps():
    body = "<p>Legal: legal@acme.com · Support: support@acme.com · Press: press@acme.com</p>"
    routes = routes_for("acme.com", **{"https://acme.com/about": FakeResponse(text=HTML.format(page="about", body=body))})
    result = find_emails("Acme", "acme.com", session=FakeSession(routes))
    conf = {f.email: f.confidence for f in result.emails}
    assert conf["legal@acme.com"] == 30
    assert conf["support@acme.com"] == 55
    assert conf["press@acme.com"] == 70


# ---------------------------------------------------------------------------
# Convention learning + pattern inference
# ---------------------------------------------------------------------------
def test_local_shape_classification():
    assert local_shape("jane.smith") == "dot_two"
    assert local_shape("j.smith") == "dot_initial"
    assert local_shape("jane_smith") == "underscore_two"
    assert local_shape("janesmith") == "plain"
    assert local_shape("a.b.c") is None
    assert local_shape("ab") is None


def test_learn_conventions_skips_roles_and_freemail():
    conventions = learn_conventions([
        "jane.smith@acme.com", "m.brown@acme.com", "info@acme.com",
        "noreply@acme.com", "dave@gmail.com",
    ])
    assert conventions == ["dot_two", "dot_initial"]


def test_pattern_inference_uses_learned_convention_first():
    patterns = infer_patterns("Mary", "Brown", "acme.com", learned=["dot_initial"])
    assert patterns[0].email == "m.brown@acme.com"
    assert patterns[0].confidence == 65
    assert all(p.source == "pattern" and p.verification == "pattern" for p in patterns)
    # fallbacks still present, ranked below the learned shape
    assert any(p.email == "mary.brown@acme.com" and p.confidence == 45 for p in patterns)


def test_pattern_inference_without_convention_falls_back():
    patterns = infer_patterns("Mary", "Brown", "acme.com", learned=[])
    assert patterns[0].email == "mary.brown@acme.com"
    assert patterns[0].confidence == 45


def test_find_emails_learns_convention_and_infers_for_supplied_name():
    team_html = "<p>Jane Smith — <a href='mailto:j.smith@acme.com'>email</a> · Sam Brown — s.brown@acme.com</p>"
    routes = routes_for("acme.com", **{"https://acme.com/team": FakeResponse(text=HTML.format(page="team", body=team_html))})
    result = find_emails("Acme", "acme.com", first_name="Mary", last_name="Brown", session=FakeSession(routes))
    assert result.conventions == ["dot_initial"]
    inferred = next(f for f in result.emails if f.email == "m.brown@acme.com")
    assert inferred.confidence == 65 and inferred.source == "pattern"
    # the harvested j.smith@ gets the person bonus for the supplied name? No —
    # supplied name is Mary Brown; j.smith does not match. m.brown@ does.
    mb = next(f for f in result.emails if f.email == "m.brown@acme.com")
    assert mb.kind == "pattern"
    top_contact = result.contacts[0]
    assert top_contact["email"] == "m.brown@acme.com" or top_contact["confidence"] >= 65
    assert top_contact["name"] == "" or top_contact["email"] == "m.brown@acme.com"


def test_name_bonus_for_matching_harvested_email():
    body = "<p><a href='mailto:mary.brown@acme.com'>Mary Brown</a></p>"
    routes = routes_for("acme.com", **{"https://acme.com/team": FakeResponse(text=HTML.format(page="team", body=body))})
    with_name = find_emails("Acme", "acme.com", first_name="Mary", last_name="Brown", session=FakeSession(routes))
    without = find_emails("Acme", "acme.com", session=FakeSession(routes))
    m_with = next(f for f in with_name.emails if f.email == "mary.brown@acme.com")
    m_without = next(f for f in without.emails if f.email == "mary.brown@acme.com")
    assert m_with.kind == "person"
    assert (m_with.confidence, m_without.confidence) == (97, 92)  # 88+4+8 vs 88+4


# ---------------------------------------------------------------------------
# Posture + degradation contracts
# ---------------------------------------------------------------------------
def test_robots_disallow_blocks_all_and_degrades():
    routes = routes_for("acme.com", **{"https://acme.com/robots.txt": FakeResponse(text="User-agent: *\nDisallow: /\n")})
    session = FakeSession(routes)
    result = find_emails("Acme", "acme.com", session=session)
    assert result.pages_fetched == 0
    assert all(u.endswith("robots.txt") for u in session.requested)
    assert any("robots.txt" in d for d in result.degradations)


def test_forbidden_urls_never_fetched():
    for marker in ("linkedin.com", "indeed.com", "glassdoor.com"):
        assert ef.is_forbidden_url(f"https://www.{marker}/acme")


def test_http_failures_logged_without_killing_the_rest():
    routes = routes_for("acme.com", **{"https://acme.com/team": FakeResponse(status=500, text="boom")})
    result = find_emails("Acme", "acme.com", session=FakeSession(routes))
    outcomes = {r.url: r.outcome for r in result.fetch_log}
    assert outcomes["https://acme.com/team"] == "http_error"
    assert outcomes["https://acme.com/contact"] == "fetched"


def test_empty_domain_degrades_without_fetching():
    session = FakeSession()
    result = find_emails("Mystery Co", "", session=session)
    assert session.requested == []
    assert any("no domain known" in d for d in result.degradations)


def test_nothing_found_degrades_loudly():
    session = FakeSession(routes_for("acme.com"))  # pages exist, zero emails
    result = find_emails("Acme", "acme.com", session=session)
    assert result.emails == [] and result.contacts == []
    assert any("no public email found" in d for d in result.degradations)
    assert any("pattern inference needs a supplied name" in d for d in result.degradations)


def test_one_fetch_per_page_no_crawling():
    session = FakeSession(routes_for("acme.com"))
    find_emails("Acme", "acme.com", session=session)
    pages = [u for u in session.requested if not u.endswith("robots.txt")]
    assert len(pages) == len(set(pages))


# ---------------------------------------------------------------------------
# MX verification (monkeypatched)
# ---------------------------------------------------------------------------
def test_mx_fail_caps_everything():
    monkey = pytest.MonkeyPatch()
    monkey.setattr(ef, "mx_status", lambda domain: "fail")
    try:
        routes = routes_for("acme.com", **{"https://acme.com/contact": FakeResponse(text=HTML.format(page="contact", body="<a href='mailto:jane@acme.com'>J</a>"))})
        result = find_emails("Acme", "acme.com", session=FakeSession(routes))
        jane = next(f for f in result.emails if f.email == "jane@acme.com")
        assert jane.confidence == 15 and jane.verification == "mx_fail"
        assert any("MX/A record" in d for d in result.degradations)
    finally:
        monkey.undo()


def test_mx_ok_boosts_and_labels():
    monkey = pytest.MonkeyPatch()
    monkey.setattr(ef, "mx_status", lambda domain: "ok")
    try:
        routes = routes_for("acme.com", **{"https://acme.com/contact": FakeResponse(text=HTML.format(page="contact", body="Mail: hello@acme.io") )})
        result = find_emails("Acme", "acme.com", session=FakeSession(routes))
        hello = next(f for f in result.emails if f.email == "hello@acme.io")
        assert hello.verification == "mx_ok"
        assert hello.confidence == 73  # 70 generic-role cap + 3 mx; context gated above 80
    finally:
        monkey.undo()


# ---------------------------------------------------------------------------
# Output contract
# ---------------------------------------------------------------------------
CONTACTLEAD_FIELDS = {"name", "email", "position", "seniority", "department", "confidence", "linkedin_url"}


def test_contacts_are_contactlead_shaped():
    body = "<a href='mailto:jane.smith@acme.com'>Jane</a> <a href='mailto:sam@acme.com'>Sam</a> <a href='mailto:a@acme.com'>A</a> <a href='mailto:b@acme.com'>B</a> <a href='mailto:c@acme.com'>C</a> <a href='mailto:d@acme.com'>D</a>"
    routes = routes_for("acme.com", **{"https://acme.com/team": FakeResponse(text=HTML.format(page="team", body=body))})
    result = find_emails("Acme", "acme.com", session=FakeSession(routes))
    assert len(result.contacts) == 5  # capped at top-5
    for contact in result.contacts:
        assert CONTACTLEAD_FIELDS <= set(contact)
        assert 0 <= contact["confidence"] <= 97
        assert contact["position"] == "" and contact["linkedin_url"] == ""
    # ranked best-first
    confs = [c["confidence"] for c in result.contacts]
    assert confs == sorted(confs, reverse=True)


def test_dedup_keeps_best_confidence():
    routes = routes_for("acme.com", **{
        "https://acme.com": FakeResponse(text=HTML.format(page="home", body="Email: jane.smith@acme.com")),
        "https://acme.com/contact": FakeResponse(text=HTML.format(page="contact", body="<a href='mailto:jane.smith@acme.com'>Jane</a>")),
    })
    result = find_emails("Acme", "acme.com", session=FakeSession(routes))
    jane = [f for f in result.emails if f.email == "jane.smith@acme.com"]
    assert len(jane) == 1
    assert jane[0].confidence == 92  # mailto + contact-context beat the homepage text match (88+4)
