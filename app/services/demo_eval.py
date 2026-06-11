"""
Lightweight demo/eval harness for BDR Pipeline workflow metrics.

The default path is intentionally offline: it builds representative sample
states from anonymized/synthetic demo prospects so portfolio demos can be
validated without API availability. Live workflow execution is supported by the
CLI script, but errors are recorded as metrics instead of crashing the run.
"""
from __future__ import annotations

import csv
import json
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from time import perf_counter
from typing import Any, Callable, Iterable

from app.agents.critic import CriticResult, QualityGate, RiskFlag, TouchScore
from app.agents.state import (
    AccountScoringResult,
    AngleDraft,
    ContactLead,
    EnrichmentResult,
    EvidenceCard,
    ICPClassification,
    OutreachSequence,
    ProspectCard,
    ScoreComponent,
    SequenceTouch,
    StrategyDecision,
)
from app.services.report_builder import build_account_report_markdown
from app.tenants import load_tenant
from app.tenants.schema import TenantConfig


METRIC_FIELDS = [
    "account",
    "industry_context",
    "run_completed",
    "runtime_seconds",
    "evidence_card_count",
    "high_confidence_evidence_count",
    "contact_count",
    "account_score",
    "priority_label",
    "quality_gate_verdict",
    "risk_flag_count",
    "unsupported_claim_count",
    "report_generated",
    "notes_errors",
]


@dataclass(frozen=True)
class DemoEvalResult:
    rows: list[dict[str, Any]]
    mode: str
    tenant_id: str
    evaluated_on: date


def load_prospects(tenant: TenantConfig, limit: int | None = None) -> list[dict[str, str]]:
    """Load prospect rows from a tenant prospects.csv, tolerating missing/malformed data."""
    path = tenant.prospects_csv
    if not path.exists():
        return []

    rows: list[dict[str, str]] = []
    try:
        with path.open("r", encoding="utf-8-sig", newline="") as handle:
            reader = csv.DictReader(handle)
            for raw in reader:
                clean = {str(k or "").strip(): str(v or "").strip() for k, v in raw.items()}
                if clean.get("company"):
                    rows.append(clean)
                if limit and len(rows) >= limit:
                    break
    except Exception:
        return []
    return rows


def run_sample_eval(tenant_id: str = "demo", max_accounts: int = 3) -> DemoEvalResult:
    """Run deterministic offline checks against demo prospects."""
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

    rows: list[dict[str, Any]] = []
    for index, prospect in enumerate(prospects, start=1):
        started = perf_counter()
        try:
            state = build_sample_state(tenant, prospect, index)
            runtime = perf_counter() - started
            rows.append(metrics_from_state(state, runtime_seconds=runtime, completed=True))
        except Exception as exc:
            runtime = perf_counter() - started
            rows.append(error_row(prospect, runtime, f"sample-state error: {type(exc).__name__}: {exc}"))

    return DemoEvalResult(rows=rows, mode="sample", tenant_id=tenant_id, evaluated_on=date.today())


def run_live_eval(
    tenant_id: str = "demo",
    max_accounts: int = 3,
    workflow_runner: Callable[[TenantConfig, dict[str, str]], dict] | None = None,
) -> DemoEvalResult:
    """Run live workflow checks, recording API/runtime failures as rows."""
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

    rows: list[dict[str, Any]] = []
    for prospect in prospects:
        started = perf_counter()
        try:
            state = workflow_runner(tenant, prospect)
            runtime = perf_counter() - started
            completed = not bool(state.get("error"))
            row = metrics_from_state(state, runtime_seconds=runtime, completed=completed)
            if state.get("error"):
                row["notes_errors"] = str(state.get("error"))
            rows.append(row)
        except Exception as exc:
            runtime = perf_counter() - started
            rows.append(error_row(prospect, runtime, f"live workflow error: {type(exc).__name__}: {exc}"))

    return DemoEvalResult(rows=rows, mode="live", tenant_id=tenant_id, evaluated_on=date.today())


def _default_live_runner(tenant: TenantConfig, prospect: dict[str, str]) -> dict:
    from app.agents.workflow_engine import build_workflow, run_workflow

    workflow = build_workflow(use_checkpointer=False)
    return run_workflow(
        workflow,
        company=prospect.get("company", ""),
        industry=prospect.get("industry", "") or "unknown",
        tenant=tenant,
        sync_to_notion=False,
        trigger_headline=_prospect_context(prospect),
        prospect_notes=prospect.get("notes", ""),
    )


def build_sample_state(tenant: TenantConfig, prospect: dict[str, str], index: int = 1) -> dict:
    """Build a representative completed state without network or LLM calls."""
    company = prospect.get("company") or f"Demo Account {index}"
    industry = prospect.get("industry") or "B2B SaaS"
    context = _prospect_context(prospect) or "Synthetic demo context for workflow evaluation."

    evidence = [
        EvidenceCard(
            evidence_id="manual-trigger",
            claim=context,
            source_title="Demo prospect notes",
            source_url="",
            source_type="manual_trigger",
            support_type="observed",
            confidence_label="high",
            confidence_score=86,
            excerpt=context,
            safe_to_use=True,
            used_in_outreach=True,
            notes="Synthetic demo row, not campaign data.",
        ),
        EvidenceCard(
            evidence_id="icp-score",
            claim=f"{company} matches the demo ICP industry context.",
            source_title="Internal account score",
            source_url="",
            source_type="icp_score",
            support_type="derived",
            confidence_label="medium",
            confidence_score=68,
            excerpt=f"Industry: {industry}",
            safe_to_use=False,
            used_in_outreach=False,
            notes="Derived internal score for demo validation.",
        ),
    ]

    if index % 2:
        evidence.append(
            EvidenceCard(
                evidence_id="synthetic-job-signal",
                claim=f"{company} appears to be refining revenue operations workflows.",
                source_title="Synthetic job-signal fixture",
                source_url="",
                source_type="job_signal",
                support_type="inferred",
                confidence_label="low",
                confidence_score=42,
                excerpt="Synthetic low-confidence signal used to exercise risk handling.",
                safe_to_use=False,
                used_in_outreach=False,
                notes="Dry-run fixture only.",
            )
        )

    score_value = max(55, min(88, 78 - index * 4 + len(evidence) * 2))
    priority = "review" if score_value < 80 else "high_priority"
    account_score = AccountScoringResult(
        overall_score=score_value,
        priority_label=priority,
        icp_fit=_component("ICP fit", 4, "Industry and motion align with the demo ICP.", ["icp-score"]),
        pain_evidence=_component("Pain evidence", 3, "Pain is plausible but should be verified.", ["manual-trigger"]),
        trigger_strength=_component("Trigger strength", 4, "Demo notes include a clear operating trigger.", ["manual-trigger"]),
        contact_confidence=_component("Contact confidence", 2, "Dry-run mode does not enrich real contacts.", []),
        evidence_quality=_component("Evidence quality", 3, "Evidence is sufficient for demo validation, not sending.", ["manual-trigger"]),
        recommended_action="Review evidence and verify contact manually before any outreach.",
        warnings=["Dry-run metrics are internal workflow checks, not campaign performance."],
    )

    enrichment = EnrichmentResult(
        company=company,
        industry=industry,
        domain=prospect.get("domain", ""),
        contacts=[] if index % 3 else [
            ContactLead(
                name="Synthetic Revenue Leader",
                email="",
                position=tenant.persona.title,
                seniority="executive",
                confidence=54,
            )
        ],
        evidence_cards=evidence,
        account_score=account_score,
        icp=ICPClassification(
            tier=1 if score_value >= 80 else 2,
            tier_label=tenant.icp.tier1_label if score_value >= 80 else tenant.icp.tier2_label,
            rationale="Synthetic demo account aligns with the configured ICP.",
            score=score_value,
            score_breakdown={"dry_run": score_value},
        ),
        research_summary=f"Dry-run summary for {company}: {context}",
        intent_score=score_value,
        intent_top_trigger=context,
    )

    strategy = StrategyDecision(
        recommended_angle="angle1",
        angle_name=tenant.angle_by_key("angle1").name,
        rationale=(
            f"{company} has a demo trigger tied to forecast confidence and revenue operations. "
            "This is suitable for internal workflow validation, pending manual verification."
        ),
        cpo_hypothesis=tenant.persona.title,
        pain_signal=context,
    )

    sequence = OutreachSequence(
        recommended_angle=strategy.angle_name,
        entry_persona=tenant.persona.title,
        touches=[
            SequenceTouch(
                touch_number=1,
                day=0,
                channel="linkedin_connect",
                body=f"Noticed the {industry} revenue ops context at {company}. Worth connecting?",
                persona=tenant.persona.title,
                word_count=11,
            ),
            SequenceTouch(
                touch_number=2,
                day=1,
                channel="email",
                subject=f"{company} forecast review",
                body=(
                    f"Saw the demo context around {context}. "
                    "If useful, I can share how teams review pipeline risk before board updates."
                ),
                cta="Open to a manual review?",
                persona=tenant.persona.title,
                word_count=24,
            ),
        ],
    )
    card = ProspectCard(
        before_after=(
            f"Before: {company} is evaluated from limited public-safe demo context.\n\n"
            "After: The workflow produces evidence, score, sequence, and quality gate artifacts for human review."
        ),
        angles=[
            AngleDraft(
                angle_key="angle1",
                name=tenant.angle_by_key("angle1").name,
                tab_label=tenant.angle_by_key("angle1").tab_label,
                dm="Synthetic DM draft for internal validation.",
                email_subject=f"{company} forecast review",
                email_body="Synthetic email draft for internal validation. Manual approval required.",
            )
        ],
        sequence=sequence,
    )

    risk_flags = []
    unsupported_count = 0
    if index % 2:
        risk_flags.append(
            RiskFlag(
                risk_type="thin_evidence",
                severity="medium",
                touch_number=2,
                text_excerpt=context[:120],
                rationale="Dry-run evidence is intentionally synthetic and should not be treated as verified.",
                recommended_fix="Verify the trigger against source-backed evidence before sending.",
                evidence_ids=["manual-trigger"],
            )
        )
        unsupported_count = 1

    gate = QualityGate(
        verdict="needs_edit" if risk_flags else "approved",
        safe_to_send=not bool(risk_flags),
        confidence="medium" if not risk_flags else "low",
        summary="Dry-run quality gate completed for internal workflow validation.",
        required_edits=["Verify all synthetic evidence before sending."] if risk_flags else [],
        risk_flags=risk_flags,
        unsupported_claim_count=unsupported_count,
        evidence_coverage_note="Dry-run sample state; not live campaign evidence.",
    )

    critic_result = CriticResult(
        touch_scores=[
            TouchScore(touch_number=1, pain_specificity=3, proof_relevance=3, cta_clarity=3, human_voice=4),
            TouchScore(touch_number=2, pain_specificity=3, proof_relevance=3, cta_clarity=4, human_voice=4),
        ],
        overall_quality=3.4 if risk_flags else 3.8,
        rewrites_applied=0,
        critique_summary="Dry-run critique fixture completed.",
        quality_gate=gate,
    )

    return {
        "tenant": tenant,
        "company": company,
        "industry": industry,
        "sync_to_notion": False,
        "trigger_headline": context,
        "prospect_notes": prospect.get("notes", ""),
        "enrichment": enrichment,
        "strategy": strategy,
        "card": card,
        "critic_result": critic_result,
        "crm_result": None,
        "agent_trace": ["Demo eval: sample state generated"],
        "error": None,
    }


def metrics_from_state(state: dict, runtime_seconds: float, completed: bool) -> dict[str, Any]:
    """Extract the honest internal metrics requested for demo evaluation."""
    enrichment = state.get("enrichment")
    account_score = _get(enrichment, "account_score")
    critic_result = state.get("critic_result")
    gate = _get(critic_result, "quality_gate")
    evidence = _get(enrichment, "evidence_cards") or []
    contacts = _get(enrichment, "contacts") or []
    report_generated = False
    notes: list[str] = []

    try:
        tenant = state.get("tenant")
        if tenant:
            report = build_account_report_markdown(state, tenant)
            report_generated = bool(report.strip())
    except Exception as exc:
        notes.append(f"report generation error: {type(exc).__name__}: {exc}")

    if state.get("error"):
        notes.append(str(state.get("error")))

    return _ordered_row(
        {
            "account": state.get("company") or _get(enrichment, "company") or "",
            "industry_context": _industry_context(state),
            "run_completed": "yes" if completed else "no",
            "runtime_seconds": f"{runtime_seconds:.2f}",
            "evidence_card_count": len(evidence),
            "high_confidence_evidence_count": sum(
                1 for card in evidence if _get(card, "confidence_label") == "high"
            ),
            "contact_count": len(contacts),
            "account_score": _get(account_score, "overall_score", ""),
            "priority_label": _get(account_score, "priority_label", ""),
            "quality_gate_verdict": _get(gate, "verdict", ""),
            "risk_flag_count": len(_get(gate, "risk_flags") or []),
            "unsupported_claim_count": _get(gate, "unsupported_claim_count", ""),
            "report_generated": "yes" if report_generated else "no",
            "notes_errors": " | ".join(notes),
        }
    )


def error_row(prospect: dict[str, str], runtime_seconds: float, error: str) -> dict[str, Any]:
    return _ordered_row(
        {
            "account": prospect.get("company", ""),
            "industry_context": _prospect_industry_context(prospect),
            "run_completed": "no",
            "runtime_seconds": f"{runtime_seconds:.2f}",
            "evidence_card_count": 0,
            "high_confidence_evidence_count": 0,
            "contact_count": 0,
            "account_score": "",
            "priority_label": "",
            "quality_gate_verdict": "",
            "risk_flag_count": 0,
            "unsupported_claim_count": "",
            "report_generated": "no",
            "notes_errors": error,
        }
    )


def write_csv(rows: Iterable[dict[str, Any]], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=METRIC_FIELDS)
        writer.writeheader()
        for row in rows:
            writer.writerow(_ordered_row(row))


def write_json(result: DemoEvalResult, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "mode": result.mode,
        "tenant_id": result.tenant_id,
        "evaluated_on": result.evaluated_on.isoformat(),
        "disclaimer": (
            "Internal demo workflow metrics only. No reply rates, meeting rates, "
            "revenue, or campaign lift are claimed."
        ),
        "rows": result.rows,
    }
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def build_evals_markdown(result: DemoEvalResult, csv_path: Path | None = None) -> str:
    rows = result.rows
    account_names = ", ".join(str(row.get("account", "")) for row in rows if row.get("account")) or "None"
    completed = sum(1 for row in rows if row.get("run_completed") == "yes")
    table = _markdown_table(rows)
    csv_note = f"\nDetailed CSV: `{csv_path.as_posix()}`\n" if csv_path else ""

    return "\n".join(
        [
            "# Demo Eval Results",
            "",
            "These are internal demo workflow metrics for the BDR Pipeline portfolio artifact.",
            "They do not claim reply rates, meeting rates, revenue, deliverability, or campaign lift.",
            "",
            "## What Was Evaluated",
            "",
            f"- Date: {result.evaluated_on.isoformat()}",
            f"- Tenant: `{result.tenant_id}`",
            f"- Mode: `{result.mode}`",
            f"- Demo accounts used: {account_names}",
            f"- Completed runs: {completed}/{len(rows)}",
            csv_note.rstrip(),
            "",
            "## Metrics",
            "",
            table,
            "",
            "## Limitations",
            "",
            "- Demo prospects are anonymized/synthetic examples.",
            "- Sample mode uses deterministic fixture states and does not verify live API reliability.",
            "- Live mode depends on available API keys, network access, and third-party enrichment services.",
            "- Evidence confidence is a workflow quality signal, not proof that outreach should be sent.",
            "- Human review is required before any outreach leaves the system.",
            "",
            "## What These Results Prove",
            "",
            "- The workflow can produce measurable internal artifacts: evidence counts, account score, quality gate verdict, risks, and report generation status.",
            "- The eval harness records runtime and failures without treating missing APIs as a successful run.",
            "",
            "## What These Results Do Not Prove",
            "",
            "- They do not prove reply-rate lift.",
            "- They do not prove meetings booked.",
            "- They do not prove revenue impact.",
            "- They do not prove production reliability across real prospect data.",
            "",
        ]
    )


def write_evals_markdown(result: DemoEvalResult, path: Path, csv_path: Path | None = None) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(build_evals_markdown(result, csv_path=csv_path), encoding="utf-8")


def _component(label: str, score: int, rationale: str, evidence_ids: list[str]) -> ScoreComponent:
    return ScoreComponent(label=label, score=score, rationale=rationale, evidence_ids=evidence_ids)


def _prospect_context(row: dict[str, str]) -> str:
    for key in ("trigger_headline", "trigger", "notes", "context"):
        value = (row.get(key) or "").strip()
        if value:
            return value
    return ""


def _prospect_industry_context(row: dict[str, str]) -> str:
    parts = [row.get("industry", ""), _prospect_context(row)]
    return " | ".join(part for part in parts if part)


def _industry_context(state: dict) -> str:
    parts = [
        str(state.get("industry") or _get(state.get("enrichment"), "industry") or ""),
        str(state.get("trigger_headline") or state.get("prospect_notes") or ""),
    ]
    return " | ".join(part for part in parts if part)


def _ordered_row(row: dict[str, Any]) -> dict[str, Any]:
    return {field: row.get(field, "") for field in METRIC_FIELDS}


def _markdown_table(rows: list[dict[str, Any]]) -> str:
    if not rows:
        return "_No eval rows generated._"
    header = "| " + " | ".join(METRIC_FIELDS) + " |"
    separator = "| " + " | ".join("---" for _ in METRIC_FIELDS) + " |"
    body = [
        "| " + " | ".join(_escape_md(row.get(field, "")) for field in METRIC_FIELDS) + " |"
        for row in rows
    ]
    return "\n".join([header, separator, *body])


def _escape_md(value: Any) -> str:
    text = str(value).replace("\n", " ").strip()
    return text.replace("|", "\\|")


def _get(obj: Any, path: str, default: Any = None) -> Any:
    current = obj
    for part in path.split("."):
        if current is None:
            return default
        if isinstance(current, dict):
            current = current.get(part, default)
        else:
            current = getattr(current, part, default)
    return current
