"""
sequence_export.py — export eval-passed, queued sequences to sending-tool CSVs.

Fully offline: reads only the SQLite store (app/services/store.py), no network
and no LLM calls. Supports Instantly and Smartlead lead-list CSVs. Both tools
column-map on import and accept arbitrary extra columns as custom variables
({{column_name}} in campaign copy), so beyond the lead fields each row carries
the per-touch personalized copy as subject_N / body_N / day_N columns. Sequence
steps and send delays are configured inside the sending tool — the CSV carries
leads plus the copy to merge in.

Eligibility is strict: a prospect exports only when its tracker status matches
(default: queued), its latest run passed the eval gates (prospects.eval_passed
== 1 — store.upsert_prospect_from_state always overwrites eval_passed and
last_run_id with the newest run), it has a contact email, and the latest run's
rehydrated sequence contains at least one email touch. LinkedIn touches are
omitted — both tools are email senders.
"""
from __future__ import annotations

import csv
import io
from dataclasses import dataclass
from typing import Optional, Sequence

from app.agents.state import OutreachSequence, SequenceTouch
from app.services import store

EXPORT_FORMATS = ("instantly", "smartlead")

INSTANTLY_BASE_HEADERS = [
    "email",
    "first_name",
    "last_name",
    "company_name",
    "website",
    "personalization",
    "angle",
]
SMARTLEAD_BASE_HEADERS = [
    "first_name",
    "last_name",
    "email",
    "company_name",
    "website",
    "linkedin_profile",
    "angle",
]


@dataclass
class ExportResult:
    fmt: str
    csv_text: str
    exported: list[str]  # company names, in exported (sorted-by-company) order
    skipped: list[tuple[str, str]]  # (company, reason)
    headers: list[str]


def _split_name(name: str) -> tuple[str, str]:
    """Split a display name into (first, rest). ('', '') on empty input."""
    parts = (name or "").split()
    if not parts:
        return "", ""
    return parts[0], " ".join(parts[1:])


def _email_touches(sequence: OutreachSequence) -> list[SequenceTouch]:
    """Email touches only, in sequence order — LinkedIn touches don't export."""
    return [touch for touch in sequence.touches if touch.channel == "email"]


def _run_state(prospect_row: dict) -> Optional[dict]:
    """Rehydrate the latest run's state for a tracker row, or None."""
    run_id = prospect_row.get("last_run_id")
    if not run_id:
        return None
    run = store.get_run(int(run_id))
    if not run:
        return None
    return store.rehydrate_state(run)


def _sequence_from_state(state: Optional[dict]) -> Optional[OutreachSequence]:
    if not state:
        return None
    card = state.get("card")
    if card is None:
        return None
    return getattr(card, "sequence", None)


def _sequence_for(prospect_row: dict) -> Optional[OutreachSequence]:
    """The outreach sequence from the prospect's latest run, or None."""
    return _sequence_from_state(_run_state(prospect_row))


def _linkedin_from_state(state: Optional[dict]) -> str:
    if not state:
        return ""
    contacts = getattr(state.get("enrichment"), "contacts", None) or []
    if not contacts:
        return ""
    return getattr(contacts[0], "linkedin_url", "") or ""


def collect_export_rows(
    tenant_id: str,
    statuses: Sequence[str] = ("queued",),
) -> tuple[list[dict], list[tuple[str, str]]]:
    """Gather exportable rows for a tenant, plus (company, reason) skips.

    Row dicts: company, email, first_name, last_name, website, linkedin_profile,
    angle, and touches — a list of (subject, body, day) per email touch.
    Rows are sorted by company for deterministic output.
    """
    rows: list[dict] = []
    skipped: list[tuple[str, str]] = []

    for prospect in store.list_prospects(tenant_id):
        company = prospect.get("company", "")
        status = prospect.get("status", "")
        if status not in statuses:
            skipped.append((company, f"status is '{status}'"))
            continue
        eval_passed = prospect.get("eval_passed")
        if eval_passed != 1:
            reason = "latest run failed eval gates" if eval_passed == 0 else "never evaluated"
            skipped.append((company, reason))
            continue
        email = (prospect.get("contact_email") or "").strip()
        if not email:
            skipped.append((company, "no contact email"))
            continue
        state = _run_state(prospect)
        sequence = _sequence_from_state(state)
        touches = _email_touches(sequence) if sequence else []
        if not touches:
            skipped.append((company, "no exportable sequence on latest run"))
            continue

        first_name, last_name = _split_name(prospect.get("contact_name") or "")
        rows.append(
            {
                "company": company,
                "email": email,
                "first_name": first_name,
                "last_name": last_name,
                "website": prospect.get("domain") or "",
                "linkedin_profile": _linkedin_from_state(state),
                "angle": prospect.get("angle") or "",
                "touches": [(t.subject or "", t.body or "", t.day) for t in touches],
            }
        )

    rows.sort(key=lambda row: row["company"])
    skipped.sort(key=lambda item: item[0])
    return rows, skipped


def build_csv(rows: list[dict], fmt: str) -> tuple[str, list[str]]:
    """Render collected rows as CSV text for one sending tool.

    Touch columns (subject_N / body_N / day_N) are sized to the max email-touch
    count across the exported rows. csv.writer quoting keeps multiline bodies
    intact on import.
    """
    if fmt not in EXPORT_FORMATS:
        raise ValueError(f"Unknown export format {fmt!r}. Valid: {EXPORT_FORMATS}")

    base = INSTANTLY_BASE_HEADERS if fmt == "instantly" else SMARTLEAD_BASE_HEADERS
    max_touches = max((len(row["touches"]) for row in rows), default=0)
    headers = list(base)
    for i in range(1, max_touches + 1):
        headers += [f"subject_{i}", f"body_{i}", f"day_{i}"]

    buffer = io.StringIO()
    writer = csv.writer(buffer, lineterminator="\n")
    writer.writerow(headers)
    for row in rows:
        touches = row["touches"]
        if fmt == "instantly":
            first_body = touches[0][1] if touches else ""
            personalization = first_body.splitlines()[0] if first_body else ""
            record = [
                row["email"],
                row["first_name"],
                row["last_name"],
                row["company"],
                row["website"],
                personalization,
                row["angle"],
            ]
        else:
            record = [
                row["first_name"],
                row["last_name"],
                row["email"],
                row["company"],
                row["website"],
                row["linkedin_profile"],
                row["angle"],
            ]
        for i in range(max_touches):
            if i < len(touches):
                subject, body, day = touches[i]
                record += [subject, body, day]
            else:
                record += ["", "", ""]
        writer.writerow(record)
    return buffer.getvalue(), headers


def export_sequences(
    tenant_id: str,
    fmt: str,
    statuses: Sequence[str] = ("queued",),
    *,
    log_events: bool = True,
) -> ExportResult:
    """Export a tenant's eligible sequences to one sending-tool CSV.

    When log_events is True, one 'exported' event is logged per exported
    prospect (detail = format, angle = prospect angle).
    """
    rows, skipped = collect_export_rows(tenant_id, statuses)
    csv_text, headers = build_csv(rows, fmt)
    if log_events:
        for row in rows:
            store.log_event(tenant_id, row["company"], "exported", detail=fmt, angle=row["angle"])
    return ExportResult(
        fmt=fmt,
        csv_text=csv_text,
        exported=[row["company"] for row in rows],
        skipped=skipped,
        headers=headers,
    )
