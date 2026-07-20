"""
Offline verification for the CRM layer: provider dispatch + HubSpot connector.

No network, no API keys: every HubSpot HTTP call goes through the single
hubspot_sync._api choke point, which this script replaces with an in-memory
recorder returning canned responses. Prints one line per check and exits 0
when everything passes, 1 otherwise — CI-able like scripts/run_eval.py.

    python scripts/check_crm_sync.py
"""
from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

# Nothing here should write the SQLite store, but if anything does, keep it
# out of the repo's pipeline/bdr.db.
os.environ.setdefault("BDR_DB_PATH", str(Path(tempfile.gettempdir()) / "bdr-check-crm-sync.db"))

from pydantic import ValidationError  # noqa: E402

from app.agents.state import CRMSyncResult  # noqa: E402
from app.services import crm_sync, hubspot_sync, pipedrive_sync, salesforce_sync  # noqa: E402
from app.services.demo_eval import build_sample_state  # noqa: E402
from app.tenants import load_tenant  # noqa: E402
from app.tenants.schema import CRMConfig  # noqa: E402

FAILURES: list[str] = []


def check(name: str, fn) -> None:
    try:
        fn()
    except Exception as exc:
        FAILURES.append(name)
        print(f"  FAIL  {name} — {type(exc).__name__}: {exc}")
    else:
        print(f"  ok    {name}")


class APIRecorder:
    """Stands in for hubspot_sync._api — records calls, returns canned dicts."""

    def __init__(self, responses: dict[str, dict]):
        self.calls: list[tuple[str, str, dict | None]] = []
        self.responses = dict(responses)

    def __call__(self, method: str, path: str, token: str, payload: dict | None = None) -> dict:
        self.calls.append((method, path, payload))
        if path not in self.responses:
            raise AssertionError(f"unexpected HubSpot API path: {path}")
        return self.responses[path]

    @property
    def paths(self) -> list[str]:
        return [path for _, path, _ in self.calls]


def _sample_state() -> dict:
    """Full offline fixture state with a contactable lead and sync toggled on."""
    tenant = load_tenant("demo")
    state = build_sample_state(
        tenant,
        {"company": "Check Co", "industry": "B2B SaaS", "domain": "check.co"},
        index=3,  # index % 3 == 0 -> the fixture includes a contact
    )
    assert state["enrichment"].contacts, "index-3 fixture should include a contact"
    state["enrichment"].contacts[0].email = "lead@check.co"
    state["sync_to_notion"] = True
    return state


# ---------------------------------------------------------------------------
# Checks
# ---------------------------------------------------------------------------
def check_schema_compat() -> None:
    assert CRMConfig().provider == "notion", "default provider must stay notion"
    CRMConfig(enabled=True, notion_database_id="x")  # pre-provider configs still validate
    CRMConfig(provider="hubspot", hubspot_token_env="ACME_HS")
    CRMConfig(provider="salesforce", salesforce_token_env="ACME_SF", salesforce_instance_url="https://acme.my.salesforce.com")
    CRMConfig(provider="pipedrive", pipedrive_token_env="ACME_PD")
    try:
        CRMConfig(provider="zoho")
    except ValidationError:
        pass
    else:
        raise AssertionError("provider='zoho' should be rejected")
    tenant = load_tenant("demo")
    assert tenant.crm.provider == "notion", "demo tenant (no provider key) must default to notion"


def check_skip_paths() -> None:
    result = hubspot_sync.push_to_hubspot({"sync_to_notion": False})
    assert result.skipped and not result.success, "toggle-off must skip"
    assert result.skip_reason == "Sync disabled in UI."
    assert result.provider == "hubspot"

    os.environ.pop("HUBSPOT_ACCESS_TOKEN", None)
    result = hubspot_sync.push_to_hubspot({"sync_to_notion": True, "company": "X"})
    assert result.skipped and not result.success, "missing token must skip, not error"
    assert "HUBSPOT_ACCESS_TOKEN" in result.skip_reason


def check_dry_run_zero_http() -> None:
    def _boom(method, path, token, payload=None):
        raise AssertionError(f"HTTP call attempted during dry run: {method} {path}")

    original = hubspot_sync._api
    hubspot_sync._api = _boom
    try:
        os.environ["HUBSPOT_ACCESS_TOKEN"] = "fake-token-for-check"
        state = _sample_state()

        result = hubspot_sync.push_to_hubspot(state, dry_run=True)
        assert result.success and result.dry_run and result.page_id == "dry-run"
        assert result.provider == "hubspot"

        os.environ["BDR_CRM_DRY_RUN"] = "1"
        result = hubspot_sync.push_to_hubspot(state)
        assert result.success and result.dry_run and result.page_id == "dry-run"
    finally:
        hubspot_sync._api = original
        os.environ.pop("BDR_CRM_DRY_RUN", None)


def check_full_mocked_push() -> None:
    recorder = APIRecorder(
        {
            "/crm/v3/objects/companies/search": {"results": []},
            "/crm/v3/objects/companies": {"id": "C1"},
            "/crm/v3/objects/contacts/search": {"results": []},
            "/crm/v3/objects/contacts": {"id": "P1"},
            "/crm/v3/objects/notes": {"id": "N1"},
        }
    )
    original = hubspot_sync._api
    hubspot_sync._api = recorder
    try:
        os.environ["HUBSPOT_ACCESS_TOKEN"] = "fake-token-for-check"
        state = _sample_state()
        result = hubspot_sync.push_to_hubspot(state)
    finally:
        hubspot_sync._api = original

    assert result.success and result.provider == "hubspot", f"push failed: {result.error}"
    assert result.page_id == "C1"
    assert recorder.paths == [
        "/crm/v3/objects/companies/search",
        "/crm/v3/objects/companies",
        "/crm/v3/objects/contacts/search",
        "/crm/v3/objects/contacts",
        "/crm/v3/objects/notes",
    ], f"unexpected call order: {recorder.paths}"

    note_payload = recorder.calls[-1][2] or {}
    body = note_payload["properties"]["hs_note_body"]
    tenant = load_tenant("demo")
    assert "Check Co" in body, "note body must mention the company"
    assert tenant.angle_by_key("angle1").name in body, "note body must name the recommended angle"
    assert "Check Co forecast review" in body, "note body must include the email subject"

    assoc_pairs = {
        (assoc["types"][0]["associationTypeId"], assoc["to"]["id"])
        for assoc in note_payload["associations"]
    }
    assert (hubspot_sync.ASSOC_NOTE_TO_COMPANY, "C1") in assoc_pairs, assoc_pairs
    assert (hubspot_sync.ASSOC_NOTE_TO_CONTACT, "P1") in assoc_pairs, assoc_pairs


def check_existing_company_not_recreated() -> None:
    recorder = APIRecorder(
        {
            "/crm/v3/objects/companies/search": {"results": [{"id": "C9"}]},
            "/crm/v3/objects/contacts/search": {"results": [{"id": "P9"}]},
            "/crm/v3/objects/notes": {"id": "N2"},
        }
    )
    original = hubspot_sync._api
    hubspot_sync._api = recorder
    try:
        os.environ["HUBSPOT_ACCESS_TOKEN"] = "fake-token-for-check"
        result = hubspot_sync.push_to_hubspot(_sample_state())
    finally:
        hubspot_sync._api = original

    assert result.success and result.page_id == "C9"
    assert "/crm/v3/objects/companies" not in recorder.paths, "must not create an existing company"
    assert "/crm/v3/objects/contacts" not in recorder.paths, "must not create an existing contact"


def check_api_error_becomes_result() -> None:
    def _fail(method, path, token, payload=None):
        raise hubspot_sync.HubSpotAPIError(500, "internal boom")

    original = hubspot_sync._api
    hubspot_sync._api = _fail
    try:
        os.environ["HUBSPOT_ACCESS_TOKEN"] = "fake-token-for-check"
        result = hubspot_sync.push_to_hubspot(_sample_state())
    finally:
        hubspot_sync._api = original

    assert not result.success and not result.skipped
    assert result.error.startswith("HubSpot"), result.error
    assert result.provider == "hubspot"


def check_salesforce_connector() -> None:
    # Skip paths: toggle off, then missing token/instance URL.
    result = salesforce_sync.push_to_salesforce({"sync_to_notion": False})
    assert result.skipped and result.provider == "salesforce"
    for key in ("SALESFORCE_ACCESS_TOKEN", "SALESFORCE_INSTANCE_URL"):
        os.environ.pop(key, None)
    result = salesforce_sync.push_to_salesforce({"sync_to_notion": True, "company": "X"})
    assert result.skipped and "SALESFORCE_ACCESS_TOKEN" in result.skip_reason

    # Dry run: everything built, zero HTTP.
    def _boom(method, path, token, instance_url, payload=None):
        raise AssertionError(f"HTTP call attempted during dry run: {method} {path}")

    original = salesforce_sync._api
    salesforce_sync._api = _boom
    try:
        result = salesforce_sync.push_to_salesforce(_sample_state(), dry_run=True)
        assert result.success and result.dry_run and result.page_id == "dry-run"
    finally:
        salesforce_sync._api = original

    # Full mocked push: account search -> create, contact search -> create, note.
    class SFRecorder:
        def __init__(self):
            self.calls: list[tuple[str, str, dict | None]] = []

        def __call__(self, method, path, token, instance_url, payload=None):
            self.calls.append((method, path, payload))
            if path.startswith(f"/services/data/{salesforce_sync.API_VERSION}/query"):
                return {"records": []}
            if path.endswith("/sobjects/Account"):
                return {"id": "A1"}
            if path.endswith("/sobjects/Contact"):
                return {"id": "C1"}
            if path.endswith("/sobjects/Note"):
                return {"id": "N1"}
            raise AssertionError(f"unexpected Salesforce API path: {path}")

    recorder = SFRecorder()
    salesforce_sync._api = recorder
    try:
        os.environ["SALESFORCE_ACCESS_TOKEN"] = "fake-token-for-check"
        os.environ["SALESFORCE_INSTANCE_URL"] = "https://check.my.salesforce.com"
        result = salesforce_sync.push_to_salesforce(_sample_state())
    finally:
        salesforce_sync._api = original
        os.environ.pop("SALESFORCE_ACCESS_TOKEN", None)
        os.environ.pop("SALESFORCE_INSTANCE_URL", None)

    assert result.success and result.page_id == "A1", f"push failed: {result.error}"
    kinds = [path.split("/")[-1].split("?")[0] for _, path, _ in recorder.calls]
    assert kinds == ["query", "Account", "query", "Contact", "Note"], kinds
    note_payload = recorder.calls[-1][2] or {}
    assert note_payload["ParentId"] == "A1"
    assert "Check Co" in note_payload["Body"], "note body must mention the company"

    # API error surfaces as a failed result, not an exception.
    def _fail(method, path, token, instance_url, payload=None):
        raise salesforce_sync.SalesforceAPIError(500, "internal boom")

    salesforce_sync._api = _fail
    try:
        os.environ["SALESFORCE_ACCESS_TOKEN"] = "fake-token-for-check"
        os.environ["SALESFORCE_INSTANCE_URL"] = "https://check.my.salesforce.com"
        result = salesforce_sync.push_to_salesforce(_sample_state())
    finally:
        salesforce_sync._api = original
        os.environ.pop("SALESFORCE_ACCESS_TOKEN", None)
        os.environ.pop("SALESFORCE_INSTANCE_URL", None)
    assert not result.success and not result.skipped
    assert result.error.startswith("Salesforce"), result.error


def check_pipedrive_connector() -> None:
    result = pipedrive_sync.push_to_pipedrive({"sync_to_notion": False})
    assert result.skipped and result.provider == "pipedrive"
    os.environ.pop("PIPEDRIVE_API_TOKEN", None)
    result = pipedrive_sync.push_to_pipedrive({"sync_to_notion": True, "company": "X"})
    assert result.skipped and "PIPEDRIVE_API_TOKEN" in result.skip_reason

    def _boom(method, path, token, payload=None):
        raise AssertionError(f"HTTP call attempted during dry run: {method} {path}")

    original = pipedrive_sync._api
    pipedrive_sync._api = _boom
    try:
        result = pipedrive_sync.push_to_pipedrive(_sample_state(), dry_run=True)
        assert result.success and result.dry_run and result.page_id == "dry-run"
    finally:
        pipedrive_sync._api = original

    class PDRecorder:
        def __init__(self):
            self.calls: list[tuple[str, str, dict | None]] = []

        def __call__(self, method, path, token, payload=None):
            self.calls.append((method, path, payload))
            if path.startswith("/organizations/search") or path.startswith("/persons/search"):
                return {"data": {"items": []}}
            if path == "/organizations":
                return {"data": {"id": 11}}
            if path == "/persons":
                return {"data": {"id": 22}}
            if path == "/notes":
                return {"data": {"id": 33}}
            raise AssertionError(f"unexpected Pipedrive API path: {path}")

    recorder = PDRecorder()
    pipedrive_sync._api = recorder
    try:
        os.environ["PIPEDRIVE_API_TOKEN"] = "fake-token-for-check"
        result = pipedrive_sync.push_to_pipedrive(_sample_state())
    finally:
        pipedrive_sync._api = original
        os.environ.pop("PIPEDRIVE_API_TOKEN", None)

    assert result.success and result.page_id == "11", f"push failed: {result.error}"
    kinds = [path.split("?")[0] for _, path, _ in recorder.calls]
    assert kinds == [
        "/organizations/search", "/organizations", "/persons/search", "/persons", "/notes"
    ], kinds
    note_payload = recorder.calls[-1][2] or {}
    assert note_payload["org_id"] == 11 and note_payload["person_id"] == 22
    assert "Check Co" in note_payload["content"], "note content must mention the company"

    def _fail(method, path, token, payload=None):
        raise pipedrive_sync.PipedriveAPIError(500, "internal boom")

    pipedrive_sync._api = _fail
    try:
        os.environ["PIPEDRIVE_API_TOKEN"] = "fake-token-for-check"
        result = pipedrive_sync.push_to_pipedrive(_sample_state())
    finally:
        pipedrive_sync._api = original
        os.environ.pop("PIPEDRIVE_API_TOKEN", None)
    assert not result.success and not result.skipped
    assert result.error.startswith("Pipedrive"), result.error


def check_dispatcher_routing() -> None:
    calls: list[str] = []

    def fake_notion(state):
        calls.append("notion")
        return CRMSyncResult(success=True, provider="notion")

    def fake_hubspot(state, *, dry_run=False):
        calls.append("hubspot")
        return CRMSyncResult(success=True, provider="hubspot")

    def fake_salesforce(state, *, dry_run=False):
        calls.append("salesforce")
        return CRMSyncResult(success=True, provider="salesforce")

    def fake_pipedrive(state, *, dry_run=False):
        calls.append("pipedrive")
        return CRMSyncResult(success=True, provider="pipedrive")

    original_notion = crm_sync.push_to_notion
    original_hubspot = hubspot_sync.push_to_hubspot
    original_salesforce = salesforce_sync.push_to_salesforce
    original_pipedrive = pipedrive_sync.push_to_pipedrive
    crm_sync.push_to_notion = fake_notion
    hubspot_sync.push_to_hubspot = fake_hubspot
    salesforce_sync.push_to_salesforce = fake_salesforce
    pipedrive_sync.push_to_pipedrive = fake_pipedrive
    try:
        tenant = load_tenant("demo")
        for provider in ("hubspot", "salesforce", "pipedrive", "notion"):
            routed = tenant.model_copy(update={"crm": CRMConfig(provider=provider)})
            out = crm_sync.run_crm_sync({"tenant": routed, "sync_to_notion": True})
            assert out["crm_result"].provider == provider, f"{provider} misrouted"
        assert calls == ["hubspot", "salesforce", "pipedrive", "notion"], calls

        routed = tenant.model_copy(update={"crm": CRMConfig(provider="none")})
        out = crm_sync.run_crm_sync({"tenant": routed, "sync_to_notion": True})
        assert out["crm_result"].skipped and out["crm_result"].provider == "none"
        assert calls == ["hubspot", "salesforce", "pipedrive", "notion"], "provider=none must call no connector"

        assert crm_sync.run_crm_sync({"error": "x"}) == {}, "errored state must return {}"
    finally:
        crm_sync.push_to_notion = original_notion
        hubspot_sync.push_to_hubspot = original_hubspot
        salesforce_sync.push_to_salesforce = original_salesforce
        pipedrive_sync.push_to_pipedrive = original_pipedrive


def main() -> int:
    saved_env = {
        key: os.environ.get(key)
        for key in (
            "HUBSPOT_ACCESS_TOKEN",
            "SALESFORCE_ACCESS_TOKEN",
            "SALESFORCE_INSTANCE_URL",
            "PIPEDRIVE_API_TOKEN",
            "BDR_CRM_DRY_RUN",
        )
    }
    os.environ.pop("BDR_CRM_DRY_RUN", None)
    try:
        print("CRM sync offline checks:")
        check("schema compat (CRMConfig provider fields + demo tenant default)", check_schema_compat)
        check("skip paths never raise (toggle off / missing token)", check_skip_paths)
        check("dry run makes zero HTTP calls", check_dry_run_zero_http)
        check("full mocked push (call order, note body, associations)", check_full_mocked_push)
        check("existing company/contact are not recreated", check_existing_company_not_recreated)
        check("API errors surface as failed results, not exceptions", check_api_error_becomes_result)
        check("salesforce connector (skips, dry run, call order, errors)", check_salesforce_connector)
        check("pipedrive connector (skips, dry run, call order, errors)", check_pipedrive_connector)
        check("run_crm_sync dispatches by tenant.crm.provider", check_dispatcher_routing)
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
    print("All CRM sync checks passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
