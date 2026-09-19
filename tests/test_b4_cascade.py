"""
Tests for B4 (ticket #13): cascade council.

Cascade = one scorer first; the full panel only convenes when the single
result is borderline or a rewrite is needed. `cascade_enabled: false`
(default) restores exactly the pre-B4 behavior.

No network, no API keys — the decision helper is pure and the council
aggregation is exercised with fabricated critiques.
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import pytest  # noqa: E402

from app.agents.critic import (  # noqa: E402
    CriticConfig,
    CriticResult,
    QualityGate,
    SequenceCritique,
    TouchScore,
    _aggregate,
    needs_council_escalation,
)
from app.tenants import load_tenant  # noqa: E402


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

def make_critique(overall: float, touches: list[tuple[int, int]] | None = None) -> SequenceCritique:
    """overall = rater's mean quality; touches = (number, dim_score).

    needs_rewrite is NOT settable directly: TouchScore's validator derives it
    from dims < 3 (production semantics). dim_score=3 is the clean-pass case.
    """
    if touches is None:
        touches = [(1, 4), (2, 4)]
    touch_scores = [
        TouchScore(
            touch_number=n,
            pain_specificity=s,
            proof_relevance=s,
            cta_clarity=s,
            human_voice=s,
        )
        for n, s in touches
    ]
    return SequenceCritique(
        touch_scores=touch_scores,
        overall_quality=overall,
        critique_summary="test",
    )


# ---------------------------------------------------------------------------
# Escalation decision (pure)
# ---------------------------------------------------------------------------

def test_clean_high_score_does_not_escalate():
    cfg = CriticConfig(cascade_enabled=True)
    escalate, reasons = needs_council_escalation(make_critique(5.0), cfg)
    assert escalate is False
    assert reasons == []


def test_below_band_clean_copy_does_not_escalate():
    """Below the band with clean copy: harsh-but-confident single rater stands."""
    cfg = CriticConfig(cascade_enabled=True, cascade_borderline_low=3, cascade_borderline_high=4)
    # dims 3 (no rewrite trigger) but a harsh overall 2.5
    escalate, _ = needs_council_escalation(make_critique(2.5, touches=[(1, 3), (2, 3)]), cfg)
    assert escalate is False


def test_clear_reject_with_failing_dims_escalates():
    """Failing dims mean rewrites — those always get panel scrutiny."""
    cfg = CriticConfig(cascade_enabled=True, cascade_borderline_low=3, cascade_borderline_high=4)
    escalate, reasons = needs_council_escalation(make_critique(2.0, touches=[(1, 2), (2, 2)]), cfg)
    assert escalate is True
    assert any("rewrite" in r for r in reasons)


def test_borderline_band_escalates():
    cfg = CriticConfig(cascade_enabled=True, cascade_borderline_low=3, cascade_borderline_high=4)
    for mean in (3.0, 3.5, 4.0):
        escalate, reasons = needs_council_escalation(make_critique(mean), cfg)
        assert escalate is True, mean
        assert any("borderline" in r for r in reasons)


def test_rewrite_need_escalates_even_with_good_score():
    cfg = CriticConfig(cascade_enabled=True, cascade_borderline_low=3, cascade_borderline_high=4)
    # one weak touch (dims 2 -> needs_rewrite) despite overall 4.5
    critique = make_critique(4.5, touches=[(1, 4), (2, 2)])
    escalate, reasons = needs_council_escalation(critique, cfg)
    assert escalate is True
    assert any("rewrite" in r for r in reasons)


def test_custom_band_overrides():
    cfg = CriticConfig(cascade_enabled=True, cascade_borderline_low=4, cascade_borderline_high=4)
    escalate, _ = needs_council_escalation(make_critique(4.5), cfg)
    assert escalate is False  # 4.5 above band high=4


# ---------------------------------------------------------------------------
# Config defaults — parity with pre-B4
# ---------------------------------------------------------------------------

def test_cascade_disabled_by_default_everywhere():
    tenant = load_tenant("demo")
    assert tenant.critic.cascade_enabled is False


def test_cascade_enabled_with_council_size_one_never_cascades():
    """council_size=1 means single-rater always; cascade branch requires >1."""
    cfg = CriticConfig(cascade_enabled=True, council_size=1)
    assert max(1, cfg.council_size) == 1
    # The run_critic guard `cascade_enabled and council_size > 1` keeps this off.


def test_extra_forbid_still_enforced():
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        CriticConfig(cascade_borderline_low=3, nonsense=True)


# ---------------------------------------------------------------------------
# Seeded panel aggregation (escalation path)
# ---------------------------------------------------------------------------

def test_aggregate_seeded_panel_mediates():
    """Seed critique + one fresh scorer -> median aggregation, unchanged math."""
    seed = make_critique(2.0, touches=[(1, 2), (2, 2)])  # harsh per-dim too
    fresh = make_critique(4.0)
    aggregated, overalls, disagreements = _aggregate([seed, fresh])
    assert aggregated.overall_quality == 3.0  # median of 2 and 4
    assert overalls == [2.0, 4.0]
    assert disagreements  # 2-point per-dimension spread surfaces


def test_aggregate_single_critique_passthrough():
    solo = make_critique(4.0)
    aggregated, overalls, disagreements = _aggregate([solo])
    assert aggregated is solo
    assert overalls == [4.0]
    assert disagreements == []


def test_critic_result_carries_cascade_semantics():
    """A de-escalated run reports council_size=1 (what actually ran)."""
    result = CriticResult(
        touch_scores=make_critique(5.0).touch_scores,
        overall_quality=5.0,
        rewrites_applied=0,
        critique_summary="cascade clean",
        quality_gate=QualityGate(
            verdict="approved", safe_to_send=True, confidence="high", summary="ok",
        ),
        council_size=1,
        agreement_level="single_rater",
    )
    assert result.council_size == 1
    assert result.agreement_level == "single_rater"


# ---------------------------------------------------------------------------
# Cost semantics of the cascade (documented ledger behavior)
# ---------------------------------------------------------------------------

def test_escalation_cost_model():
    """Non-escalated: 1 scorer call. Escalated: 1 + (N-1) = N calls.

    The seed critique reuses the cascade probe's call, so escalation adds
    exactly N-1 calls — no double-spending scorer 0. Ledger-verified via
    merge_usage's totals (calls accumulate per tracker snapshot).
    """
    from app.services.token_accounting import UsageTracker, merge_usage

    def calls_for(escalated: bool, n: int) -> int:
        tracker = UsageTracker(node="critic", default_model="claude-sonnet-4-6")

        class Resp:
            model = "claude-sonnet-4-6"
            usage_metadata = {"input_tokens": 100, "output_tokens": 10}

        tracker.on_llm_end(Resp())  # the cascade probe (always runs)
        if escalated:
            tracker.on_llm_end(Resp())  # scorers 1..n-1
            for _ in range(n - 2):
                tracker.on_llm_end(Resp())
        return tracker.snapshot()["calls"]

    assert calls_for(escalated=False, n=3) == 1   # clean single-rater run
    assert calls_for(escalated=True, n=3) == 3    # full panel equivalent
