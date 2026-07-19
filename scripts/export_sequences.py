"""
Export queued, eval-passed sequences to sending-tool CSVs (Instantly / Smartlead).

Reads only the SQLite tracker — fully offline, no API keys needed. Only
prospects whose latest run passed the eval gates export; every skip prints a
reason. One `exported` event is logged per exported prospect (skipped with
--dry-run).

Examples:
    python scripts/export_sequences.py                          # demo tenant, both formats
    python scripts/export_sequences.py --format instantly       # one format
    python scripts/export_sequences.py --status queued --status sent
    python scripts/export_sequences.py --dry-run                # summary only, no files/events
"""
from __future__ import annotations

import argparse
import sys
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.services import store  # noqa: E402
from app.services.sequence_export import EXPORT_FORMATS, collect_export_rows, export_sequences  # noqa: E402


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Export sequences to sending-tool CSVs.")
    parser.add_argument("--tenant", default="demo", help="Tenant slug. Defaults to demo.")
    parser.add_argument(
        "--format",
        choices=(*EXPORT_FORMATS, "both"),
        default="both",
        help="Sending tool CSV format. Defaults to both.",
    )
    parser.add_argument(
        "--status",
        action="append",
        default=None,
        choices=store.PROSPECT_STATUSES,
        help="Tracker status to export (repeatable). Defaults to queued.",
    )
    parser.add_argument("--out", default="exports", help="Output directory, relative to repo root.")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print the exported/skipped summary without writing files or logging events.",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    statuses = tuple(args.status) if args.status else ("queued",)
    formats = EXPORT_FORMATS if args.format == "both" else (args.format,)

    if args.dry_run:
        rows, skipped = collect_export_rows(args.tenant, statuses)
        for row in rows:
            print(f"export  {row['company']}  ({row['email']}, {len(row['touches'])} email touch(es))")
        for company, reason in skipped:
            print(f"skip    {company}  — {reason}")
        if not rows:
            print("0 eligible prospects.")
        else:
            print(f"\n{len(rows)} prospect(s) would export to: {', '.join(formats)} (dry run — nothing written).")
        return 0

    out_dir = ROOT / args.out
    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = date.today().isoformat()

    wrote_any = False
    for fmt in formats:
        # Events log on the first format only — one `exported` event per
        # prospect per export action, not per file.
        result = export_sequences(args.tenant, fmt, statuses, log_events=not wrote_any)
        if not result.exported:
            for company, reason in result.skipped:
                print(f"skip    {company}  — {reason}")
            print("0 eligible prospects.")
            return 0
        path = out_dir / f"{args.tenant}_{fmt}_{stamp}.csv"
        path.write_text(result.csv_text, encoding="utf-8")
        rel = path.relative_to(ROOT)
        for company in result.exported:
            print(f"export  {company}  -> {rel}")
        for company, reason in result.skipped:
            print(f"skip    {company}  — {reason}")
        wrote_any = True

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
