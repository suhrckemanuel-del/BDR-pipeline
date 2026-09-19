"""
Unit tests for the council-vs-single-rater comparison harness
(app/services/council_eval.py).

Pure-function tests plus run_council_eval exercised with injected fake
workflow/critic runners: no API calls, no network, no real pipeline runs.

Run:
    .venv/Scripts/python.exe -m pytest tests -q
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.agents.critic import (  # noqa: E402
    CriticResult,
    QualityGate,
    TouchScore,
)
from app.services.council_eval import (  # noqa: E402
    CSV_FIELDS,
    build_comparison_row,
    build_markdown_report,
    compare_arms,
    CouncilEvalResult,
    error_row,
    extract_arm_metrics,
    patch_tenant_critic_config,
    run_council_eval,
    summarize_results,
)
from app.tenants import load_tenant  # noqa: E402


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

def make_gate(verdict: str) -> QualityGate:
    return QualityGate(
        verdict=verdict,  # type: ignore[arg-type]
        safe_to_send=(verdict == "approved"),
        confidence="medium",
        summary="test gate",
    )


def make_critic_result(
    verdict: str = "approved",
    overall: float = 4.0,
    council_size: int = 1,
    scorer_overall_scores: list[float] | None = None,
    disagreements: list[str] | None = None,
    agreement_level: str = "single_rater",
    touches: list[tuple[int, int, int, int, int]] | None = None,
) -> CriticResult:
    """touches: list of (number, pain, proof, cta, voice)."""
    if touches is None:
        touches = [(1, 4, 4, 4, 4), (2, 4, 3, 4, 5)]
    touch_scores = [
        TouchScore(
            touch_number=n,
            pain_specificity=p,
            proof_relevance=pr,
            cta_clarity=c,
            human_voice=v,
        )
        for n, p, pr, c, v in touches
    ]
    return CriticResult(
        touch_scores=touch_scores,
        overall_quality=overall,
        rewrites_applied=0,
        critique_summary="test",
        quality_gate=make_gate(verdict),
        council_size=council_size,
        scorer_overall_scores=scorer_overall_scores or [],
        disagreements=disagreements or [],
        agreement_level=agreement_level,  # type: ignore[arg-type]
    )


# ---------------------------------------------------------------------------
# patch_tenant_critic_config
# ---------------------------------------------------------------------------

def test_patch_tenant_critic_config():
    tenant = load_tenant("demo")
    assert tenant.critic.council_size == 3  # demo default

    patched = patch_tenant_critic_config(tenant, 1)
    assert patched.critic.council_size == 1
    # Other critic settings preserved
    assert patched.critic.council_models == tenant.critic.council_models
    assert patched.critic.best_of_n == tenant.critic.best_of_n
    # Original untouched
    assert tenant.critic.council_size == 3
    # Rest of tenant shared, not deep-copied
    assert patched.brand is tenant.brand
    assert patched.business is tenant.business


# ---------------------------------------------------------------------------
# extract_arm_metrics
# ---------------------------------------------------------------------------

def test_extract_arm_metrics_single_rater():
    result = make_critic_result(
        verdict="approved",
        overall=4.0,
        council_size=1,
        agreement_level="single_rater",
    )
    m = extract_arm_metrics(result)
    assert m["gate_verdict"] == "approved"
    assert m["overall_quality"] == 4.0
    assert m["council_size"] == 1
    assert m["scorer_spread"] == 0.0
    assert m["disagreement_count"] == 0
    assert m["touch_count"] == 2
    assert m["touches_needing_rewrite"] == 0
    assert m["pain_mean"] == 4.0


def test_extract_arm_metrics_council():
    result = make_critic_result(
        verdict="needs_edit",
        overall=3.5,
        council_size=3,
        scorer_overall_scores=[3.0, 4.0, 3.5],
        disagreements=["T1.pain_specificity: scorers [2, 4]"],
        agreement_level="medium",
        touches=[(1, 3, 4, 2, 5), (2, 2, 3, 3, 4)],
    )
    m = extract_arm_metrics(result)
    assert m["gate_verdict"] == "needs_edit"
    assert m["council_size"] == 3
    assert m["scorer_spread"] == 1.0
    assert m["disagreement_count"] == 1
    assert m["agreement_level"] == "medium"
    assert m["touches_needing_rewrite"] == 2  # pain=3 fails? no: <3 fails -> touch1 cta=2, touch2 pain=2
    assert m["cta_mean"] == 2.5


def test_extract_arm_metrics_empty_result():
    m = extract_arm_metrics(CriticResult())
    assert m["gate_verdict"] == ""
    assert m["overall_quality"] == 0.0
    assert m["touch_count"] == 0
    assert m["disagreement_count"] == 0


# ---------------------------------------------------------------------------
# compare_arms
# ---------------------------------------------------------------------------

def test_compare_same_verdict():
    single = extract_arm_metrics(make_critic_result(verdict="approved", overall=4.0))
    council = extract_arm_metrics(
        make_critic_result(
            verdict="approved",
            overall=4.2,
            council_size=3,
            scorer_overall_scores=[4.0, 4.5, 4.2],
            agreement_level="high",
        )
    )
    c = compare_arms(single, council)
    assert c["verdict_shift"] == "same"
    assert c["quality_delta"] == 0.2
    assert c["disagreement_count"] == 0
    assert c["agreement_level"] == "high"


def test_compare_council_stricter():
    single = extract_arm_metrics(make_critic_result(verdict="approved", overall=4.0))
    council = extract_arm_metrics(make_critic_result(verdict="needs_edit", overall=3.2))
    c = compare_arms(single, council)
    assert c["verdict_shift"] == "council_stricter"
    assert c["quality_delta"] == -0.8


def test_compare_council_looser():
    single = extract_arm_metrics(make_critic_result(verdict="do_not_send_yet", overall=2.0))
    council = extract_arm_metrics(make_critic_result(verdict="needs_edit", overall=2.6))
    c = compare_arms(single, council)
    assert c["verdict_shift"] == "council_looser"


def test_compare_disagreement_rate_denominator():
    # 2 touches x 4 dims = 8; 2 disagreements -> 0.25
    single = extract_arm_metrics(make_critic_result())
    council = extract_arm_metrics(
        make_critic_result(
            council_size=3,
            disagreements=["T1.a: [1, 4]", "T2.b: [2, 5]"],
            agreement_level="low",
        )
    )
    c = compare_arms(single, council)
    assert c["disagreement_count"] == 2
    assert c["disagreement_rate"] == 0.25


def test_compare_handles_missing_verdicts():
    single = extract_arm_metrics(CriticResult())
    council = extract_arm_metrics(CriticResult())
    c = compare_arms(single, council)
    assert c["verdict_shift"] == "same"
    assert c["verdict_single"] == ""


# ---------------------------------------------------------------------------
# summarize_results
# ---------------------------------------------------------------------------

def _row(**overrides):
    base = {
        "account": "A",
        "run_completed": "yes",
        "verdict_single": "approved",
        "verdict_council": "approved",
        "verdict_shift": "same",
        "quality_single": 4.0,
        "quality_council": 4.2,
        "quality_delta": 0.2,
        "risk_flags_single": 0,
        "risk_flags_council": 1,
        "disagreement_count": 1,
        "disagreement_rate": 0.125,
        "agreement_level": "medium",
    }
    base.update(overrides)
    return base


def test_summarize_empty():
    s = summarize_results([])
    assert s["accounts"] == 0
    assert s["completed_pairs"] == 0
    assert s["verdict_shifts"] == {}


def test_summarize_counts_and_averages():
    rows = [
        _row(),
        _row(verdict_shift="council_stricter", verdict_council="needs_edit", quality_delta=-0.5),
        _row(run_completed="no", notes_errors="boom"),
        _row(verdict_single="do_not_send_yet", verdict_council="do_not_send_yet", verdict_shift="same"),
    ]
    s = summarize_results(rows)
    assert s["accounts"] == 4
    assert s["completed_pairs"] == 3
    assert s["verdict_shifts"] == {"same": 2, "council_stricter": 1}
    assert s["council_stricter_count"] == 1
    assert s["same_verdict_count"] == 2
    assert s["high_risk_agreement_count"] == 1
    assert s["total_disagreements"] == 3
    # quality: (4.2 + 3.7 + 4.2) / 3 — but council_stricter row has quality_council 4.2
    # with default overrides, so council mean = (4.2 + 4.2 + 4.2) / 3 = 4.2
    assert s["avg_quality_council"] == 4.2


def test_summarize_agreement_distribution():
    rows = [_row(), _row(agreement_level="high"), _row(agreement_level="low")]
    s = summarize_results(rows)
    assert s["agreement_level_counts"] == {"medium": 1, "high": 1, "low": 1}


# ---------------------------------------------------------------------------
# build_comparison_row + error_row
# ---------------------------------------------------------------------------

def test_build_comparison_row():
    single_state = {"critic_result": make_critic_result(verdict="approved", overall=4.0)}
    council_state = {
        "critic_result": make_critic_result(
            verdict="approved",
            overall=4.1,
            council_size=3,
            scorer_overall_scores=[4.0, 4.2, 4.1],
            disagreements=["T1.pain_specificity: scorers [3, 5]"],
            agreement_level="medium",
        )
    }
    row = build_comparison_row("Globex", "B2B SaaS", single_state, council_state, 1.234)
    assert row["account"] == "Globex"
    assert row["run_completed"] == "yes"
    assert row["verdict_shift"] == "same"
    assert row["quality_delta"] == 0.1
    assert row["disagreement_count"] == 1
    assert row["runtime_seconds"] == "1.23"
    assert row["disagreements"] == ["T1.pain_specificity: scorers [3, 5]"]
    # All CSV fields present
    for field in CSV_FIELDS:
        assert field in row


def test_error_row_shape():
    row = error_row("X", "industry", "boom")
    assert row["run_completed"] == "no"
    assert row["notes_errors"] == "boom"
    # CSV writer fills missing fields as empty strings
    filled = {field: row.get(field, "") for field in CSV_FIELDS}
    assert filled["verdict_single"] == ""


# ---------------------------------------------------------------------------
# run_council_eval with injected fakes
# ---------------------------------------------------------------------------

def test_run_council_eval_with_fake_runners():
    tenant = load_tenant("demo")

    def fake_workflow(t, prospect):
        # Minimal completed state both arms can score.
        return {
            "tenant": t,
            "company": prospect.get("company", ""),
            "industry": prospect.get("industry", ""),
            "card": object(),  # presence check only
            "enrichment": object(),
            "error": None,
        }

    captured_sizes: list[int] = []

    def fake_critic(state, t, council_size):
        captured_sizes.append(council_size)
        if council_size == 1:
            result = make_critic_result(verdict="approved", overall=4.0)
        else:
            result = make_critic_result(
                verdict="needs_edit",
                overall=3.4,
                council_size=council_size,
                scorer_overall_scores=[3.0, 3.8, 3.4],
                disagreements=["T1.pain_specificity: scorers [2, 4]"],
                agreement_level="medium",
            )
        return {"critic_result": result, "error": None}

    result = run_council_eval(
        tenant_id="demo",
        max_accounts=2,
        council_size=3,
        workflow_runner=fake_workflow,
        critic_runner=fake_critic,
    )
    assert result.mode == "live"
    assert result.council_size == 3
    assert len(result.rows) == 2
    assert all(row["run_completed"] == "yes" for row in result.rows)
    assert captured_sizes == [1, 3, 1, 3]  # both arms per account

    summary = summarize_results(result.rows)
    assert summary["completed_pairs"] == 2
    assert summary["council_stricter_count"] == 2


def test_run_council_eval_records_workflow_error():
    def fake_workflow(t, prospect):
        return {"error": "enrichment failed"}

    result = run_council_eval(
        tenant_id="demo",
        max_accounts=1,
        council_size=3,
        workflow_runner=fake_workflow,
        critic_runner=lambda s, t, c: (_ for _ in ()).throw(AssertionError("must not be called")),
    )
    assert len(result.rows) == 1
    assert result.rows[0]["run_completed"] == "no"
    assert "enrichment failed" in result.rows[0]["notes_errors"]


def test_run_council_eval_records_exception():
    def fake_workflow(t, prospect):
        raise RuntimeError("network down")

    result = run_council_eval(
        tenant_id="demo",
        max_accounts=1,
        council_size=3,
        workflow_runner=fake_workflow,
    )
    assert result.rows[0]["run_completed"] == "no"
    assert "RuntimeError" in result.rows[0]["notes_errors"]


# ---------------------------------------------------------------------------
# Markdown report
# ---------------------------------------------------------------------------

def test_markdown_report_contains_key_sections():
    rows = [_row(account="Globex")]
    result = CouncilEvalResult(
        rows=rows, mode="live", tenant_id="demo", council_size=3,
    )
    summary = summarize_results(rows)
    md = build_markdown_report(result, summary)
    assert "# Council vs. Single-Rater Critic Comparison" in md
    assert "council_size: 3" in md
    assert "Globex" in md
    assert "Limitations" in md
    assert "No reply rates" in md


def test_markdown_report_empty():
    result = CouncilEvalResult(rows=[], mode="live", tenant_id="demo", council_size=3)
    summary = summarize_results([])
    md = build_markdown_report(result, summary)
    assert "_No comparison rows generated._" in md
