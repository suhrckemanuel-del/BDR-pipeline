"""
pipedrive_sync.py — Pipedrive CRM connector (sibling of hubspot_sync.py).

Pushes the finalised prospect into Pipedrive via the v1 REST API using an API
token. Schema-agnostic, same philosophy as the other connectors: only default
objects and fields —

  Organization — name
  Person       — name, email (linked via org_id)

Everything else (ICP tier, account score, angle, drafts, the full sequence)
goes into a Note attached to the organization (and person, when one exists) —
free text, no custom fields to provision.

Tokens never live in config files: crm.pipedrive_token_env may name an env
var holding a per-tenant API token, falling back to PIPEDRIVE_API_TOKEN.
Missing token -> skip with a clear message, never crash. BDR_CRM_DRY_RUN=1
builds everything with zero HTTP. All HTTP goes through the single _api choke
point (token as ?api_token= query param, the Pipedrive convention) so
check_crm_sync.py can mock the wire.
"""
from __future__ import annotations

import os
from urllib.parse import quote

import requests

from app.agents.state import BDRState, CRMSyncResult
from app.services.hubspot_sync import build_note_body

PIPEDRIVE_BASE = "https://api.pipedrive.com/v1"


class PipedriveAPIError(RuntimeError):
    """Non-2xx response from the Pipedrive API. Carries status code + body."""

    def __init__(self, status_code: int, text: str):
        super().__init__(f"HTTP {status_code}: {text[:300]}")
        self.status_code = status_code
        self.text = text


def _resolve_token(tenant) -> str:
    """crm.pipedrive_token_env names an env var; fallback PIPEDRIVE_API_TOKEN."""
    if tenant is not None and getattr(tenant.crm, "pipedrive_token_env", None):
        token = os.environ.get(tenant.crm.pipedrive_token_env, "")
        if token:
            return token
    return os.environ.get("PIPEDRIVE_API_TOKEN", "")


def _api(method: str, path: str, token: str, payload: dict | None = None) -> dict:
    """Single HTTP choke point for every Pipedrive call (mocked in offline checks)."""
    sep = "&" if "?" in path else "?"
    response = requests.request(
        method,
        f"{PIPEDRIVE_BASE}{path}{sep}api_token={token}",
        json=payload,
        timeout=15,
    )
    if not 200 <= response.status_code < 300:
        raise PipedriveAPIError(response.status_code, response.text)
    return response.json() if response.text else {}


def _first_item_id(data: dict) -> str:
    items = ((data.get("data") or {}).get("items")) or []
    if not items:
        return ""
    item = (items[0] or {}).get("item") or {}
    return str(item.get("id", "")) if item.get("id") is not None else ""


def _find_organization(token: str, company: str) -> str:
    data = _api(
        "GET", f"/organizations/search?term={quote(company)}&exact_match=true&limit=1", token
    )
    return _first_item_id(data)


def _create_organization(token: str, company: str) -> str:
    data = _api("POST", "/organizations", token, {"name": company})
    org = data.get("data") or {}
    return str(org.get("id", "")) if org.get("id") is not None else ""


def _find_or_create_person(token: str, contact, org_id: str) -> str:
    """Find a person by email, creating it if absent. "" when contact has no email."""
    if contact is None or not contact.email:
        return ""
    data = _api(
        "GET",
        f"/persons/search?term={quote(contact.email)}&fields=email&exact_match=true&limit=1",
        token,
    )
    person_id = _first_item_id(data)
    if person_id:
        return person_id

    payload = {
        "name": contact.name or contact.email,
        "email": [{"value": contact.email, "primary": True}],
    }
    if org_id:
        payload["org_id"] = int(org_id)
    data = _api("POST", "/persons", token, payload)
    person = data.get("data") or {}
    return str(person.get("id", "")) if person.get("id") is not None else ""


def _create_note(token: str, body: str, org_id: str, person_id: str = "") -> str:
    payload: dict = {"content": body, "org_id": int(org_id)}
    if person_id:
        payload["person_id"] = int(person_id)
    data = _api("POST", "/notes", token, payload)
    note = data.get("data") or {}
    return str(note.get("id", "")) if note.get("id") is not None else ""


def push_to_pipedrive(state: BDRState, *, dry_run: bool = False) -> CRMSyncResult:
    """Push the finalised prospect to Pipedrive. Returns a CRMSyncResult; never raises.

    Reads the generic sync toggle from state["sync_to_notion"] (the key
    predates multi-provider support). Skips when the toggle is off or no token
    is configured. dry_run=True (or BDR_CRM_DRY_RUN=1) builds everything with
    zero HTTP.
    """
    if not state.get("sync_to_notion", False):
        return CRMSyncResult(
            success=False, skipped=True, skip_reason="Sync disabled in UI.", provider="pipedrive"
        )

    dry_run = dry_run or os.environ.get("BDR_CRM_DRY_RUN") == "1"

    token = _resolve_token(state.get("tenant"))
    if not token and not dry_run:
        return CRMSyncResult(
            success=False,
            skipped=True,
            skip_reason="PIPEDRIVE_API_TOKEN missing in .env (or tenant crm.pipedrive_token_env unset).",
            provider="pipedrive",
        )

    enrichment = state.get("enrichment")
    company = (enrichment.company if enrichment else state.get("company", "")) or "Unknown"
    top_contact = enrichment.contacts[0] if enrichment and enrichment.contacts else None

    note_body = build_note_body(state)

    if dry_run:
        return CRMSyncResult(success=True, page_id="dry-run", provider="pipedrive", dry_run=True)

    try:
        org_id = _find_organization(token, company) or _create_organization(token, company)
    except Exception as exc:
        return CRMSyncResult(
            success=False, error=f"Pipedrive organization upsert failed: {exc}", provider="pipedrive"
        )

    person_id = ""
    try:
        if top_contact is not None:
            person_id = _find_or_create_person(token, top_contact, org_id)
    except Exception as exc:
        return CRMSyncResult(
            success=False, error=f"Pipedrive person upsert failed: {exc}", provider="pipedrive"
        )

    try:
        _create_note(token, note_body, org_id, person_id)
    except Exception as exc:
        return CRMSyncResult(
            success=False, error=f"Pipedrive note create failed: {exc}", provider="pipedrive"
        )

    return CRMSyncResult(success=True, page_id=org_id, page_url="", provider="pipedrive")
