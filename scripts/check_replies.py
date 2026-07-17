"""
Check the Gmail inbox for replies from contacted prospects and update the tracker.

Requires GMAIL_SENDER + GMAIL_APP_PASSWORD in .env (same credentials as the
sender). Matching prospects move to status 'replied' and a reply event is
logged with the outreach angle for per-angle reply-rate stats.

Examples:
    python scripts/check_replies.py                     # demo tenant, last 30 days
    python scripts/check_replies.py --dry-run           # report matches, change nothing
    python scripts/check_replies.py --since-days 7
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

try:
    from dotenv import load_dotenv  # type: ignore

    load_dotenv(ROOT / ".env")
except ImportError:
    pass

from app.services import store  # noqa: E402
from app.services.reply_tracker import check_replies  # noqa: E402


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Detect prospect replies via Gmail IMAP.")
    parser.add_argument("--tenant", default="demo", help="Tenant slug. Defaults to demo.")
    parser.add_argument("--since-days", type=int, default=30, help="How far back to search the inbox.")
    parser.add_argument("--dry-run", action="store_true", help="Report matches without updating statuses.")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    result = check_replies(args.tenant, since_days=args.since_days, dry_run=args.dry_run)

    for error in result.errors:
        print(f"ERROR: {error}")
    if result.errors and not result.checked_addresses:
        return 2

    print(f"Checked {result.checked_addresses} contacted prospect(s) over the last {args.since_days} days.")
    if not result.hits:
        print("No replies detected.")
    for hit in result.hits:
        action = "would mark" if args.dry_run else "marked"
        print(f"REPLY  {hit.company} <{hit.contact_email}> — {hit.subject!r} ({action} replied, angle={hit.angle or '—'})")

    stats = store.angle_reply_stats(args.tenant)
    if stats:
        print("\nReply rate by angle:")
        for row in stats:
            pct = f"{row['reply_rate'] * 100:.0f}%"
            print(f"  {row['angle']}: {row['replied']}/{row['contacted']} ({pct})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
