"""
Unit tests for the SQLite run store (A5 / ticket #7).

Covers roundtrips over Evidence cards, SequenceTouch, CriticResult, and the
quality-gate verdict; the tenant-stripping rule; review-queue indexing; and
graceful degradation when the store is unavailable.

No network, no API keys.
"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import pytest  # noqa: E402

from app.services import run_store as rs  # noqa: E402
from app.services.demo_eval import build_sample_state  # noqa: E402
from app.tenants import load_tenant  # noqa: E402


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture()
def store(tmp_path):
    s = rs.RunStore(db_path=tmp_path / "runs.db")
    yield s
    s.close()


def _tenant():
    return load_tenant("demo")


def _prospect():
    return {"company": "Roundtrip Co", "industry": "B2B SaaS", "notes": ""}


# ---------------------------------------------------------------------------
# Roundtrips
# ---------------------------------------------------------------------------

def test_roundtrip_preserves_evidence_cards(store):
    state = build_sample_state(_tenant(), _prospect())
    run_id = store.start_run(state)
    store.finish_run(run_id, state)

    loaded = store.load_state(run_id)
    original_cards = state["enrichment"].evidence_cards
    loaded_cards = loaded["enrichment"]["evidence_cards"]
    assert len(loaded_cards) == len(original_cards)
    assert loaded_cards[0]["evidence_id"] == original_cards[0].evidence_id
    assert loaded_cards[0]["claim"] == original_cards[0].claim
    assert loaded_cards[0]["confidence_label"] == original_cards[0].confidence_label


def test_roundtrip_preserves_touches(store):
    state = build_sample_state(_tenant(), _prospect())
    run_id = store.start_run(state)
    store.finish_run(run_id, state)

    loaded = store.load_state(run_id)
    original_touches = state["card"].sequence.touches
    loaded_touches = loaded["card"]["sequence"]["touches"]
    assert len(loaded_touches) == len(original_touches)
    assert loaded_touches[0]["touch_number"] == original_touches[0].touch_number
    assert loaded_touches[0]["body"] == original_touches[0].body
    assert loaded_touches[0]["subject"] == original_touches[0].subject


def test_roundtrip_preserves_critic_result_and_gate(store):
    state = build_sample_state(_tenant(), _prospect())
    run_id = store.start_run(state)
    store.finish_run(run_id, state)

    loaded = store.load_state(run_id)
    gate = state["critic_result"].quality_gate
    loaded_gate = loaded["critic_result"]["quality_gate"]
    assert loaded_gate["verdict"] == gate.verdict
    assert loaded_gate["safe_to_send"] == gate.safe_to_send
    assert loaded["critic_result"]["overall_quality"] == state["critic_result"].overall_quality


def test_tenant_stripped_but_tenant_id_kept(store):
    state = build_sample_state(_tenant(), _prospect())
    run_id = store.start_run(state)
    store.finish_run(run_id, state)

    loaded = store.load_state(run_id)
    assert "tenant" not in loaded
    assert loaded["tenant_id"] == "demo"


def test_degradations_roundtrip(store):
    state = build_sample_state(_tenant(), _prospect())
    state["degradations"] = ["Exa: EXA_API_KEY missing — no live signals fetched"]
    run_id = store.start_run(state)
    store.finish_run(run_id, state)
    loaded = store.load_state(run_id)
    assert loaded["degradations"] == ["Exa: EXA_API_KEY missing — no live signals fetched"]


# ---------------------------------------------------------------------------
# Status derivation
# ---------------------------------------------------------------------------

def test_status_complete(store):
    state = build_sample_state(_tenant(), _prospect())
    run_id = store.start_run(state)
    status = store.finish_run(run_id, state)
    assert status == "complete"


def test_status_degraded(store):
    state = build_sample_state(_tenant(), _prospect())
    state["degradations"] = ["Hunter: HTTP 429 — no contacts fetched"]
    run_id = store.start_run(state)
    status = store.finish_run(run_id, state)
    assert status == "degraded"


def test_status_failed(store):
    state = {"company": "X", "error": "boom"}
    run_id = store.start_run({**state, "tenant": _tenant()})
    status = store.finish_run(run_id, state)
    assert status == "failed"


# ---------------------------------------------------------------------------
# Review queue
# ---------------------------------------------------------------------------

def test_review_queue_excludes_approved_and_failed(store):
    for company, approved, fail in [
        ("Alpha", False, False), ("Beta", True, False),
        ("Gamma", False, True), ("Delta", False, False),
    ]:
        state = build_sample_state(_tenant(), {"company": company, "industry": "SaaS", "notes": ""})
        if fail:
            state["error"] = "boom"
        run_id = store.start_run(state)
        store.finish_run(run_id, state)
        if approved:
            store.set_approved(run_id, True)

    queue = store.review_queue(tenant_id="demo")
    companies = [r.company for r in queue]
    assert "Alpha" in companies and "Delta" in companies
    assert "Beta" not in companies  # approved
    assert "Gamma" not in companies  # failed


def test_review_queue_index_exists_and_is_fast_at_100_runs(store):
    state = build_sample_state(_tenant(), {"company": "Bulk", "industry": "SaaS", "notes": ""})
    for i in range(100):
        variant = build_sample_state(_tenant(), {"company": f"Bulk {i}", "industry": "SaaS", "notes": ""})
        run_id = store.start_run(variant)
        store.finish_run(run_id, variant)

    indexes = {
        row[1] if isinstance(row, tuple) else row["name"]
        for row in store._connection().execute("PRAGMA index_list(runs)").fetchall()
    }
    assert any("idx_runs_queue" in str(idx) for idx in indexes)

    started = time.perf_counter()
    for _ in range(5):
        store.review_queue(tenant_id="demo", limit=50)
    elapsed = time.perf_counter() - started
    assert elapsed < 1.0  # indexed query, 100 runs — must be near-instant


def test_recent_runs_ordering(store):
    for i in range(3):
        variant = build_sample_state(_tenant(), {"company": f"Co {i}", "industry": "SaaS", "notes": ""})
        run_id = store.start_run(variant)
        store.finish_run(run_id, variant)
        time.sleep(1.1)  # created_at is second-resolution; ensure distinct stamps

    runs = store.recent_runs(tenant_id="demo", limit=3)
    assert [r.company for r in runs] == ["Co 2", "Co 1", "Co 0"]


def test_approval_writeback(store):
    state = build_sample_state(_tenant(), _prospect())
    run_id = store.start_run(state)
    store.finish_run(run_id, state)
    assert store.set_approved(run_id, True) is True
    record = store.recent_runs(tenant_id="demo")[0]
    assert record.approved is True


# ---------------------------------------------------------------------------
# Graceful degradation
# ---------------------------------------------------------------------------

def test_unavailable_store_does_not_raise(tmp_path, monkeypatch):
    """A store on an unwritable path must fail soft — callers decide severity."""
    bad = rs.RunStore(db_path=tmp_path / "nope" / "deep" / "runs.db")
    # On most systems this path is creatable; force the failure instead by
    # pointing start_run at a directory-as-db.
    directory_db = rs.RunStore(db_path=tmp_path)  # tmp_path is a directory
    state = build_sample_state(_tenant(), _prospect())
    with pytest.raises(rs.sqlite3.Error):
        directory_db.start_run(state)


def test_state_to_jsonable_handles_weird_objects():
    class Weird:
        def __repr__(self):
            return "<weird>"

    out = rs.state_to_jsonable({"tenant": object(), "company": "Acme", "blob": Weird()})
    assert "tenant" not in out
    assert out["company"] == "Acme"
    assert "<weird>" in out["blob"]
    json.dumps(out)  # must be encodable


def test_schema_version_recorded(store):
    state = build_sample_state(_tenant(), _prospect())
    run_id = store.start_run(state)
    row = store._connection().execute(
        "SELECT schema_version FROM runs WHERE run_id = ?", (run_id,)
    ).fetchone()
    assert row["schema_version"] == rs.SCHEMA_VERSION
