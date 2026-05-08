"""
Send outreach emails from a Gmail account via SMTP.

Reads a ready-to-send CSV (output of generate_outreach_emails.py) and sends
each row that hasn't been sent yet (sent != 'yes') and has a to_email.

After each send, marks sent=yes in the CSV and appends to pipeline/sent_log.csv.

Setup:
  1. Enable 2-Step Verification on the Gmail account
  2. Create an App Password: myaccount.google.com/apppasswords
     -> select "Mail" + "Windows Computer" -> copy the 16-char password
  3. Add to .env:
       GMAIL_APP_PASSWORD=xxxx xxxx xxxx xxxx
       GMAIL_SENDER=your-email@gmail.com

Usage:
  python scripts/send_via_gmail.py --csv outreach/ready-to-send/group-a-small-ceo.csv --dry-run
  python scripts/send_via_gmail.py --csv outreach/ready-to-send/group-a-small-ceo.csv --send
  python scripts/send_via_gmail.py --csv outreach/ready-to-send/group-b-large-gtm.csv --send
  python scripts/send_via_gmail.py --csv outreach/ready-to-send/group-a-small-ceo.csv --send --daily-limit 10
"""
from __future__ import annotations

import argparse
import csv
import os
import smtplib
import sys
import time
from datetime import date
from email.mime.text import MIMEText
from pathlib import Path

SMTP_HOST = "smtp.gmail.com"
SMTP_PORT = 587


def load_env_file(path: Path) -> None:
    if not path.is_file():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, val = line.partition("=")
        key, val = key.strip(), val.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = val


def append_sent_log(log_path: Path, record: dict) -> None:
    is_new = not log_path.exists()
    with log_path.open("a", newline="", encoding="utf-8-sig") as f:
        fields = ["date", "company", "to_name", "to_email", "subject", "split_test_group"]
        w = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
        if is_new:
            w.writeheader()
        w.writerow(record)


def send_email(
    smtp: smtplib.SMTP,
    sender: str,
    to_email: str,
    to_name: str,
    subject: str,
    body: str,
) -> None:
    msg = MIMEText(body, "plain", "utf-8")
    msg["From"] = sender
    msg["To"] = f"{to_name} <{to_email}>" if to_name else to_email
    msg["Subject"] = subject
    smtp.sendmail(sender, [to_email], msg.as_string())


def main() -> None:
    root = Path(__file__).resolve().parents[1]
    load_env_file(root / ".env")

    p = argparse.ArgumentParser(description="Send outreach emails via Gmail SMTP")
    p.add_argument("--csv", required=True, help="Path to ready-to-send CSV (relative to repo root or absolute)")
    p.add_argument("--dry-run", action="store_true", help="Print emails without sending")
    p.add_argument("--send", action="store_true", help="Actually send (requires GMAIL_APP_PASSWORD)")
    p.add_argument("--daily-limit", type=int, default=20, help="Max emails to send per run (default 20)")
    p.add_argument("--sleep", type=float, default=3.0, help="Seconds between sends (default 3)")
    args = p.parse_args()

    if not args.dry_run and not args.send:
        print("Pass --dry-run to preview or --send to send.", file=sys.stderr)
        sys.exit(1)

    sender = (os.environ.get("GMAIL_SENDER") or "").strip()
    app_password = (os.environ.get("GMAIL_APP_PASSWORD") or "").strip()

    if args.send and not app_password:
        print(
            "GMAIL_APP_PASSWORD not set.\n"
            "Create one at myaccount.google.com/apppasswords and add it to .env.",
            file=sys.stderr,
        )
        sys.exit(1)

    csv_path = Path(args.csv)
    if not csv_path.is_absolute():
        csv_path = root / csv_path
    if not csv_path.exists():
        print(f"CSV not found: {csv_path}", file=sys.stderr)
        sys.exit(1)

    with csv_path.open(newline="", encoding="utf-8-sig") as f:
        rows = list(csv.DictReader(f))
        fieldnames = list(rows[0].keys()) if rows else []

    pending = [r for r in rows if (r.get("sent") or "").strip().lower() != "yes"
                                  and (r.get("to_email") or "").strip()]
    skip_no_email = [r for r in rows if not (r.get("to_email") or "").strip()
                                         and (r.get("sent") or "").strip().lower() != "yes"]

    print(f"Pending: {len(pending)} | Already sent: {sum(1 for r in rows if r.get('sent')=='yes')} | "
          f"No email: {len(skip_no_email)}")

    if skip_no_email:
        print(f"\n[!] {len(skip_no_email)} rows skipped (no to_email):")
        for r in skip_no_email:
            print(f"    {r.get('company')} — {r.get('to_name') or 'unknown'}")
        print("    -> Run domain_search.py to fill missing emails, or add them manually.\n")

    to_send = pending[:args.daily_limit]

    if not to_send:
        print("Nothing to send.")
        return

    if args.dry_run:
        print(f"\n--- DRY RUN: {len(to_send)} email(s) ---")
        for r in to_send:
            print(f"\n{'='*60}")
            print(f"To:      {r.get('to_name')} <{r.get('to_email')}>")
            print(f"From:    {sender or 'GMAIL_SENDER_NOT_SET'}")
            print(f"Subject: {r.get('subject')}")
            print(f"Group:   {r.get('split_test_group')}")
            print(f"Body:\n{r.get('body')}")
        print(f"\n--dry-run: no emails sent.")
        return

    # --- SEND ---
    log_path = root / "pipeline" / "sent_log.csv"
    sent_count = 0

    print(f"\nConnecting to {SMTP_HOST}:{SMTP_PORT}...")
    with smtplib.SMTP(SMTP_HOST, SMTP_PORT) as smtp:
        smtp.ehlo()
        smtp.starttls()
        smtp.login(sender, app_password)
        print("Authenticated.\n")

        for r in to_send:
            to_email = r["to_email"].strip()
            to_name = (r.get("to_name") or "").strip()
            subject = (r.get("subject") or "").strip()
            body = (r.get("body") or "").strip()

            print(f"  Sending -> {to_name} <{to_email}>  [{r.get('company')}]")
            try:
                send_email(smtp, sender, to_email, to_name, subject, body)
                r["sent"] = "yes"
                r["send_date"] = date.today().isoformat()
                sent_count += 1
                append_sent_log(log_path, {
                    "date":             date.today().isoformat(),
                    "company":          r.get("company", ""),
                    "to_name":          to_name,
                    "to_email":         to_email,
                    "subject":          subject,
                    "split_test_group": r.get("split_test_group", ""),
                })
                time.sleep(max(args.sleep, 0))
            except Exception as e:
                print(f"    [ERROR] {e}", file=sys.stderr)

    # Write updated CSV (marks sent=yes)
    with csv_path.open("w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
        w.writeheader()
        w.writerows(rows)

    print(f"\nSent {sent_count} email(s). Log: {log_path}")


if __name__ == "__main__":
    main()
