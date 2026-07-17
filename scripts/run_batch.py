"""
Run the BDR pipeline over a whole prospect list from the command line.

Every run is persisted to pipeline/bdr.db (runs + prospect tracker rows) and
scored by the deterministic eval gates. Default mode is offline sample states
so batch behaviour can be verified without API keys.

Examples:
    python scripts/run_batch.py                             # demo tenant, sample mode
    python scripts/run_batch.py --mode live --limit 5       # live APIs, first 5 rows
    python scripts/run_batch.py --csv path/to/list.csv      # custom prospect list
"""
from __future__ import annotations

import argparse
import csv
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.services.batch_runner import batch_summary, run_batch  # noqa: E402
from app.tenants import load_tenant  # noqa: E402


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Batch-run the BDR pipeline over a prospect list.")
    parser.add_argument("--tenant", default="demo", help="Tenant slug. Defaults to demo.")
    parser.add_argument(
        "--mode",
        choices=("sample", "live"),
        default="sample",
        help="sample = offline fixture states (no API keys needed); live = full workflow.",
    )
    parser.add_argument("--limit", type=int, default=0, help="Max prospect rows to run (0 = all).")
    parser.add_argument("--csv", default="", help="Prospect CSV path. Defaults to the tenant's prospects.csv.")
    parser.add_argument("--sync-notion", action="store_true", help="Push live runs to Notion (live mode only).")
    return parser.parse_args()


def load_csv(path: Path) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        return [
            {str(k or "").strip(): str(v or "").strip() for k, v in row.items()}
            for row in csv.DictReader(handle)
            if (row.get("company") or "").strip()
        ]


def main() -> int:
    args = parse_args()
    tenant = load_tenant(args.tenant)

    prospects = None
    if args.csv:
        csv_path = Path(args.csv)
        if not csv_path.exists():
            print(f"CSV not found: {csv_path}")
            return 2
        prospects = load_csv(csv_path)

    def progress(index: int, total: int, company: str) -> None:
        print(f"[{index}/{total}] {company} ...", flush=True)

    results = run_batch(
        tenant,
        prospects,
        mode=args.mode,
        limit=args.limit or None,
        sync_to_notion=args.sync_notion,
        on_progress=progress,
    )

    if not results:
        print("No prospect rows found — nothing to run.")
        return 2

    print()
    header = f"{'Company':<28} {'Score':>5} {'Priority':<18} {'Critic':>6} {'Angle':<8} {'Eval':<6} Notes"
    print(header)
    print("-" * len(header))
    for r in results:
        eval_status = "pass" if r.eval_report.passed else "FAIL"
        notes = r.error or r.eval_report.failure_names()
        print(
            f"{r.company[:27]:<28} {str(r.account_score or '—'):>5} {r.priority_label:<18} "
            f"{('%.1f' % r.critic_score) if r.critic_score is not None else '—':>6} "
            f"{r.recommended_angle:<8} {eval_status:<6} {notes[:60]}"
        )

    summary = batch_summary(results)
    print()
    print(
        f"Batch complete: {summary['completed']}/{summary['total']} runs completed, "
        f"{summary['eval_passed']} passed all eval gates."
    )
    if summary["avg_critic"] is not None:
        print(f"Average critic score: {summary['avg_critic']}/5")
    print(
        f"Pipeline time: {summary['pipeline_minutes']} min vs ~{summary['est_manual_minutes']} min "
        f"at a {45}-min/prospect manual research benchmark (stated assumption, not a measurement)."
    )
    print("Runs persisted to pipeline/bdr.db — see the Batch/History views in the app.")
    return 0 if summary["completed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
