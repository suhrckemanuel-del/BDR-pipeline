"""
Run the council-vs-single-rater critic comparison for the BDR Pipeline.

One full pipeline run per prospect (shared generation), then the SAME state is
scored by both critic arms:

  - arm A: council_size=1  (original single-rater critic)
  - arm B: council_size=N  (default 3, multi-agent critique council)

Differences therefore reflect rater behavior only, not generation variance.
Requires ANTHROPIC_API_KEY (the critic arms make live LLM calls).

Examples:
    python scripts/run_council_eval.py
    python scripts/run_council_eval.py --max-accounts 3 --council-size 5
    python scripts/run_council_eval.py --tenant demo --csv docs/council-eval-results.csv
"""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.services.council_eval import (  # noqa: E402
    CouncilEvalResult,
    run_council_eval,
    summarize_results,
    write_csv,
    write_json,
    write_markdown,
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Compare single-rater vs. council critic verdicts on identical runs.",
    )
    parser.add_argument(
        "--tenant",
        default="demo",
        help="Tenant slug to evaluate. Defaults to demo.",
    )
    parser.add_argument(
        "--max-accounts",
        type=int,
        default=2,
        help="Maximum prospect rows to compare. Each account costs one full pipeline run plus two critic passes.",
    )
    parser.add_argument(
        "--council-size",
        type=int,
        default=3,
        choices=(2, 3, 4, 5),
        help="Council size for arm B (arm A is always council_size=1).",
    )
    parser.add_argument(
        "--csv",
        default="docs/council-eval-results.csv",
        help="CSV output path.",
    )
    parser.add_argument(
        "--json",
        default="docs/council-eval-results.json",
        help="JSON output path.",
    )
    parser.add_argument(
        "--docs",
        default="docs/council-evals.md",
        help="Markdown summary output path.",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()

    if not os.environ.get("ANTHROPIC_API_KEY"):
        print(
            "ANTHROPIC_API_KEY is not set. The council comparison runs live critic "
            "calls, so it cannot produce meaningful results without it.",
            file=sys.stderr,
        )
        return 2

    from dotenv import load_dotenv  # type: ignore

    load_dotenv(ROOT / ".env")

    if not os.environ.get("ANTHROPIC_API_KEY"):
        print(
            "ANTHROPIC_API_KEY is not set even after loading .env. Aborting.",
            file=sys.stderr,
        )
        return 2

    print(
        f"Running council comparison: tenant={args.tenant} · "
        f"accounts<={args.max_accounts} · council_size=1 vs {args.council_size} ..."
    )
    print("Each account = one full pipeline run + two critic passes on the shared state.")

    result: CouncilEvalResult = run_council_eval(
        tenant_id=args.tenant,
        max_accounts=args.max_accounts,
        council_size=args.council_size,
    )
    summary = summarize_results(result.rows)

    csv_path = ROOT / args.csv
    json_path = ROOT / args.json
    docs_path = ROOT / args.docs

    write_csv(result.rows, csv_path)
    write_json(result, summary, json_path)
    write_markdown(result, summary, docs_path, csv_path=Path(args.csv))

    completed = sum(1 for row in result.rows if row.get("run_completed") == "yes")
    print(
        f"Compared {completed}/{len(result.rows)} account(s) "
        f"(mode={result.mode}, council_size=1 vs {result.council_size})."
    )
    print(
        "Verdict shifts: "
        f"stricter={summary.get('council_stricter_count', 0)} · "
        f"looser={summary.get('council_looser_count', 0)} · "
        f"same={summary.get('same_verdict_count', 0)}"
    )
    print(
        "Avg copy quality: "
        f"single={summary.get('avg_quality_single', 0.0):.2f}/5 · "
        f"council={summary.get('avg_quality_council', 0.0):.2f}/5 · "
        f"delta={summary.get('avg_quality_delta', 0.0):+.2f}"
    )
    print(f"Total disagreement flags (council arm): {summary.get('total_disagreements', 0)}")
    print(f"CSV: {csv_path}")
    print(f"JSON: {json_path}")
    print(f"Docs: {docs_path}")
    print("No reply rates, meeting rates, revenue, or campaign lift are claimed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
