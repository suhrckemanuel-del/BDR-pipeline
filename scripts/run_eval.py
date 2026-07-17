"""
Run the deterministic pipeline eval gates as a regression check.

This is the CI-able eval loop: it executes the pipeline for a set of prospects
(offline sample states by default, live with --mode live), scores every result
against the quality gates in app/services/pipeline_evals.py, writes a Markdown
report, and exits non-zero if any blocking gate fails — so a regression in
sequence assembly, copy quality, or critic thresholds fails loudly.

Examples:
    python scripts/run_eval.py                          # offline, demo tenant
    python scripts/run_eval.py --mode live --max 2      # live workflow eval
    python scripts/run_eval.py --strict                 # completeness gates block even in sample mode
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path
from time import perf_counter

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.services.demo_eval import build_sample_state, load_prospects  # noqa: E402
from app.services.pipeline_evals import build_eval_markdown, evaluate_state  # noqa: E402
from app.tenants import load_tenant  # noqa: E402


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run pipeline quality-gate evals.")
    parser.add_argument("--tenant", default="demo", help="Tenant slug. Defaults to demo.")
    parser.add_argument(
        "--mode",
        choices=("sample", "live"),
        default="sample",
        help="sample = offline fixture states; live = full workflow (needs API keys).",
    )
    parser.add_argument("--max", type=int, default=3, help="Max prospect rows to evaluate.")
    parser.add_argument(
        "--strict",
        action="store_true",
        help="Treat completeness gates (5 touches, 3 drafts) as blocking even in sample mode.",
    )
    parser.add_argument("--report", default="docs/pipeline-eval-report.md", help="Markdown report path.")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    tenant = load_tenant(args.tenant)
    prospects = load_prospects(tenant, limit=args.max)
    if not prospects:
        prospects = [{"company": "Sample Demo Account", "industry": "B2B SaaS"}]

    strict = args.strict or args.mode == "live"
    results = []
    started = perf_counter()

    for index, prospect in enumerate(prospects, start=1):
        company = prospect.get("company", f"Account {index}")
        if args.mode == "sample":
            state = build_sample_state(tenant, prospect, index)
        else:
            from app.agents.workflow_engine import build_workflow, run_workflow

            workflow = build_workflow(use_checkpointer=False)
            try:
                state = run_workflow(
                    workflow,
                    company=company,
                    industry=prospect.get("industry", "") or "unknown",
                    tenant=tenant,
                    sync_to_notion=False,
                    trigger_headline=prospect.get("notes", ""),
                )
            except Exception as exc:
                state = {"company": company, "error": f"{type(exc).__name__}: {exc}"}

        report = evaluate_state(state, strict=strict)
        results.append((company, report))
        print(f"{company}: {report.summary_line()}")
        for check in report.failures:
            print(f"    FAIL  {check.name}  {check.detail}")
        for check in report.warnings:
            print(f"    warn  {check.name}  {check.detail}")

    elapsed = perf_counter() - started
    report_path = ROOT / args.report
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(
        build_eval_markdown(results, title=f"Pipeline Eval Report — tenant `{args.tenant}`, mode `{args.mode}`"),
        encoding="utf-8",
    )

    failed = [name for name, report in results if not report.passed]
    print()
    print(f"Evaluated {len(results)} account(s) in {elapsed:.1f}s. Report: {report_path}")
    if failed:
        print(f"BLOCKING FAILURES on: {', '.join(failed)}")
        return 1
    print("All blocking gates passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
