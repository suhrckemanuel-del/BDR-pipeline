"""
batch_runner.py — Run the pipeline over a whole prospect list.

The batch loop is the "upload a list, get scored sequences back" path:
for each prospect row it executes the workflow (live APIs, or the offline
sample-state builder for demos), runs the deterministic eval gates, and
persists the run + tracker row to SQLite. One failing prospect never aborts
the batch — errors are recorded as failed runs.

Modes:
  live    — full LangGraph workflow (Anthropic + Exa + Hunter).
  sample  — deterministic offline fixture states (no network), so batch mode
            can be demonstrated and tested without API keys.
"""
from __future__ import annotations

from dataclasses import dataclass
from time import perf_counter
from typing import Callable, Optional

from app.services import store
from app.services.demo_eval import build_sample_state, load_prospects
from app.services.pipeline_evals import EvalReport, evaluate_state
from app.tenants.schema import TenantConfig

# Labeled assumption for the ROI strip — manual research + drafting benchmark.
# This is an input to an estimate, not a measured claim.
MANUAL_MINUTES_PER_PROSPECT = 45


@dataclass
class BatchResult:
    company: str
    industry: str
    run_id: int
    runtime_seconds: float
    error: str
    eval_report: EvalReport
    account_score: Optional[int]
    priority_label: str
    critic_score: Optional[float]
    recommended_angle: str

    @property
    def completed(self) -> bool:
        return not self.error


def _get(obj, attr, default=None):
    if obj is None:
        return default
    if isinstance(obj, dict):
        return obj.get(attr, default)
    return getattr(obj, attr, default)


def _run_live(tenant: TenantConfig, prospect: dict[str, str], sync_to_notion: bool) -> dict:
    from app.agents.workflow_engine import build_workflow, run_workflow

    workflow = build_workflow(use_checkpointer=False)
    context = ""
    for key in ("trigger_headline", "trigger", "notes", "context"):
        if (prospect.get(key) or "").strip():
            context = prospect[key].strip()
            break
    return run_workflow(
        workflow,
        company=prospect.get("company", ""),
        industry=prospect.get("industry", "") or "unknown",
        tenant=tenant,
        sync_to_notion=sync_to_notion,
        trigger_headline=context,
        prospect_notes=prospect.get("notes", ""),
    )


def run_batch(
    tenant: TenantConfig,
    prospects: Optional[list[dict[str, str]]] = None,
    *,
    mode: str = "live",
    limit: Optional[int] = None,
    sync_to_notion: bool = False,
    source: str = "batch",
    on_progress: Optional[Callable[[int, int, str], None]] = None,
) -> list[BatchResult]:
    """
    Run the pipeline for each prospect row and persist everything.

    prospects defaults to the tenant's prospects.csv. on_progress(index, total,
    company) is called before each run so UIs can render live progress.
    """
    if mode not in ("live", "sample"):
        raise ValueError(f"mode must be 'live' or 'sample', got {mode!r}")

    rows = prospects if prospects is not None else load_prospects(tenant)
    if limit:
        rows = rows[:limit]

    results: list[BatchResult] = []
    total = len(rows)
    for index, prospect in enumerate(rows, start=1):
        company = prospect.get("company", "") or f"Account {index}"
        if on_progress:
            on_progress(index, total, company)

        started = perf_counter()
        try:
            if mode == "sample":
                state = build_sample_state(tenant, prospect, index)
            else:
                state = _run_live(tenant, prospect, sync_to_notion)
        except Exception as exc:
            state = {
                "tenant": tenant,
                "company": company,
                "industry": prospect.get("industry", ""),
                "error": f"{type(exc).__name__}: {exc}",
            }
        runtime = perf_counter() - started

        report = evaluate_state(state, strict=(mode == "live"))
        run_id = store.record_run(
            state,
            source=source,
            mode=mode,
            runtime_seconds=runtime,
            eval_passed=report.passed,
            eval_failures=report.failure_names(),
        )
        if not state.get("error"):
            store.upsert_prospect_from_state(state, run_id, eval_passed=report.passed)

        enrichment = state.get("enrichment")
        account_score = _get(_get(enrichment, "account_score"), "overall_score")
        results.append(
            BatchResult(
                company=company,
                industry=state.get("industry", "") or "",
                run_id=run_id,
                runtime_seconds=round(runtime, 2),
                error=str(state.get("error") or ""),
                eval_report=report,
                account_score=account_score,
                priority_label=_get(_get(enrichment, "account_score"), "priority_label", "") or "",
                critic_score=_get(state.get("critic_result"), "overall_quality"),
                recommended_angle=_get(state.get("strategy"), "recommended_angle", "") or "",
            )
        )
    return results


def batch_summary(results: list[BatchResult]) -> dict:
    """Aggregate metrics for the batch results table / ROI strip."""
    completed = [r for r in results if r.completed]
    eval_passed = [r for r in results if r.eval_report.passed and r.completed]
    critic_scores = [r.critic_score for r in completed if r.critic_score is not None]
    total_runtime = sum(r.runtime_seconds for r in results)
    return {
        "total": len(results),
        "completed": len(completed),
        "eval_passed": len(eval_passed),
        "avg_critic": round(sum(critic_scores) / len(critic_scores), 2) if critic_scores else None,
        "total_runtime_seconds": round(total_runtime, 1),
        # Estimate against a stated manual benchmark — an assumption, not a measurement.
        "est_manual_minutes": len(completed) * MANUAL_MINUTES_PER_PROSPECT,
        "pipeline_minutes": round(total_runtime / 60, 1),
    }
