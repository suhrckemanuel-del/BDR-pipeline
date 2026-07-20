"""
Offline regression checks for the composite account-scoring service.

Runs fixture inputs through app/services/account_scoring.py and asserts the
weighted arithmetic, bounds, determinism, tenant-weight overrides, backward
compatibility (no scoring_weights in config -> historical defaults), and the
score_components_sum eval gate. No network, no API keys, no LLM. A scratch
BDR_DB_PATH keeps the real pipeline/bdr.db untouched. Exit 1 on any failure.

    python scripts/check_scoring.py
"""
from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

_SCRATCH = tempfile.mkdtemp(prefix="bdr-check-scoring-")
os.environ["BDR_DB_PATH"] = str(Path(_SCRATCH) / "check_scoring.db")

from app.agents.state import (  # noqa: E402
    ContactLead,
    EvidenceCard,
    ICPClassification,
)
from app.services.account_scoring import (  # noqa: E402
    DEFAULT_WEIGHTS,
    compute_account_score,
    effective_weights,
    expected_overall,
)
from app.services.demo_eval import build_sample_state  # noqa: E402
from app.services.pipeline_evals import evaluate_state  # noqa: E402
from app.tenants import load_tenant  # noqa: E402
from app.tenants.schema import ICPConfig, ScoringWeights  # noqa: E402

COMPONENT_NAMES = (
    "icp_fit",
    "pain_evidence",
    "trigger_strength",
    "contact_confidence",
    "evidence_quality",
)


def _fixture_inputs() -> dict:
    """A fixed, fully deterministic scoring input set."""
    cards = [
        EvidenceCard(
            evidence_id="live_signal-1",
            claim="Acme announced an efficiency program during earnings.",
            source_title="Acme earnings call",
            source_url="https://example.com/earnings",
            source_type="live_signal",
            support_type="observed",
            confidence_label="high",
            confidence_score=85,
            excerpt="Cost reduction and operations transformation under way.",
            safe_to_use=True,
        ),
        EvidenceCard(
            evidence_id="job_signal-1",
            claim="Acme is hiring a Head of Revenue Operations.",
            source_title="LinkedIn job posting",
            source_url="https://example.com/job",
            source_type="job_signal",
            support_type="observed",
            confidence_label="medium",
            confidence_score=65,
            excerpt="Open role: revenue operations, reporting and workflow ownership.",
            safe_to_use=True,
        ),
        EvidenceCard(
            evidence_id="icp-score-1",
            claim="Composite ICP score is 70/100.",
            source_title="Internal ICP scoring model",
            source_url="",
            source_type="icp_score",
            support_type="derived",
            confidence_label="low",
            confidence_score=40,
            excerpt="Derived internal fit score.",
            safe_to_use=False,
        ),
    ]
    contacts = [
        ContactLead(
            name="Jordan Demo",
            email="jordan@example.com",
            position="VP Revenue Operations",
            seniority="executive",
            confidence=88,
        ),
    ]
    icp = ICPClassification(
        tier=1,
        tier_label="Tier 1 — Strategic Fit",
        rationale="Fixture classification.",
        score=70,
        score_breakdown={"fixture": 70},
    )
    return {
        "industry": "B2B SaaS",
        "contacts": contacts,
        "job_signals": [],
        "evidence_cards": cards,
        "icp": icp,
        "manual_trigger": "",
    }


def main() -> int:
    failures: list[str] = []

    def check(name: str, condition: bool, detail: str = "") -> None:
        print(f"{'PASS' if condition else 'FAIL'}  {name}" + (f"  {detail}" if detail and not condition else ""))
        if not condition:
            failures.append(name)

    tenant = load_tenant("demo")
    fixture = _fixture_inputs()

    # ----- 1. Backward compat: demo tenant has no scoring_weights key --------
    check("demo_defaults", effective_weights(tenant) == ScoringWeights())
    check("none_tenant_defaults", effective_weights(None) == DEFAULT_WEIGHTS)

    result = compute_account_score(**fixture, tenant=tenant)
    components = {name: getattr(result, name).score for name in COMPONENT_NAMES}
    # Historical formula, computed by hand from the component scores.
    legacy = round(
        (
            components["icp_fit"] * 25
            + components["pain_evidence"] * 25
            + components["trigger_strength"] * 20
            + components["contact_confidence"] * 20
            + components["evidence_quality"] * 10
        ) / 5
    )
    check(
        "default_weights_match_legacy_formula",
        result.overall_score == legacy,
        f"overall={result.overall_score}, legacy={legacy}",
    )

    # ----- 2. Tenant weights are honored -------------------------------------
    icp_only = ScoringWeights(
        icp_fit=100, pain_evidence=0, trigger_strength=0, contact_confidence=0, evidence_quality=0
    )
    t_weighted = tenant.model_copy(
        update={"icp": tenant.icp.model_copy(update={"scoring_weights": icp_only})}
    )
    weighted = compute_account_score(**fixture, tenant=t_weighted)
    check(
        "icp_only_weights",
        weighted.overall_score == weighted.icp_fit.score * 20,
        f"overall={weighted.overall_score}, icp_fit={weighted.icp_fit.score}",
    )

    flat = ScoringWeights(
        icp_fit=1, pain_evidence=1, trigger_strength=1, contact_confidence=1, evidence_quality=1
    )
    t_flat = tenant.model_copy(
        update={"icp": tenant.icp.model_copy(update={"scoring_weights": flat})}
    )
    flat_result = compute_account_score(**fixture, tenant=t_flat)
    check("non_100_sum_weights_in_bounds", 0 <= flat_result.overall_score <= 100)

    # ----- 3. Bounds across extremes -----------------------------------------
    weight_sets = [DEFAULT_WEIGHTS, icp_only, flat, ScoringWeights(icp_fit=3, pain_evidence=7)]
    in_bounds = True
    for weights in weight_sets:
        for scores in ({n: 0 for n in COMPONENT_NAMES}, {n: 5 for n in COMPONENT_NAMES},
                       {n: i % 6 for i, n in enumerate(COMPONENT_NAMES)}):
            overall = expected_overall(scores, weights)
            if not 0 <= overall <= 100:
                in_bounds = False
    check("expected_overall_bounds", in_bounds)
    check("all_zero_is_zero", expected_overall({n: 0 for n in COMPONENT_NAMES}, DEFAULT_WEIGHTS) == 0)
    check("all_five_is_hundred", expected_overall({n: 5 for n in COMPONENT_NAMES}, DEFAULT_WEIGHTS) == 100)

    # ----- 4. Determinism -----------------------------------------------------
    again = compute_account_score(**fixture, tenant=tenant)
    check("deterministic", result.model_dump() == again.model_dump())

    # ----- 5. Eval gate -------------------------------------------------------
    state = build_sample_state(tenant, {"company": "Score Gate Co", "industry": "B2B SaaS"}, index=2)
    state["tenant"] = tenant
    report = evaluate_state(state, strict=True)
    gate = next((c for c in report.checks if c.name == "score_components_sum"), None)
    check("gate_present_and_passing", gate is not None and gate.passed, getattr(gate, "detail", "missing"))

    state["enrichment"].account_score.overall_score = min(
        100, state["enrichment"].account_score.overall_score + 17
    )
    strict_report = evaluate_state(state, strict=True)
    tampered = next((c for c in strict_report.checks if c.name == "score_components_sum"), None)
    check(
        "gate_fails_on_tampered_score",
        tampered is not None and not tampered.passed and not strict_report.passed,
        getattr(tampered, "detail", "missing"),
    )
    lenient_report = evaluate_state(state, strict=False)
    check(
        "gate_downgrades_to_warning_non_strict",
        lenient_report.passed and any(c.name == "score_components_sum" for c in lenient_report.warnings),
    )

    # ----- 6. Schema validation ----------------------------------------------
    try:
        ScoringWeights(icp_fit=-1)
        check("negative_weight_rejected", False)
    except Exception:
        check("negative_weight_rejected", True)
    try:
        ScoringWeights(icp_fit=0, pain_evidence=0, trigger_strength=0, contact_confidence=0, evidence_quality=0)
        check("all_zero_weights_rejected", False)
    except Exception:
        check("all_zero_weights_rejected", True)
    try:
        ScoringWeights(unknown_component=5)
        check("extra_weight_key_rejected", False)
    except Exception:
        check("extra_weight_key_rejected", True)
    check("default_weights_sum_100", sum(ICPConfig().scoring_weights.as_map().values()) == 100)

    print()
    if failures:
        print(f"FAILED: {len(failures)} check(s): {', '.join(failures)}")
        return 1
    print("All scoring checks passed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
