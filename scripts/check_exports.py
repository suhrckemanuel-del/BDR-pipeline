"""
Offline regression checks for the sending-tool export path.

Seeds a scratch SQLite store (BDR_DB_PATH points at a temp file — the real
pipeline/bdr.db is never touched) with prospects in every eligibility state,
then asserts the export service's filtering, CSV shape, determinism, and event
logging. No network, no API keys. Exit 1 on any failed assertion.

    python scripts/check_exports.py
"""
from __future__ import annotations

import csv
import io
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

_SCRATCH = tempfile.mkdtemp(prefix="bdr-check-exports-")
os.environ["BDR_DB_PATH"] = str(Path(_SCRATCH) / "check_exports.db")

from app.services import store  # noqa: E402
from app.services.demo_eval import build_sample_state  # noqa: E402
from app.services.sequence_export import (  # noqa: E402
    INSTANTLY_BASE_HEADERS,
    SMARTLEAD_BASE_HEADERS,
    export_sequences,
)
from app.tenants import load_tenant  # noqa: E402

TENANT_ID = "demo"


def _seed_prospect(tenant, company: str, *, index: int, eval_passed: bool, status: str, email: str = "") -> dict:
    state = build_sample_state(tenant, {"company": company, "industry": "B2B SaaS", "domain": "check.co"}, index)
    if email and state["enrichment"].contacts:
        state["enrichment"].contacts[0].email = email
    run_id = store.record_run(state, source="check", mode="sample", eval_passed=eval_passed)
    store.upsert_prospect_from_state(state, run_id, eval_passed=eval_passed)
    if status != "researched":
        store.set_prospect_status(TENANT_ID, company, status)
    return state


def main() -> int:
    failures: list[str] = []

    def check(name: str, condition: bool, detail: str = "") -> None:
        print(f"{'PASS' if condition else 'FAIL'}  {name}" + (f"  {detail}" if detail and not condition else ""))
        if not condition:
            failures.append(name)

    tenant = load_tenant(TENANT_ID)

    # index=3 -> has a contact (email injected); index=1 -> no contacts at all.
    p1_state = _seed_prospect(tenant, "Export Pass Co", index=3, eval_passed=True, status="queued", email="p1@pass.co")
    _seed_prospect(tenant, "Eval Fail Co", index=6, eval_passed=False, status="queued", email="p2@fail.co")
    _seed_prospect(tenant, "No Email Co", index=1, eval_passed=True, status="queued")
    _seed_prospect(tenant, "Still Researched Co", index=9, eval_passed=True, status="researched", email="p4@wait.co")

    result = export_sequences(TENANT_ID, "instantly", log_events=False)
    check("only_eval_passed_queued_exports", result.exported == ["Export Pass Co"], f"exported={result.exported}")
    skip_reasons = dict(result.skipped)
    check("eval_fail_skip_reason", "eval" in skip_reasons.get("Eval Fail Co", ""), str(result.skipped))
    check("no_email_skip_reason", "email" in skip_reasons.get("No Email Co", ""), str(result.skipped))
    check("status_skip_reason", "status" in skip_reasons.get("Still Researched Co", ""), str(result.skipped))

    parsed = list(csv.reader(io.StringIO(result.csv_text)))
    expected_header = INSTANTLY_BASE_HEADERS + ["subject_1", "body_1", "day_1"]
    check("instantly_header_exact", parsed[0] == expected_header, f"header={parsed[0]}")
    check("one_data_row", len(parsed) == 2, f"rows={len(parsed) - 1}")

    row = dict(zip(parsed[0], parsed[1]))
    email_touch = next(t for t in p1_state["card"].sequence.touches if t.channel == "email")
    check("email_cell", row["email"] == "p1@pass.co", row["email"])
    check("body_roundtrip", row["body_1"] == email_touch.body)
    check("subject_roundtrip", row["subject_1"] == email_touch.subject)
    check("day_roundtrip", row["day_1"] == str(email_touch.day), row["day_1"])
    check("company_cell", row["company_name"] == "Export Pass Co", row["company_name"])
    prospect = next(p for p in store.list_prospects(TENANT_ID) if p["company"] == "Export Pass Co")
    check("angle_cell", row["angle"] == (prospect["angle"] or ""), row["angle"])

    smartlead = export_sequences(TENANT_ID, "smartlead", log_events=False)
    sl_header = list(csv.reader(io.StringIO(smartlead.csv_text)))[0]
    check("smartlead_header_prefix", sl_header[:3] == ["first_name", "last_name", "email"], str(sl_header))
    check("smartlead_has_linkedin", "linkedin_profile" in sl_header)
    check("smartlead_base_matches_constant", sl_header[: len(SMARTLEAD_BASE_HEADERS)] == SMARTLEAD_BASE_HEADERS)

    again = export_sequences(TENANT_ID, "instantly", log_events=False)
    check("deterministic_csv", again.csv_text == result.csv_text)

    export_sequences(TENANT_ID, "instantly", log_events=True)
    events = [e for e in store.list_events(TENANT_ID) if e["event_type"] == "exported"]
    check("one_exported_event", len(events) == 1, f"events={len(events)}")
    if events:
        check("event_detail_is_format", events[0]["detail"] == "instantly", events[0]["detail"])
        check("event_angle_matches", events[0]["angle"] == (prospect["angle"] or ""), events[0]["angle"])

    cli = subprocess.run(
        [sys.executable, str(ROOT / "scripts" / "export_sequences.py"), "--tenant", "demo", "--dry-run"],
        capture_output=True,
        text=True,
        env={**os.environ},
        cwd=ROOT,
    )
    check("cli_dry_run_exit_0", cli.returncode == 0, cli.stderr[-300:])
    check("cli_dry_run_lists_export", "Export Pass Co" in cli.stdout, cli.stdout[-300:])

    print()
    if failures:
        print(f"{len(failures)} export check(s) FAILED: {', '.join(failures)}")
        return 1
    print("All export checks passed.")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    finally:
        shutil.rmtree(_SCRATCH, ignore_errors=True)
