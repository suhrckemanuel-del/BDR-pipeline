"""
Unit tests for the multi-agent critic council (app/agents/critic.py).

Pure-function tests only: no API calls, no network, no Streamlit, no tenant
files. Everything runs against the aggregation logic and Pydantic schemas.

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
    SequenceCritique,
    TouchScore,
    _aggregate,
    judge_disagreement_level,
)


def make_critique(
    touch_number: int = 1,
    pain: int = 3,
    proof: int = 3,
    cta: int = 3,
    voice: int = 3,
    overall: float = 3.0,
    feedback: str = "",
    pain_critique: str = "",
    proof_critique: str = "",
    cta_critique: str = "",
    voice_critique: str = "",
    summary: str = "",
) -> SequenceCritique:
    ts = TouchScore(
        touch_number=touch_number,
        pain_specificity=pain,
        proof_relevance=proof,
        cta_clarity=cta,
        human_voice=voice,
        feedback=feedback,
        pain_critique=pain_critique,
        proof_critique=proof_critique,
        cta_critique=cta_critique,
        voice_critique=voice_critique,
    )
    return SequenceCritique(
        touch_scores=[ts], overall_quality=overall, critique_summary=summary
    )


# ---------------------------------------------------------------------------
# Single-rater passthrough (council_size: 1 equivalence)
# ---------------------------------------------------------------------------

def test_single_rater_passthrough():
    c = make_critique(pain=4, overall=4.0, summary="Solid copy.")
    agg, overalls, disagreements = _aggregate([c])
    assert agg is c
    assert overalls == [4.0]
    assert disagreements == []


def test_critic_result_defaults_are_backward_compatible():
    result = CriticResult()
    assert result.council_size == 1
    assert result.scorer_overall_scores == []
    assert result.disagreements == []
    assert result.agreement_level == "single_rater"
    assert result.quality_gate is None


# ---------------------------------------------------------------------------
# Median aggregation
# ---------------------------------------------------------------------------

def test_median_odd_count():
    critiques = [
        make_critique(pain=2, overall=2.5),
        make_critique(pain=5, overall=4.5),
        make_critique(pain=4, overall=4.0),
    ]
    agg, overalls, _ = _aggregate(critiques)
    assert agg.touch_scores[0].pain_specificity == 4
    assert overalls == [2.5, 4.5, 4.0]


def test_median_even_count():
    critiques = [
        make_critique(proof=2, overall=2.0),
        make_critique(proof=4, overall=4.0),
    ]
    agg, _, _ = _aggregate(critiques)
    # median([2, 4]) == 3.0
    assert agg.touch_scores[0].proof_relevance == 3


def test_all_dimensions_aggregated():
    critiques = [
        make_critique(pain=1, proof=5, cta=3, voice=4, overall=3.25),
        make_critique(pain=3, proof=2, cta=4, voice=2, overall=2.75),
        make_critique(pain=5, proof=4, cta=5, voice=3, overall=4.25),
    ]
    agg, _, _ = _aggregate(critiques)
    ts = agg.touch_scores[0]
    assert ts.pain_specificity == 3   # median([1, 3, 5])
    assert ts.proof_relevance == 4    # median([5, 2, 4])
    assert ts.cta_clarity == 4        # median([3, 4, 5])
    assert ts.human_voice == 3        # median([4, 2, 3])


def test_average_recomputed_by_validator():
    critiques = [
        make_critique(pain=1, proof=1, cta=1, voice=1, overall=1.0),
        make_critique(pain=5, proof=5, cta=5, voice=5, overall=5.0),
    ]
    agg, _, _ = _aggregate(critiques)
    ts = agg.touch_scores[0]
    assert (ts.pain_specificity, ts.proof_relevance, ts.cta_clarity, ts.human_voice) == (3, 3, 3, 3)
    assert ts.average == 3.0


def test_needs_rewrite_recomputed_from_medians():
    # median([2, 2, 4]) == 2 -> failing dimension, needs_rewrite True
    critiques = [
        make_critique(pain=2, overall=3.0),
        make_critique(pain=2, overall=3.0),
        make_critique(pain=4, overall=3.0),
    ]
    agg, _, _ = _aggregate(critiques)
    ts = agg.touch_scores[0]
    assert ts.pain_specificity == 2
    assert "pain_specificity" in ts.failing_dims
    assert ts.needs_rewrite is True


def test_overall_quality_is_mean_of_scorers():
    critiques = [
        make_critique(overall=3.0),
        make_critique(overall=4.0),
    ]
    agg, _, _ = _aggregate(critiques)
    assert agg.overall_quality == 3.5


# ---------------------------------------------------------------------------
# Disagreement detection
# ---------------------------------------------------------------------------

def test_disagreement_flagged_on_span_of_two():
    critiques = [
        make_critique(pain=2, overall=3.0),
        make_critique(pain=4, overall=3.0),
    ]
    _, _, disagreements = _aggregate(critiques)
    assert disagreements == ["T1.pain_specificity: scorers [2, 4]"]


def test_no_disagreement_on_span_of_one():
    critiques = [
        make_critique(pain=2, overall=3.0),
        make_critique(pain=3, overall=3.0),
    ]
    _, _, disagreements = _aggregate(critiques)
    assert disagreements == []


def test_disagreement_per_dimension():
    critiques = [
        make_critique(pain=1, voice=5, overall=3.0),
        make_critique(pain=4, voice=3, overall=3.0),
    ]
    _, _, disagreements = _aggregate(critiques)
    assert "T1.pain_specificity: scorers [1, 4]" in disagreements
    assert "T1.human_voice: scorers [5, 3]" in disagreements
    assert len(disagreements) == 2


# ---------------------------------------------------------------------------
# Critique prose selection
# ---------------------------------------------------------------------------

def test_first_non_empty_critique_chosen():
    critiques = [
        make_critique(pain=2, pain_critique="", overall=3.0),
        make_critique(pain=4, pain_critique="pain is generic", overall=3.0),
    ]
    agg, _, _ = _aggregate(critiques)
    assert agg.touch_scores[0].pain_critique == "pain is generic"


def test_feedback_takes_first_non_empty():
    critiques = [
        make_critique(feedback="", overall=3.0),
        make_critique(feedback="touch 1 is too vague", overall=3.0),
    ]
    agg, _, _ = _aggregate(critiques)
    assert agg.touch_scores[0].feedback == "touch 1 is too vague"


def test_summary_appends_disagreement_note():
    critiques = [
        make_critique(pain=1, summary="First scorer summary.", overall=3.0),
        make_critique(pain=4, summary="Second scorer summary.", overall=3.0),
    ]
    agg, _, disagreements = _aggregate(critiques)
    assert disagreements
    assert agg.critique_summary.startswith("First scorer summary.")
    assert "disagreement" in agg.critique_summary.lower()


def test_summary_unchanged_without_disagreement():
    critiques = [
        make_critique(pain=3, summary="Agreed summary.", overall=3.0),
        make_critique(pain=3, summary="Other summary.", overall=3.0),
    ]
    agg, _, _ = _aggregate(critiques)
    assert agg.critique_summary == "Agreed summary."


# ---------------------------------------------------------------------------
# Robustness
# ---------------------------------------------------------------------------

def test_missing_touch_from_one_scorer():
    """A scorer that dropped a touch must not crash aggregation."""
    full = make_critique(touch_number=1, pain=2, overall=3.0)
    partial = make_critique(pain=5, overall=3.0)
    partial.touch_scores = []  # scorer returned nothing for this touch

    agg, _, _ = _aggregate([full, partial])
    # median([2]) == 2
    assert agg.touch_scores[0].pain_specificity == 2


def test_multiple_touches_sorted_by_number():
    critiques = [
        SequenceCritique(
            touch_scores=[
                TouchScore(touch_number=2, pain_specificity=4),
                TouchScore(touch_number=1, pain_specificity=2),
            ],
            overall_quality=3.0,
            critique_summary="",
        ),
        SequenceCritique(
            touch_scores=[
                TouchScore(touch_number=2, pain_specificity=2),
                TouchScore(touch_number=1, pain_specificity=4),
            ],
            overall_quality=3.0,
            critique_summary="",
        ),
    ]
    agg, _, disagreements = _aggregate(critiques)
    assert [t.touch_number for t in agg.touch_scores] == [1, 2]
    assert len(disagreements) == 2  # both touches disagree on pain


def test_aggregate_rejects_empty_list():
    import pytest

    with pytest.raises(ValueError):
        _aggregate([])


# ---------------------------------------------------------------------------
# Agreement level helper
# ---------------------------------------------------------------------------

def test_judge_disagreement_level():
    assert judge_disagreement_level([]) == "high"
    assert judge_disagreement_level(["T1.pain_specificity: scorers [1, 4]"]) == "medium"
    assert judge_disagreement_level(["a", "b"]) == "low"
