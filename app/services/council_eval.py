"""
council_eval.py — Council vs. single-rater comparison harness.

Runs the SAME completed prospect state through two critic arms and compares:

  - gate verdict distribution
  - copy quality scores
  - council disagreement rates

Design (important for a fair comparison):

  Arm A ("single"): critic with council_size=1 on the completed state.
  Arm B ("council"): critic with council_size=N (default 3) on the same state.

Both arms score the copy produced by the original run; neither arm's rewrite
path is allowed to mutate the stored card (rewrites are applied to a deepcopy
that is discarded). This isolates rater behavior from generation variance —
the alternative (two full pipeline runs) would confound the comparison with
different Exa/Hunter/LLM outputs.

The harness never auto-sends anything and stores results as internal workflow
metrics only. It does not measure replies, meetings, revenue, or campaign lift.
"""
from __future__ import annotations

import csv
import json
import statistics
from copy import deepcopy
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from time import perf_counter
from typing import Any, Callable

from app.agents.critic import CriticResult, run_critic
from app.services.demo_eval import load_prospects
from app.tenants.schema import TenantConfig


@dataclass(frozen=True)
class CouncilEvalResult:
    rows: list[dict[str, Any]]
    mode: str
    tenant_id: str
    council_size: int
    evaluated_on: date = field(default_factory=date.today)


# ---------------------------------------------------------------------------
# Config patching
# ---------------------------------------------------------------------------

def patch_tenant_critic_config(
    tenant: TenantConfig, council_size: int
) -> TenantConfig:
    """
    Return a shallow-copied tenant with critic.council_size set to the given
    value. Pydantic models are (mostly) immutable by default, so use
    model_copy with a re-validated nested CriticConfig. No files are written.
    """
    patched = tenant.model_copy(deep=False)
    patched_critic = tenant.critic.model_copy(update={"council_size": council_size})
    # TenantConfig attributes are assignable (pydantic v2 default allows
    # assignment with validation only when configured; use object.__setattr__
    # via model_copy on the parent to stay safe):
    return patched.model_copy(update={"critic": patched_critic})


# ---------------------------------------------------------------------------
# Metrics extraction
# ---------------------------------------------------------------------------

def extract_arm_metrics(critic_result: CriticResult) -> dict[str, Any]:
    """Extract comparison metrics from one arm's CriticResult. Pure function."""
    gate = critic_result.quality_gate
    touch_scores = critic_result.touch_scores or []
    dim_values: dict[str, list[int]] = {
        "pain": [],
        "proof": [],
        "cta": [],
        "voice": [],
    }
    for ts in touch_scores:
        dim_values["pain"].append(ts.pain_specificity)
        dim_values["proof"].append(ts.proof_relevance)
        dim_values["cta"].append(ts.cta_clarity)
        dim_values["voice"].append(ts.human_voice)

    scorer_overall_scores = list(getattr(critic_result, "scorer_overall_scores", []) or [])
    scorer_spread = (
        round(max(scorer_overall_scores) - min(scorer_overall_scores), 2)
        if len(scorer_overall_scores) > 1
        else 0.0
    )

    return {
        "gate_verdict": (getattr(gate, "verdict", "") or "") if gate else "",
        "gate_confidence": (getattr(gate, "confidence", "") or "") if gate else "",
        "gate_risk_flag_count": len(getattr(gate, "risk_flags", []) or []) if gate else 0,
        "gate_unsupported_claim_count": (
            getattr(gate, "unsupported_claim_count", 0) if gate else 0
        ),
        "overall_quality": round(float(critic_result.overall_quality or 0.0), 2),
        "touch_count": len(touch_scores),
        "touches_needing_rewrite": sum(1 for ts in touch_scores if ts.needs_rewrite),
        "pain_mean": round(statistics.fmean(dim_values["pain"]), 2) if dim_values["pain"] else 0.0,
        "proof_mean": round(statistics.fmean(dim_values["proof"]), 2) if dim_values["proof"] else 0.0,
        "cta_mean": round(statistics.fmean(dim_values["cta"]), 2) if dim_values["cta"] else 0.0,
        "voice_mean": round(statistics.fmean(dim_values["voice"]), 2) if dim_values["voice"] else 0.0,
        "council_size": int(getattr(critic_result, "council_size", 1) or 1),
        "scorer_overall_scores": scorer_overall_scores,
        "scorer_spread": scorer_spread,
        "disagreement_count": len(getattr(critic_result, "disagreements", []) or []),
        "disagreements": list(getattr(critic_result, "disagreements", []) or []),
        "agreement_level": getattr(critic_result, "agreement_level", "single_rater") or "single_rater",
    }


def metrics_from_state_after_critic(state: dict) -> dict[str, Any]:
    """Pull the CriticResult out of a post-run_critic state (pure helper)."""
    critic_result = state.get("critic_result")
    if critic_result is None:
        raise ValueError("state has no critic_result; critic arm did not run")
    return extract_arm_metrics(critic_result)


# ---------------------------------------------------------------------------
# Comparison logic (pure)
# ---------------------------------------------------------------------------

VERDICT_SEVERITY = {
    "approved": 0,
    "needs_edit": 1,
    "needs_more_research": 2,
    "do_not_send_yet": 3,
}

AGREEMENT_RANK = {"high": 0, "medium": 1, "low": 2}


def compare_arms(single: dict[str, Any], council: dict[str, Any]) -> dict[str, Any]:
    """Compare the two arms' metric dicts. Pure function."""
    s_verdict = single.get("gate_verdict", "")
    c_verdict = council.get("gate_verdict", "")
    s_sev = VERDICT_SEVERITY.get(s_verdict)
    c_sev = VERDICT_SEVERITY.get(c_verdict)

    if s_sev is not None and c_sev is not None and s_sev != c_sev:
        verdict_shift = "council_stricter" if c_sev > s_sev else "council_looser"
    else:
        verdict_shift = "same"

    s_q = float(single.get("overall_quality", 0.0) or 0.0)
    c_q = float(council.get("overall_quality", 0.0) or 0.0)
    quality_delta = round(c_q - s_q, 2)

    s_risk = int(single.get("gate_risk_flag_count", 0) or 0)
    c_risk = int(council.get("gate_risk_flag_count", 0) or 0)

    disagreements = list(council.get("disagreements", []) or [])
    agreement_level = council.get("agreement_level", "single_rater")
    touch_count = max(int(council.get("touch_count", 0) or 0), 1)
    dim_count = 4
    rate_denominator = touch_count * dim_count
    disagreement_rate = round(len(disagreements) / rate_denominator, 3)

    return {
        "verdict_single": s_verdict,
        "verdict_council": c_verdict,
        "verdict_shift": verdict_shift,
        "quality_single": s_q,
        "quality_council": c_q,
        "quality_delta": quality_delta,
        "risk_flags_single": s_risk,
        "risk_flags_council": c_risk,
        "touches_flagged_single": int(single.get("touches_needing_rewrite", 0) or 0),
        "touches_flagged_council": int(council.get("touches_needing_rewrite", 0) or 0),
        "disagreement_count": len(disagreements),
        "disagreement_rate": disagreement_rate,
        "agreement_level": agreement_level,
        "scorer_spread": float(council.get("scorer_spread", 0.0) or 0.0),
    }


# ---------------------------------------------------------------------------
# Console + Markdown summarization
# ---------------------------------------------------------------------------

def summarize_results(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """Aggregate comparison rows into headline numbers. Pure function."""
    if not rows:
        return {
            "accounts": 0,
            "completed_pairs": 0,
            "verdict_shifts": {},
            "council_stricter_count": 0,
            "council_looser_count": 0,
            "same_verdict_count": 0,
            "avg_quality_single": 0.0,
            "avg_quality_council": 0.0,
            "avg_quality_delta": 0.0,
            "total_disagreements": 0,
            "avg_disagreement_rate": 0.0,
            "agreement_level_counts": {},
            "verdicts_single": {},
            "verdicts_council": {},
            "high_risk_agreement_count": 0,
        }

    def _mean(values: list[float]) -> float:
        return round(statistics.fmean(values), 2) if values else 0.0

    completed = [r for r in rows if r.get("run_completed") == "yes"]

    verdict_shifts: dict[str, int] = {}
    verdicts_single: dict[str, int] = {}
    verdicts_council: dict[str, int] = {}
    agreement_counts: dict[str, int] = {}
    quality_single: list[float] = []
    quality_council: list[float] = []
    deltas: list[float] = []
    total_disagreements = 0
    disagreement_rates: list[float] = []
    high_risk_agreement = 0

    for row in completed:
        shift = row.get("verdict_shift", "same") or "same"
        verdict_shifts[shift] = verdict_shifts.get(shift, 0) + 1
        vs = row.get("verdict_single", "") or "(none)"
        vc = row.get("verdict_council", "") or "(none)"
        verdicts_single[vs] = verdicts_single.get(vs, 0) + 1
        verdicts_council[vc] = verdicts_council.get(vc, 0) + 1
        agreement_counts[row.get("agreement_level", "single_rater") or "single_rater"] = (
            agreement_counts.get(row.get("agreement_level", "single_rater") or "single_rater", 0) + 1
        )
        quality_single.append(float(row.get("quality_single", 0.0) or 0.0))
        quality_council.append(float(row.get("quality_council", 0.0) or 0.0))
        deltas.append(float(row.get("quality_delta", 0.0) or 0.0))
        total_disagreements += int(row.get("disagreement_count", 0) or 0)
        disagreement_rates.append(float(row.get("disagreement_rate", 0.0) or 0.0))
        if (
            row.get("verdict_single") == "do_not_send_yet"
            and row.get("verdict_council") == "do_not_send_yet"
        ):
            high_risk_agreement += 1

    return {
        "accounts": len(rows),
        "completed_pairs": len(completed),
        "verdict_shifts": verdict_shifts,
        "council_stricter_count": verdict_shifts.get("council_stricter", 0),
        "council_looser_count": verdict_shifts.get("council_looser", 0),
        "same_verdict_count": verdict_shifts.get("same", 0),
        "avg_quality_single": _mean(quality_single),
        "avg_quality_council": _mean(quality_council),
        "avg_quality_delta": _mean(deltas),
        "total_disagreements": total_disagreements,
        "avg_disagreement_rate": _mean(disagreement_rates),
        "agreement_level_counts": agreement_counts,
        "verdicts_single": verdicts_single,
        "verdicts_council": verdicts_council,
        "high_risk_agreement_count": high_risk_agreement,
    }


def build_markdown_report(
    result: CouncilEvalResult, summary: dict[str, Any], csv_path: Path | None = None
) -> str:
    """Deterministic Markdown summary of the council comparison."""
    rows = result.rows
    account_names = ", ".join(
        str(row.get("account", "")) for row in rows if row.get("account")
    ) or "None"

    def fmt_dist(d: dict[str, int]) -> str:
        if not d:
            return "(none)"
        return ", ".join(f"`{k}`: {v}" for k, v in sorted(d.items()))

    csv_note = f"\nDetailed CSV: `{csv_path.as_posix()}`\n" if csv_path else ""

    lines = [
        "# Council vs. Single-Rater Critic Comparison",
        "",
        "Internal workflow metrics only. No reply rates, meeting rates, revenue,",
        "or campaign lift are claimed. Both arms score the same completed",
        "prospect state; the single-rater arm uses `council_size: 1`, the",
        f"council arm uses `council_size: {result.council_size}`.",
        "",
        "## Run Metadata",
        "",
        f"- Date: {result.evaluated_on.isoformat()}",
        f"- Tenant: `{result.tenant_id}`",
        f"- Mode: `{result.mode}`",
        f"- Council size (arm B): {result.council_size}",
        f"- Accounts compared: {len(rows)}",
        f"- Accounts: {account_names}",
        csv_note.rstrip(),
        "",
        "## Headline Comparison",
        "",
        f"- Completed pairs: {summary.get('completed_pairs', 0)}/{summary.get('accounts', 0)}",
        f"- Verdict shifts: {fmt_dist(summary.get('verdict_shifts', {}))}",
        f"- Council stricter: {summary.get('council_stricter_count', 0)} · "
        f"Council looser: {summary.get('council_looser_count', 0)} · "
        f"Same verdict: {summary.get('same_verdict_count', 0)}",
        f"- Avg copy quality — single: {summary.get('avg_quality_single', 0.0):.2f}/5 · "
        f"council: {summary.get('avg_quality_council', 0.0):.2f}/5 · "
        f"delta: {summary.get('avg_quality_delta', 0.0):+.2f}",
        f"- Total disagreement flags (council arm): {summary.get('total_disagreements', 0)}",
        f"- Avg disagreement rate per account: {summary.get('avg_disagreement_rate', 0.0):.1%}",
        f"- Agreement levels: {fmt_dist(summary.get('agreement_level_counts', {}))}",
        f"- do_not_send_yet agreement (both arms): {summary.get('high_risk_agreement_count', 0)}",
        "",
        "## Verdict Distributions",
        "",
        f"- Single-rater arm: {fmt_dist(summary.get('verdicts_single', {}))}",
        f"- Council arm: {fmt_dist(summary.get('verdicts_council', {}))}",
        "",
        "## Per-Account Detail",
        "",
    ]

    if rows:
        header_fields = [
            "account",
            "run_completed",
            "verdict_single",
            "verdict_council",
            "verdict_shift",
            "quality_single",
            "quality_council",
            "quality_delta",
            "risk_flags_single",
            "risk_flags_council",
            "disagreement_count",
            "disagreement_rate",
            "agreement_level",
        ]
        header = "| " + " | ".join(header_fields) + " |"
        separator = "| " + " | ".join("---" for _ in header_fields) + " |"
        body = []
        for row in rows:
            cells = []
            for field in header_fields:
                value = row.get(field, "")
                if isinstance(value, float):
                    value = f"{value:.2f}"
                text = str(value).replace("\n", " ").strip()
                cells.append(text.replace("|", "\\|"))
            body.append("| " + " | ".join(cells) + " |")
        lines.extend([header, separator, *body])
    else:
        lines.append("_No comparison rows generated._")

    lines.extend(
        [
            "",
            "## Limitations",
            "",
            "- Demo prospects are anonymized/synthetic examples.",
            "- Both arms share the same generation; differences reflect rater behavior only.",
            "- Council arm rewrites are discarded (comparison mode); the live pipeline applies them.",
            "- Live mode depends on API keys, network access, and vendor availability.",
            "- Human review is required before any outreach leaves the system.",
            "",
        ]
    )
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Writers
# ---------------------------------------------------------------------------

CSV_FIELDS = [
    "account",
    "industry_context",
    "run_completed",
    "notes_errors",
    "verdict_single",
    "verdict_council",
    "verdict_shift",
    "quality_single",
    "quality_council",
    "quality_delta",
    "risk_flags_single",
    "risk_flags_council",
    "touches_flagged_single",
    "touches_flagged_council",
    "disagreement_count",
    "disagreement_rate",
    "agreement_level",
    "scorer_spread",
    "disagreements",
    "degradations",
]


def write_csv(rows: list[dict[str, Any]], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=CSV_FIELDS, extrasaction="ignore")
        writer.writeheader()
        for row in rows:
            writer.writerow({field: _csv_cell(row.get(field, "")) for field in CSV_FIELDS})


def _csv_cell(value: Any) -> Any:
    if isinstance(value, list):
        return " | ".join(str(item) for item in value)
    return value


def write_json(result: CouncilEvalResult, summary: dict[str, Any], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "mode": result.mode,
        "tenant_id": result.tenant_id,
        "council_size": result.council_size,
        "evaluated_on": result.evaluated_on.isoformat(),
        "disclaimer": (
            "Internal demo workflow metrics only. No reply rates, meeting rates, "
            "revenue, or campaign lift are claimed."
        ),
        "summary": summary,
        "rows": result.rows,
    }
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def write_markdown(result: CouncilEvalResult, summary: dict[str, Any], path: Path, csv_path: Path | None = None) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(build_markdown_report(result, summary, csv_path=csv_path), encoding="utf-8")


# ---------------------------------------------------------------------------
# Arm runner
# ---------------------------------------------------------------------------

def run_critic_arm(
    state: dict,
    tenant: TenantConfig,
    council_size: int,
) -> dict:
    """
    Run the critic node on a completed state under a patched council size.

    The state's card is deep-copied first so arm rewrites can never leak into
    the other arm or the stored state.
    """
    arm_state = deepcopy(state)
    arm_state["agent_trace"] = []
    arm_state["error"] = None

    patched = patch_tenant_critic_config(tenant, council_size)
    arm_state["tenant"] = patched

    result = run_critic(arm_state)
    result["critic_result"] = result.get("critic_result") or CriticResult()
    return result


def build_comparison_row(
    account: str,
    industry_context: str,
    single_state: dict,
    council_state: dict,
    runtime_seconds: float,
) -> dict[str, Any]:
    """Merge both arms' metrics into one comparison row (pure helper)."""
    single_metrics = metrics_from_state_after_critic(single_state)
    council_metrics = metrics_from_state_after_critic(council_state)
    comparison = compare_arms(single_metrics, council_metrics)

    row: dict[str, Any] = {
        "account": account,
        "industry_context": industry_context,
        "run_completed": "yes",
        "notes_errors": "",
    }
    row.update(comparison)
    row["scorer_spread"] = council_metrics.get("scorer_spread", 0.0)
    row["disagreements"] = council_metrics.get("disagreements", [])
    row["degradations"] = list(single_state.get("degradations") or [])
    row["runtime_seconds"] = f"{runtime_seconds:.2f}"
    return row


def error_row(account: str, industry_context: str, error: str) -> dict[str, Any]:
    return {
        "account": account,
        "industry_context": industry_context,
        "run_completed": "no",
        "notes_errors": error,
    }


# ---------------------------------------------------------------------------
# Orchestration
# ---------------------------------------------------------------------------

def _default_live_runner(tenant: TenantConfig, prospect: dict[str, str]) -> dict:
    """One full pipeline run producing the shared state both arms score."""
    from app.agents.workflow_engine import build_workflow, run_workflow

    workflow = build_workflow(use_checkpointer=False)
    return run_workflow(
        workflow,
        company=prospect.get("company", ""),
        industry=prospect.get("industry", "") or "unknown",
        tenant=tenant,
        sync_to_notion=False,
        trigger_headline=_prospect_context(prospect),
    )


def _prospect_context(row: dict[str, str]) -> str:
    for key in ("trigger_headline", "trigger", "notes", "context"):
        value = (row.get(key) or "").strip()
        if value:
            return value
    return ""


def run_council_eval(
    tenant_id: str = "demo",
    max_accounts: int = 2,
    council_size: int = 3,
    workflow_runner: Callable[[TenantConfig, dict[str, str]], dict] | None = None,
    critic_runner: Callable[[dict, TenantConfig, int], dict] | None = None,
) -> CouncilEvalResult:
    """
    Compare single-rater vs. council critic on the same completed runs.

    workflow_runner and critic_runner are injectable for testing.
    """
    from app.tenants import load_tenant

    tenant = load_tenant(tenant_id)
    prospects = load_prospects(tenant, limit=max_accounts)
    if not prospects:
        prospects = [
            {
                "company": "Sample Demo Account",
                "industry": "B2B SaaS",
                "notes": "Fallback synthetic account because prospects.csv was missing or unreadable.",
            }
        ]

    if workflow_runner is None:
        workflow_runner = _default_live_runner
    if critic_runner is None:
        critic_runner = run_critic_arm

    rows: list[dict[str, Any]] = []
    for prospect in prospects:
        account = prospect.get("company", "") or "(unnamed)"
        industry_context = " | ".join(
            part
            for part in [
                prospect.get("industry", ""),
                _prospect_context(prospect),
            ]
            if part
        )
        started = perf_counter()
        try:
            # Shared generation: one full pipeline run both arms score.
            shared_state = workflow_runner(tenant, prospect)
            if shared_state.get("error"):
                rows.append(error_row(account, industry_context, str(shared_state.get("error"))))
                continue
            if not shared_state.get("card") or not shared_state.get("enrichment"):
                rows.append(
                    error_row(
                        account,
                        industry_context,
                        "workflow produced no card/enrichment; critic arms skipped",
                    )
                )
                continue

            single_state = critic_runner(shared_state, tenant, 1)
            council_state = critic_runner(shared_state, tenant, council_size)
            runtime = perf_counter() - started
            rows.append(
                build_comparison_row(account, industry_context, single_state, council_state, runtime)
            )
        except Exception as exc:  # noqa: BLE001
            rows.append(error_row(account, industry_context, f"{type(exc).__name__}: {exc}"))

    return CouncilEvalResult(
        rows=rows,
        mode="live",
        tenant_id=tenant_id,
        council_size=council_size,
        evaluated_on=date.today(),
    )
