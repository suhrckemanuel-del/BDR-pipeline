"""
salesforce_sync.py — Salesforce CRM connector (sibling of hubspot_sync.py).

Pushes the finalised prospect into Salesforce via the standard REST API using
an access token + instance URL. Schema-agnostic, same philosophy as the other
connectors: only standard objects and fields that exist in every org —

  Account — Name, Website
  Contact — FirstName, LastName, Email, Title (linked via AccountId)

Everything else (ICP tier, account score, angle, drafts, the full sequence)
goes into a Note attached to the Account — free text, no custom fields to
provision.

Auth never lives in config files: crm.salesforce_token_env may name an env
var holding a per-tenant access token, falling back to
SALESFORCE_ACCESS_TOKEN. The instance URL is not a secret, so it may live in
config (crm.salesforce_instance_url) or in SALESFORCE_INSTANCE_URL. Missing
either -> skip with a clear message, never crash. BDR_CRM_DRY_RUN=1 builds
everything with zero HTTP. All HTTP goes through the single _api choke point
so check_crm_sync.py can mock the wire.
"""
from __future__ import annotations

import os
from urllib.parse import quote

import requests

from app.agents.state import BDRState, CRMSyncResult
from app.services.hubspot_sync import build_note_body

API_VERSION = "v59.0"

# Salesforce caps Note.Body at 32,000 characters — stay safely under.
NOTE_BODY_MAX_CHARS = 30_000


class SalesforceAPIError(RuntimeError):
    """Non-2xx response from the Salesforce API. Carries status code + body."""

    def __init__(self, status_code: int, text: str):
        super().__init__(f"HTTP {status_code}: {text[:300]}")
        self.status_code = status_code
        self.text = text


def _resolve_token(tenant) -> str:
    """crm.salesforce_token_env names an env var; fallback SALESFORCE_ACCESS_TOKEN."""
    if tenant is not None and getattr(tenant.crm, "salesforce_token_env", None):
        token = os.environ.get(tenant.crm.salesforce_token_env, "")
        if token:
            return token
    return os.environ.get("SALESFORCE_ACCESS_TOKEN", "")


def _resolve_instance_url(tenant) -> str:
    """crm.salesforce_instance_url (config) or SALESFORCE_INSTANCE_URL (env)."""
    if tenant is not None and getattr(tenant.crm, "salesforce_instance_url", None):
        return tenant.crm.salesforce_instance_url.rstrip("/")
    return os.environ.get("SALESFORCE_INSTANCE_URL", "").rstrip("/")


def _api(method: str, path: str, token: str, instance_url: str, payload: dict | None = None) -> dict:
    """Single HTTP choke point for every Salesforce call (mocked in offline checks)."""
    response = requests.request(
        method,
        instance_url + path,
        headers={"Authorization": f"Bearer {token}"},
        json=payload,
        timeout=15,
    )
    if not 200 <= response.status_code < 300:
        raise SalesforceAPIError(response.status_code, response.text)
    return response.json() if response.text else {}


def _soql(query: str) -> str:
    return f"/services/data/{API_VERSION}/query?q={quote(query)}"


def _escape(value: str) -> str:
    return (value or "").replace("\\", "\\\\").replace("'", "\\'")


def _find_account(token: str, instance_url: str, company: str, domain: str) -> str:
    """Find an existing Account (by website domain when known, else exact name)."""
    if domain:
        query = f"SELECT Id FROM Account WHERE Website LIKE '%{_escape(domain)}%' LIMIT 1"
    else:
        query = f"SELECT Id FROM Account WHERE Name = '{_escape(company)}' LIMIT 1"
    data = _api("GET", _soql(query), token, instance_url)
    records = data.get("records") or []
    return str(records[0].get("Id", "")) if records else ""


def _create_account(token: str, instance_url: str, company: str, domain: str) -> str:
    payload: dict = {"Name": company}
    if domain:
        payload["Website"] = domain
    data = _api(
        "POST", f"/services/data/{API_VERSION}/sobjects/Account", token, instance_url, payload
    )
    return str(data.get("id", ""))


def _find_or_create_contact(token: str, instance_url: str, contact, account_id: str) -> str:
    """Find a Contact by email, creating it if absent. "" when contact has no email."""
    if contact is None or not contact.email:
        return ""
    data = _api(
        "GET",
        _soql(f"SELECT Id FROM Contact WHERE Email = '{_escape(contact.email)}' LIMIT 1"),
        token,
        instance_url,
    )
    records = data.get("records") or []
    if records:
        return str(records[0].get("Id", ""))

    name_parts = (contact.name or "").split()
    payload = {
        "FirstName": name_parts[0] if name_parts else "",
        "LastName": " ".join(name_parts[1:]) or (name_parts[0] if name_parts else "Unknown"),
        "Email": contact.email,
        "Title": contact.position or "",
        "AccountId": account_id,
    }
    data = _api(
        "POST", f"/services/data/{API_VERSION}/sobjects/Contact", token, instance_url, payload
    )
    return str(data.get("id", ""))


def _create_note(token: str, instance_url: str, company: str, body: str, account_id: str) -> str:
    data = _api(
        "POST",
        f"/services/data/{API_VERSION}/sobjects/Note",
        token,
        instance_url,
        {
            "ParentId": account_id,
            "Title": f"BDR outreach — {company}"[:80],
            "Body": body,
        },
    )
    return str(data.get("id", ""))


def push_to_salesforce(state: BDRState, *, dry_run: bool = False) -> CRMSyncResult:
    """Push the finalised prospect to Salesforce. Returns a CRMSyncResult; never raises.

    Reads the generic sync toggle from state["sync_to_notion"] (the key
    predates multi-provider support). Skips when the toggle is off or auth is
    missing. dry_run=True (or BDR_CRM_DRY_RUN=1) builds everything, zero HTTP.
    """
    if not state.get("sync_to_notion", False):
        return CRMSyncResult(
            success=False, skipped=True, skip_reason="Sync disabled in UI.", provider="salesforce"
        )

    dry_run = dry_run or os.environ.get("BDR_CRM_DRY_RUN") == "1"

    tenant = state.get("tenant")
    token = _resolve_token(tenant)
    instance_url = _resolve_instance_url(tenant)
    if (not token or not instance_url) and not dry_run:
        return CRMSyncResult(
            success=False,
            skipped=True,
            skip_reason=(
                "SALESFORCE_ACCESS_TOKEN or instance URL missing "
                "(crm.salesforce_token_env / crm.salesforce_instance_url or "
                "SALESFORCE_INSTANCE_URL)."
            ),
            provider="salesforce",
        )

    enrichment = state.get("enrichment")
    company = (enrichment.company if enrichment else state.get("company", "")) or "Unknown"
    domain = enrichment.domain if enrichment else ""
    top_contact = enrichment.contacts[0] if enrichment and enrichment.contacts else None

    note_body = build_note_body(state, max_chars=NOTE_BODY_MAX_CHARS)

    if dry_run:
        return CRMSyncResult(success=True, page_id="dry-run", provider="salesforce", dry_run=True)

    try:
        account_id = _find_account(token, instance_url, company, domain) or _create_account(
            token, instance_url, company, domain
        )
    except Exception as exc:
        return CRMSyncResult(
            success=False, error=f"Salesforce account upsert failed: {exc}", provider="salesforce"
        )

    try:
        if top_contact is not None:
            _find_or_create_contact(token, instance_url, top_contact, account_id)
    except Exception as exc:
        return CRMSyncResult(
            success=False, error=f"Salesforce contact upsert failed: {exc}", provider="salesforce"
        )

    try:
        _create_note(token, instance_url, company, note_body, account_id)
    except Exception as exc:
        return CRMSyncResult(
            success=False, error=f"Salesforce note create failed: {exc}", provider="salesforce"
        )

    return CRMSyncResult(success=True, page_id=account_id, page_url="", provider="salesforce")
