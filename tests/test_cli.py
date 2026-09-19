"""
Offline tests for the agent-facing CLI (app/cli.py).

No network, no API keys: exercises check / runs / approve / report happy and
error paths plus the run command's missing-key environment guard, including
the exit-code contract (0 ok / 1 failed / 2 env-or-usage) and the --json
purity guarantee (stdout carries exactly one JSON document, human lines on
stderr).
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import app.cli as cli  # noqa: E402
from app.services.run_store import RunStore  # noqa: E402


# ---------------------------------------------------------------------------
# check
# ---------------------------------------------------------------------------
def test_check_all_tenants_ok_exit_zero(capsys):
    assert cli.main(["check"]) == 0
    out = capsys.readouterr().out
    assert "demo: OK" in out


def test_check_json_stdout_is_single_document(capsys):
    assert cli.main(["check", "--json"]) == 0
    captured = capsys.readouterr()
    doc = json.loads(captured.out)  # raises unless stdout is ONE json doc
    assert doc["ok"] is True
    assert any(t["tenant"] == "demo" for t in doc["tenants"])
    assert captured.err  # human summary went to stderr


def test_check_unknown_tenant_fails_with_exit_one(capsys):
    assert cli.main(["check", "--tenant", "does-not-exist"]) == 1
    assert "FAIL" in capsys.readouterr().out


def test_route_problems_mirror_check_tenant_rules():
    """Route validation reuses the same rules as scripts/check_tenant.py."""
    from app.tenants.loader import load_tenant

    assert cli._route_problems(load_tenant("demo")) == []


# ---------------------------------------------------------------------------
# run — environment guard only (a live pipeline is never started here)
# ---------------------------------------------------------------------------
def test_run_without_api_key_exits_two_and_never_starts_pipeline(monkeypatch, capsys):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "")
    assert cli.main(["run", "--company", "Guard Test"]) == 2
    err = capsys.readouterr().err
    assert "ANTHROPIC_API_KEY is not set" in err


def test_run_without_api_key_json_mode_stdout_stays_clean(monkeypatch, capsys):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "")
    assert cli.main(["run", "--company", "Guard Test", "--json"]) == 2
    captured = capsys.readouterr()
    assert captured.out == ""  # nothing pollutes the stdout channel


def test_run_unknown_tenant_exits_one(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key-not-used")
    assert cli.main(["run", "--company", "X", "--tenant", "does-not-exist"]) == 1


# ---------------------------------------------------------------------------
# runs / approve / report against a temp store
# ---------------------------------------------------------------------------
def _seed_run(store: RunStore) -> str:
    """Seed one complete (degradation-free, gate-passed) run so derive_status
    yields 'complete' — i.e. a record the review queue legitimately lists."""
    from app.tenants.loader import load_tenant

    tenant = load_tenant("demo")
    run_id = store.start_run({"tenant": tenant, "company": "Test Co", "industry": "SaaS"})
    store.finish_run(run_id, {
        "company": "Test Co",
        "industry": "SaaS",
        "tenant_id": "demo",
        "critic_result": {"quality_gate": {"verdict": "approved", "safe_to_send": True}},
        "degradations": [],
    })
    return run_id


@pytest.fixture()
def seeded_store(tmp_path):
    """RunStore pointed at a temp DB with one finished run seeded."""
    store = RunStore(db_path=tmp_path / "runs.db")
    run_id = _seed_run(store)
    yield store, run_id
    store.close()


def test_runs_lists_seeded_run(seeded_store, monkeypatch, capsys):
    store, run_id = seeded_store
    monkeypatch.setattr(cli, "RunStore", lambda: store)
    assert cli.main(["runs"]) == 0
    assert run_id in capsys.readouterr().out


def test_runs_json_purity(seeded_store, monkeypatch, capsys):
    store, _ = seeded_store
    monkeypatch.setattr(cli, "RunStore", lambda: store)
    assert cli.main(["runs", "--json"]) == 0
    captured = capsys.readouterr()
    doc = json.loads(captured.out)
    assert isinstance(doc, list) and len(doc) == 1
    assert doc[0]["company"] == "Test Co"


def test_runs_review_queue_flag(seeded_store, monkeypatch, capsys):
    store, run_id = seeded_store
    monkeypatch.setattr(cli, "RunStore", lambda: store)
    assert cli.main(["runs", "--queue"]) == 0
    assert run_id in capsys.readouterr().out


def test_approve_unknown_run_exits_one(seeded_store, monkeypatch, capsys):
    store, _ = seeded_store
    monkeypatch.setattr(cli, "RunStore", lambda: store)
    assert cli.main(["approve", "no-such-run"]) == 1
    assert "not found" in capsys.readouterr().err


def test_approve_marks_run_and_prints_confirmation(seeded_store, monkeypatch, capsys):
    store, run_id = seeded_store
    monkeypatch.setattr(cli, "RunStore", lambda: store)
    assert cli.main(["approve", run_id]) == 0
    capsys.readouterr()
    records = store.recent_runs(limit=5)
    assert records[0].approved is True


def test_approve_json_single_document(seeded_store, monkeypatch, capsys):
    store, run_id = seeded_store
    monkeypatch.setattr(cli, "RunStore", lambda: store)
    assert cli.main(["approve", run_id, "--json"]) == 0
    captured = capsys.readouterr()
    doc = json.loads(captured.out)
    assert doc == {"run_id": run_id, "approved": True}
    assert captured.err  # "Approved ..." line went to stderr


def test_report_unknown_run_exits_one(seeded_store, monkeypatch, capsys):
    store, _ = seeded_store
    monkeypatch.setattr(cli, "RunStore", lambda: store)
    assert cli.main(["report", "no-such-run"]) == 1


def test_report_writes_markdown_file(seeded_store, monkeypatch, tmp_path, capsys):
    store, run_id = seeded_store
    monkeypatch.setattr(cli, "RunStore", lambda: store)
    out = tmp_path / "report.md"
    assert cli.main(["report", run_id, "--out", str(out)]) == 0
    text = out.read_text(encoding="utf-8")
    assert "Test Co" in text
    assert "BDR Account Report" in text


def test_report_json_path_is_absolute_and_documented(seeded_store, monkeypatch, tmp_path, capsys):
    store, run_id = seeded_store
    monkeypatch.setattr(cli, "RunStore", lambda: store)
    out = tmp_path / "report.json-mode.md"
    assert cli.main(["report", run_id, "--out", str(out), "--json"]) == 0
    captured = capsys.readouterr()
    doc = json.loads(captured.out)
    assert doc["run_id"] == run_id
    assert Path(doc["report_path"]).name == "report.json-mode.md"


# ---------------------------------------------------------------------------
# parser contract
# ---------------------------------------------------------------------------
def test_parser_requires_a_command(capsys):
    with pytest.raises(SystemExit) as exc:
        cli.main([])
    assert exc.value.code != 0


def test_exit_code_constants_are_documented():
    assert (cli.EXIT_OK, cli.EXIT_FAILED, cli.EXIT_ENV) == (0, 1, 2)
