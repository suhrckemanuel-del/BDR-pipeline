"""
Offline verification for the live sending-tool push path (sequence_push.py).

No network, no API keys: both per-tool HTTP choke points
(sequence_push._instantly_api / _smartlead_api) are replaced with in-memory
recorders returning canned responses. Prospects are seeded into a scratch
SQLite store (BDR_DB_PATH points at a temp file). Prints one line per check
and exits 0 when everything passes, 1 otherwise.

    python scripts/check_push.py
"""
from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

_SCRATCH = tempfile.mkdtemp(prefix="bdr-check-push-")
os.environ["BDR_DB_PATH"] = str(Path(_SCRATCH) / "check_push.db")

from pydantic import ValidationError  # noqa: E402

from app.services import sequence_push, store  # noqa: E402
from app.services.demo_eval import build_sample_state  # noqa: E402
from app.services.sequence_export import collect_export_rows  # noqa: E402
from app.tenants import load_tenant  # noqa: E402
from app.tenants.schema import OutreachToolsConfig  # noqa: E402

TENANT_ID = "demo"
FAILURES: list[str] = []

KEY_ENVS = ("INSTANTLY_API_KEY", "SMARTLEAD_API_KEY", "ACME_INSTANTLY_KEY", "BDR_PUSH_DRY_RUN")


def check(name: str, fn) -> None:
    try:
        fn()
    except Exception as exc:
        FAILURES.append(name)
        print(f"  FAIL  {name} — {type(exc).__name__}: {exc}")
    else:
        print(f"  ok    {name}")


class APIRecorder:
    """Stands in for a sequence_push _api choke point — records, returns {}."""

    def __init__(self):
        self.calls: list[tuple[str, str, dict | None]] = []

    def __call__(self, method: str, path: str, api_key: str, payload: dict | None = None) -> dict:
        self.calls.append((method, path, payload))
        return {}

    @property
    def paths(self) -> list[str]:
        return [path for _, path, _ in self.calls]


def _boom(method, path, api_key, payload=None):
    raise AssertionError(f"HTTP call attempted: {method} {path}")


def _seed_prospect(tenant, company: str, *, index: int, eval_passed: bool, status: str, email: str = "") -> None:
    state = build_sample_state(tenant, {"company": company, "industry": "B2B SaaS", "domain": "check.co"}, index)
    if email and state["enrichment"].contacts:
        state["enrichment"].contacts[0].email = email
    run_id = store.record_run(state, source="check", mode="sample", eval_passed=eval_passed)
    store.upsert_prospect_from_state(state, run_id, eval_passed=eval_passed)
    if status != "researched":
        store.set_prospect_status(TENANT_ID, company, status)


def _tenant_with(tenant, **outreach_kwargs):
    return tenant.model_copy(update={"outreach": OutreachToolsConfig(**outreach_kwargs)})


TENANT = load_tenant(TENANT_ID)
# index=3/6 fixtures include a contact; index=1 has none (email-less skip);
# eval_passed=False exercises the eval-gate skip path.
_seed_prospect(TENANT, "Push Pass Co", index=3, eval_passed=True, status="queued", email="p1@pass.co")
_seed_prospect(TENANT, "Push Second Co", index=6, eval_passed=True, status="queued", email="p2@pass.co")
_seed_prospect(TENANT, "Eval Fail Co", index=9, eval_passed=False, status="queued", email="p3@fail.co")
_seed_prospect(TENANT, "Still Researched Co", index=12, eval_passed=True, status="researched", email="p4@wait.co")


# ---------------------------------------------------------------------------
# Checks
# ---------------------------------------------------------------------------
def check_schema_compat() -> None:
    cfg = OutreachToolsConfig()
    assert cfg.instantly_campaign_id is None and cfg.smartlead_campaign_id is None
    assert cfg.instantly_api_key_env is None and cfg.smartlead_api_key_env is None
    try:
        OutreachToolsConfig(instantly_campain_id="typo")
    except ValidationError:
        pass
    else:
        raise AssertionError("extra key must be rejected (extra='forbid')")
    assert TENANT.outreach.instantly_campaign_id is None, "demo tenant must have no campaign wiring"


def check_no_campaign_id_skips() -> None:
    originals = (sequence_push._instantly_api, sequence_push._smartlead_api)
    sequence_push._instantly_api = sequence_push._smartlead_api = _boom
    try:
        result = sequence_push.push_sequences(TENANT, "instantly")
    finally:
        sequence_push._instantly_api, sequence_push._smartlead_api = originals
    assert result.skip_reason and "outreach.instantly_campaign_id" in result.skip_reason
    assert not result.pushed and not result.payloads
    assert not any(e["event_type"] == "pushed" for e in store.list_events(TENANT_ID)), "no events on skip"


def check_no_api_key_skips() -> None:
    tenant = _tenant_with(TENANT, instantly_campaign_id="CAMP-1")
    originals = (sequence_push._instantly_api, sequence_push._smartlead_api)
    sequence_push._instantly_api = sequence_push._smartlead_api = _boom
    try:
        result = sequence_push.push_sequences(tenant, "instantly")
    finally:
        sequence_push._instantly_api, sequence_push._smartlead_api = originals
    assert result.skip_reason and "INSTANTLY_API_KEY" in result.skip_reason, result.skip_reason
    assert "instantly_api_key_env" in result.skip_reason


def check_dry_run_zero_http() -> None:
    tenant = _tenant_with(TENANT, instantly_campaign_id="CAMP-1")
    originals = (sequence_push._instantly_api, sequence_push._smartlead_api)
    sequence_push._instantly_api = sequence_push._smartlead_api = _boom
    os.environ["BDR_PUSH_DRY_RUN"] = "1"
    try:
        result = sequence_push.push_sequences(tenant, "instantly")
    finally:
        sequence_push._instantly_api, sequence_push._smartlead_api = originals
        os.environ.pop("BDR_PUSH_DRY_RUN", None)
    assert result.dry_run and not result.skip_reason
    assert result.pushed == ["Push Pass Co", "Push Second Co"], result.pushed
    assert len(result.payloads) == 2 and result.payloads[0]["email"] == "p1@pass.co"
    assert "subject_1" in result.payloads[0]["custom_variables"], "per-touch copy must ride along"
    assert not any(e["event_type"] == "pushed" for e in store.list_events(TENANT_ID)), "no events in dry-run"


def check_full_mocked_push_instantly() -> None:
    tenant = _tenant_with(TENANT, instantly_campaign_id="CAMP-1")
    recorder = APIRecorder()
    original = sequence_push._instantly_api
    sequence_push._instantly_api = recorder
    os.environ["INSTANTLY_API_KEY"] = "fake-key-for-check"
    try:
        result = sequence_push.push_sequences(tenant, "instantly")
    finally:
        sequence_push._instantly_api = original
        os.environ.pop("INSTANTLY_API_KEY", None)

    assert result.pushed == ["Push Pass Co", "Push Second Co"], result.pushed
    assert recorder.paths == ["/api/v2/leads", "/api/v2/leads"], recorder.paths
    payload = recorder.calls[0][2] or {}
    assert payload["campaign"] == "CAMP-1" and payload["company_name"] == "Push Pass Co"
    assert payload["custom_variables"]["angle"] == "angle1"

    # Eligibility skips come from collect_export_rows verbatim (single source of truth).
    _, expected_skips = collect_export_rows(TENANT_ID)
    assert result.skipped == expected_skips, result.skipped
    skip_reasons = dict(result.skipped)
    assert skip_reasons["Eval Fail Co"] == "latest run failed eval gates"
    assert skip_reasons["Still Researched Co"] == "status is 'researched'"

    events = [e for e in store.list_events(TENANT_ID) if e["event_type"] == "pushed"]
    assert {(e["company"], e["detail"], e["angle"]) for e in events} == {
        ("Push Pass Co", "instantly", "angle1"),
        ("Push Second Co", "instantly", "angle1"),
    }, events


def check_full_mocked_push_smartlead() -> None:
    tenant = _tenant_with(TENANT, smartlead_campaign_id="SL-77")
    recorder = APIRecorder()
    original = sequence_push._smartlead_api
    sequence_push._smartlead_api = recorder
    os.environ["SMARTLEAD_API_KEY"] = "fake-key-for-check"
    try:
        result = sequence_push.push_sequences(tenant, "smartlead", log_events=False)
    finally:
        sequence_push._smartlead_api = original
        os.environ.pop("SMARTLEAD_API_KEY", None)

    assert result.pushed == ["Push Pass Co", "Push Second Co"]
    assert recorder.paths == ["/api/v1/campaigns/SL-77/leads"] * 2, recorder.paths
    payload = recorder.calls[0][2] or {}
    lead = payload["lead_list"][0]
    assert lead["email"] == "p1@pass.co" and "subject_1" in lead["custom_fields"]


def check_api_error_recorded_not_raised() -> None:
    tenant = _tenant_with(TENANT, instantly_campaign_id="CAMP-1")
    attempts: list[str] = []

    def _flaky(method, path, api_key, payload=None):
        attempts.append(payload["company_name"])
        if len(attempts) == 1:
            raise sequence_push.PushAPIError(500, "internal boom")
        return {}

    original = sequence_push._instantly_api
    sequence_push._instantly_api = _flaky
    os.environ["INSTANTLY_API_KEY"] = "fake-key-for-check"
    try:
        result = sequence_push.push_sequences(tenant, "instantly", log_events=False)
    finally:
        sequence_push._instantly_api = original
        os.environ.pop("INSTANTLY_API_KEY", None)

    assert attempts == ["Push Pass Co", "Push Second Co"], "later prospects must still be attempted"
    assert result.errors and result.errors[0][0] == "Push Pass Co"
    assert "500" in result.errors[0][1]
    assert result.pushed == ["Push Second Co"]


def check_api_key_env_indirection() -> None:
    tenant = _tenant_with(
        TENANT, instantly_campaign_id="CAMP-1", instantly_api_key_env="ACME_INSTANTLY_KEY"
    )
    recorder = APIRecorder()
    original = sequence_push._instantly_api
    sequence_push._instantly_api = recorder
    os.environ.pop("INSTANTLY_API_KEY", None)
    os.environ["ACME_INSTANTLY_KEY"] = "tenant-scoped-key"
    try:
        result = sequence_push.push_sequences(tenant, "instantly", log_events=False)
    finally:
        sequence_push._instantly_api = original
        os.environ.pop("ACME_INSTANTLY_KEY", None)
    assert not result.skip_reason and result.pushed, "named env var alone must enable the push"


def main() -> int:
    saved_env = {key: os.environ.get(key) for key in KEY_ENVS}
    for key in KEY_ENVS:
        os.environ.pop(key, None)
    try:
        print("Sequence push offline checks:")
        check("schema compat (OutreachToolsConfig defaults + demo tenant)", check_schema_compat)
        check("missing campaign id skips with zero HTTP", check_no_campaign_id_skips)
        check("missing API key skips with zero HTTP", check_no_api_key_skips)
        check("BDR_PUSH_DRY_RUN builds payloads, zero HTTP, zero events", check_dry_run_zero_http)
        check("full mocked Instantly push (payloads, eligibility, events)", check_full_mocked_push_instantly)
        check("full mocked Smartlead push (per-campaign path, lead_list)", check_full_mocked_push_smartlead)
        check("API errors are recorded, later prospects still attempted", check_api_error_recorded_not_raised)
        check("outreach.*_api_key_env indirection resolves the key", check_api_key_env_indirection)
    finally:
        for key, value in saved_env.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value

    print()
    if FAILURES:
        print(f"{len(FAILURES)} check(s) FAILED: {', '.join(FAILURES)}")
        return 1
    print("All sequence push checks passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
