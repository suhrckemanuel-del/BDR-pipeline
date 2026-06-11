"""
Run lightweight demo/eval checks for the BDR Pipeline.

Default mode is offline sample-state evaluation so the portfolio demo remains
measurable even when LLM/enrichment APIs are unavailable.

Examples:
    python scripts/run_demo_eval.py
    python scripts/run_demo_eval.py --mode live --max-accounts 2
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.services.demo_eval import (  # noqa: E402
    DemoEvalResult,
    run_live_eval,
    run_sample_eval,
    write_csv,
    write_evals_markdown,
    write_json,
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Generate honest internal demo workflow metrics.",
    )
    parser.add_argument(
        "--tenant",
        default="demo",
        help="Tenant slug to evaluate. Defaults to demo.",
    )
    parser.add_argument(
        "--mode",
        choices=("sample", "live"),
        default="sample",
        help="sample uses deterministic offline states; live runs the full workflow.",
    )
    parser.add_argument(
        "--max-accounts",
        type=int,
        default=3,
        help="Maximum prospect rows to evaluate.",
    )
    parser.add_argument(
        "--csv",
        default="docs/eval-results.csv",
        help="CSV output path.",
    )
    parser.add_argument(
        "--json",
        default="docs/eval-results.json",
        help="JSON output path.",
    )
    parser.add_argument(
        "--docs",
        default="docs/evals.md",
        help="Markdown summary output path.",
    )
    return parser.parse_args()


def run(args: argparse.Namespace) -> DemoEvalResult:
    if args.mode == "live":
        return run_live_eval(tenant_id=args.tenant, max_accounts=args.max_accounts)
    return run_sample_eval(tenant_id=args.tenant, max_accounts=args.max_accounts)


def main() -> int:
    args = parse_args()
    result = run(args)

    csv_path = ROOT / args.csv
    json_path = ROOT / args.json
    docs_path = ROOT / args.docs

    write_csv(result.rows, csv_path)
    write_json(result, json_path)
    write_evals_markdown(result, docs_path, csv_path=Path(args.csv))

    completed = sum(1 for row in result.rows if row.get("run_completed") == "yes")
    print(
        "Generated internal demo workflow metrics "
        f"({completed}/{len(result.rows)} completed, mode={result.mode})."
    )
    print(f"CSV: {csv_path}")
    print(f"JSON: {json_path}")
    print(f"Docs: {docs_path}")
    print("No reply rates, meeting rates, revenue, or campaign lift are claimed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
