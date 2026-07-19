"""
reply_tracker.py — Detect inbound replies via Gmail IMAP and close the loop.

The reply-rate loop: prospects marked 'sent' (or 'queued') in the tracker have
a contact email; this module polls the sender's Gmail inbox over IMAP for
messages from those addresses, marks matching prospects 'replied', and logs an
event carrying the outreach angle — which powers per-angle reply-rate stats.

Auth reuses the existing Gmail App Password setup (GMAIL_SENDER +
GMAIL_APP_PASSWORD in .env) — the same credentials work for SMTP and IMAP.

Deliberately conservative: read-only IMAP (no flags changed, nothing deleted),
sender-address matching only, and a bounded SINCE window.
"""
from __future__ import annotations

import email
import email.utils
import imaplib
import os
from dataclasses import dataclass
from datetime import date, timedelta

from app.services import store

IMAP_HOST = "imap.gmail.com"
IMAP_PORT = 993

# Tracker statuses that make a prospect eligible for reply matching.
CONTACTED_STATUSES = ("queued", "sent")


@dataclass
class ReplyHit:
    company: str
    contact_email: str
    subject: str
    received: str
    angle: str


@dataclass
class ReplyCheckResult:
    checked_addresses: int
    hits: list[ReplyHit]
    errors: list[str]

    @property
    def ok(self) -> bool:
        return not self.errors


def _credentials() -> tuple[str, str]:
    sender = os.environ.get("GMAIL_SENDER", "").strip()
    password = os.environ.get("GMAIL_APP_PASSWORD", "").strip()
    return sender, password


def _decode_header(value: str) -> str:
    try:
        parts = email.header.decode_header(value or "")
        return "".join(
            chunk.decode(enc or "utf-8", errors="replace") if isinstance(chunk, bytes) else chunk
            for chunk, enc in parts
        )
    except Exception:
        return value or ""


def _search_from(imap: imaplib.IMAP4_SSL, address: str, since: date) -> list[bytes]:
    since_str = since.strftime("%d-%b-%Y")
    status, data = imap.search(None, "FROM", f'"{address}"', "SINCE", since_str)
    if status != "OK" or not data or not data[0]:
        return []
    return data[0].split()


def check_replies(tenant_id: str, *, since_days: int = 30, dry_run: bool = False) -> ReplyCheckResult:
    """
    Poll the inbox for replies from contacted prospects of this tenant.

    dry_run reports hits without updating prospect statuses — useful for
    verifying matching before letting it drive the tracker.
    """
    sender, password = _credentials()
    if not sender or not password:
        return ReplyCheckResult(
            checked_addresses=0,
            hits=[],
            errors=["GMAIL_SENDER or GMAIL_APP_PASSWORD not set in .env — reply tracking needs both."],
        )

    candidates = [
        p
        for p in store.list_prospects(tenant_id)
        if p.get("status") in CONTACTED_STATUSES and (p.get("contact_email") or "").strip()
    ]
    if not candidates:
        return ReplyCheckResult(checked_addresses=0, hits=[], errors=[])

    since = date.today() - timedelta(days=since_days)
    hits: list[ReplyHit] = []
    errors: list[str] = []

    try:
        imap = imaplib.IMAP4_SSL(IMAP_HOST, IMAP_PORT)
    except Exception as exc:
        return ReplyCheckResult(0, [], [f"IMAP connect failed: {exc}"])

    try:
        try:
            imap.login(sender, password)
        except imaplib.IMAP4.error:
            return ReplyCheckResult(
                0, [], ["Gmail IMAP authentication failed. Check GMAIL_APP_PASSWORD in .env."]
            )
        imap.select("INBOX", readonly=True)

        for prospect in candidates:
            address = prospect["contact_email"].strip()
            try:
                message_ids = _search_from(imap, address, since)
            except Exception as exc:
                errors.append(f"{prospect['company']}: IMAP search failed ({exc})")
                continue
            if not message_ids:
                continue

            # Newest matching message for context.
            subject, received = "", ""
            try:
                status, data = imap.fetch(message_ids[-1], "(BODY.PEEK[HEADER.FIELDS (SUBJECT DATE)])")
                if status == "OK" and data and data[0]:
                    headers = email.message_from_bytes(data[0][1])
                    subject = _decode_header(headers.get("Subject", ""))
                    received = headers.get("Date", "")
            except Exception:
                pass

            hit = ReplyHit(
                company=prospect["company"],
                contact_email=address,
                subject=subject,
                received=received,
                angle=prospect.get("angle", "") or "",
            )
            hits.append(hit)

            if not dry_run:
                store.set_prospect_status(
                    tenant_id,
                    prospect["company"],
                    "replied",
                    detail=f"IMAP reply from {address}: {subject[:120]}",
                )
                store.log_event(
                    tenant_id,
                    prospect["company"],
                    "reply_detected",
                    detail=subject[:200],
                    angle=hit.angle,
                )
    finally:
        try:
            imap.logout()
        except Exception:
            pass

    return ReplyCheckResult(checked_addresses=len(candidates), hits=hits, errors=errors)
