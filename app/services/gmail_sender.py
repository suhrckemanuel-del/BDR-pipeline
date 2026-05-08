"""
gmail_sender.py — Gmail SMTP sender for outreach sequences.

Uses Gmail App Password (simpler than OAuth2, already proven in scripts/send_via_gmail.py).

Setup (one-time):
  1. Enable 2-Step Verification on your Gmail account
  2. Go to myaccount.google.com/apppasswords
  3. Create App Password -> select "Mail" + device name -> copy 16-char password
  4. Add to .env:
       GMAIL_APP_PASSWORD=xxxx xxxx xxxx xxxx
       GMAIL_SENDER=your-email@gmail.com

Queue system:
  - Each approved sequence is saved as tenants/<tenant_id>/data/queued/{company}_{date}.json
  - Queue file tracks sent status per touch + scheduled dates
  - load_queue() / get_due_touches() / mark_sent() provide the interface
"""
from __future__ import annotations

import json
import os
import smtplib
from datetime import date, datetime, timedelta
from email.mime.text import MIMEText
from pathlib import Path

SMTP_HOST = "smtp.gmail.com"
SMTP_PORT = 587

ROOT = Path(__file__).resolve().parents[2]


def _queue_dir(tenant_id: str) -> Path:
    """Per-tenant queue directory under tenants/<tenant_id>/data/queued/."""
    return ROOT / "tenants" / tenant_id / "data" / "queued"


# ---------------------------------------------------------------------------
# Core send
# ---------------------------------------------------------------------------
def send_email(
    to_email: str,
    to_name: str,
    subject: str,
    body: str,
    sender: str = "",
    app_password: str = "",
) -> tuple[bool, str]:
    """
    Send a single plain-text email via Gmail SMTP.
    Returns (success, error_message).
    """
    sender = sender or os.environ.get("GMAIL_SENDER", "").strip()
    app_password = app_password or os.environ.get("GMAIL_APP_PASSWORD", "").strip()

    if not sender:
        return False, "GMAIL_SENDER not set in .env"
    if not app_password:
        return False, "GMAIL_APP_PASSWORD not set in .env — create one at myaccount.google.com/apppasswords"

    msg = MIMEText(body, "plain", "utf-8")
    msg["From"] = sender
    msg["To"] = f"{to_name} <{to_email}>" if to_name else to_email
    msg["Subject"] = subject

    try:
        with smtplib.SMTP(SMTP_HOST, SMTP_PORT, timeout=15) as smtp:
            smtp.ehlo()
            smtp.starttls()
            smtp.login(sender, app_password)
            smtp.sendmail(sender, [to_email], msg.as_string())
        return True, ""
    except smtplib.SMTPAuthenticationError:
        return False, "Gmail authentication failed. Check GMAIL_APP_PASSWORD in .env."
    except smtplib.SMTPException as e:
        return False, f"SMTP error: {e}"
    except Exception as e:
        return False, f"Send failed: {e}"


# ---------------------------------------------------------------------------
# Queue system
# ---------------------------------------------------------------------------
def _queue_path(tenant_id: str, company: str, created: str) -> Path:
    safe = company.lower().replace(" ", "_").replace("/", "-")
    return _queue_dir(tenant_id) / f"{safe}_{created}.json"


def queue_sequence(
    tenant_id: str,
    company: str,
    recipient_email: str,
    recipient_name: str,
    touches: list[dict],
    send_date: str = "",
) -> Path:
    """
    Save touches 2-N to the queue. Touch 1 is assumed already sent.
    Each touch gets a scheduled_date = send_date + touch.day.

    touches: list of dicts with keys: touch_number, day, channel, subject, body
    Returns the queue file path.
    """
    queue_dir = _queue_dir(tenant_id)
    queue_dir.mkdir(parents=True, exist_ok=True)
    today = send_date or date.today().isoformat()
    base = datetime.strptime(today, "%Y-%m-%d").date()

    record = {
        "company": company,
        "recipient_email": recipient_email,
        "recipient_name": recipient_name,
        "created": today,
        "touches": [],
    }
    for t in touches:
        scheduled = (base + timedelta(days=t["day"])).isoformat()
        record["touches"].append({
            "touch_number": t["touch_number"],
            "day": t["day"],
            "channel": t["channel"],
            "subject": t.get("subject", ""),
            "body": t["body"],
            "scheduled_date": scheduled,
            "sent": False,
            "sent_date": "",
        })

    path = _queue_path(tenant_id, company, today)
    path.write_text(json.dumps(record, indent=2, ensure_ascii=False), encoding="utf-8")
    return path


def load_queue(tenant_id: str) -> list[dict]:
    """Load all queued sequences for a tenant. Returns list of records."""
    queue_dir = _queue_dir(tenant_id)
    if not queue_dir.exists():
        return []
    records = []
    for f in sorted(queue_dir.glob("*.json")):
        try:
            records.append(json.loads(f.read_text(encoding="utf-8")))
        except Exception:
            pass
    return records


def get_due_touches(tenant_id: str, as_of: str = "") -> list[dict]:
    """
    Return all touches in this tenant's queue where:
      - channel == "email"
      - sent == False
      - scheduled_date <= today
    Each item includes top-level company/email/name context.
    """
    today = as_of or date.today().isoformat()
    due = []
    for record in load_queue(tenant_id):
        for touch in record.get("touches", []):
            if (
                touch.get("channel") == "email"
                and not touch.get("sent")
                and touch.get("scheduled_date", "9999") <= today
            ):
                due.append({
                    **touch,
                    "company": record["company"],
                    "recipient_email": record["recipient_email"],
                    "recipient_name": record["recipient_name"],
                    "_source_file": _queue_path(tenant_id, record["company"], record["created"]),
                })
    return due


def mark_sent(tenant_id: str, company: str, created: str, touch_number: int) -> bool:
    """Mark a queued touch as sent. Returns True if found and updated."""
    path = _queue_path(tenant_id, company, created)
    if not path.exists():
        return False
    record = json.loads(path.read_text(encoding="utf-8"))
    for t in record["touches"]:
        if t["touch_number"] == touch_number:
            t["sent"] = True
            t["sent_date"] = date.today().isoformat()
            break
    else:
        return False
    path.write_text(json.dumps(record, indent=2, ensure_ascii=False), encoding="utf-8")
    return True


def pending_count(tenant_id: str) -> int:
    """Quick count of unsent email touches in this tenant's queue."""
    return sum(
        1
        for r in load_queue(tenant_id)
        for t in r.get("touches", [])
        if not t.get("sent") and t.get("channel") == "email"
    )
