"""
hubspot_sync.py — HubSpot CRM Connector (sibling of crm_sync.push_to_notion).

Pushes the finalised prospect (strategy + recommended angle + top contact +
drafted sequence) into HubSpot via the v3 CRM REST API using a private-app
access token.

Schema-agnostic, same philosophy as the Notion connector: we don't know which
custom properties a portal defines, so we only touch default properties that
exist in every HubSpot portal:

  companies — name, domain
  contacts  — email, firstname, lastname, jobtitle

Everything else (industry, ICP tier, account score, recommended angle, drafts,
the full 5-touch sequence) goes into a Note associated to the company — and to
the top contact when one has an email. Notes are free text, so no schema match
is required. We deliberately do NOT set the company `industry` property: it is
an enumeration in default portals, so arbitrary values would fail. Industry
lives in the note body instead.

The generic sync toggle in BDRState is still named `sync_to_notion` — it
predates multi-provider support and is threaded through the whole app, so this
connector reads the same key rather than renaming it.
"""
from __future__ import annotations

import os
import time

import requests

from app.agents.state import BDRState, CRMSyncResult

HUBSPOT_BASE = "https://api.hubapi.com"

# HubSpot-defined default association type IDs (v4 associations API).
ASSOC_NOTE_TO_COMPANY = 190
ASSOC_NOTE_TO_CONTACT = 202

# hs_note_body is capped by HubSpot at 65,536 characters — stay safely under.
NOTE_BODY_MAX_CHARS = 60_000


class HubSpotAPIError(RuntimeError):
    """Non-2xx response from the HubSpot API. Carries status code + body."""

    def __init__(self, status_code: int, text: str):
        super().__init__(f"HTTP {status_code}: {text[:300]}")
        self.status_code = status_code
        self.text = text


def _resolve_token(tenant) -> str:
    """
    Resolve the private-app token for this tenant.

    Tenant config may name an env var (crm.hubspot_token_env) holding a
    per-tenant token; otherwise fall back to the shared HUBSPOT_ACCESS_TOKEN.
    Returns "" when neither is set.
    """
    if tenant is not None and getattr(tenant.crm, "hubspot_token_env", None):
        token = os.environ.get(tenant.crm.hubspot_token_env, "")
        if token:
            return token
    return os.environ.get("HUBSPOT_ACCESS_TOKEN", "")


def _api(method: str, path: str, token: str, payload: dict | None = None) -> dict:
    """Single HTTP choke point for every HubSpot call (mocked in offline checks)."""
    response = requests.request(
        method,
        HUBSPOT_BASE + path,
        headers={"Authorization": f"Bearer {token}"},
        json=payload,
        timeout=15,
    )
    if not 200 <= response.status_code < 300:
        raise HubSpotAPIError(response.status_code, response.text)
    return response.json() if response.text else {}


def build_note_body(state: BDRState, max_chars: int = NOTE_BODY_MAX_CHARS) -> str:
    """Plain-text mirror of crm_sync._build_blocks() — the full prospect payload.

    Shared by every token-based connector (HubSpot, Salesforce, Pipedrive):
    the prospect card is free text, so no per-CRM schema match is required.
    """
    enrichment = state.get("enrichment")
    strategy = state.get("strategy")
    card = state.get("card")
    if not (enrichment and strategy and card):
        return ""
    if not card.angles:
        return ""

    rec_key = strategy.recommended_angle
    rec_draft = next((a for a in card.angles if a.angle_key == rec_key), card.angles[0])
    top_contact = enrichment.contacts[0] if enrichment.contacts else None

    lines: list[str] = []
    lines.append("== Account context ==")
    lines.append(f"Industry: {enrichment.industry or '-'}")
    if enrichment.icp:
        lines.append(f"ICP: {enrichment.icp.tier_label} - {enrichment.icp.rationale}")
    if enrichment.account_score:
        lines.append(
            f"Account score: {enrichment.account_score.overall_score}/100 "
            f"({enrichment.account_score.priority_label})"
        )
    lines.append(f"Persona: {strategy.cpo_hypothesis}")
    lines.append(f"Pain signal: {strategy.pain_signal}")

    lines.append("")
    lines.append("== Recommended angle ==")
    lines.append(f"{strategy.angle_name} ({rec_key})")
    lines.append(strategy.rationale)

    lines.append("")
    lines.append("== Before / After ==")
    lines.append(card.before_after)

    lines.append("")
    lines.append("== LinkedIn DM (recommended angle) ==")
    lines.append(rec_draft.dm)

    lines.append("")
    lines.append(f"== Cold email - {rec_draft.email_subject} ==")
    lines.append(rec_draft.email_body)

    if card.sequence:
        lines.append("")
        lines.append("== Sequence ==")
        for touch in card.sequence.touches:
            header = f"Touch {touch.touch_number} - day {touch.day} - {touch.channel}"
            if touch.subject:
                header += f" - {touch.subject}"
            lines.append(header)
            lines.append(touch.body)
            lines.append("")

    if top_contact:
        lines.append("== Top contact (Hunter.io) ==")
        lines.append(f"Name: {top_contact.name or '-'}")
        lines.append(f"Position: {top_contact.position or '-'}")
        lines.append(f"Email: {top_contact.email or '-'}")
        lines.append(f"Confidence: {top_contact.confidence}")

    return "\n".join(lines)[:max_chars]


def _find_company(token: str, company: str, domain: str) -> str:
    """Search for an existing company (by domain when known, else exact name)."""
    if domain:
        filters = [{"propertyName": "domain", "operator": "EQ", "value": domain}]
    else:
        filters = [{"propertyName": "name", "operator": "EQ", "value": company}]
    data = _api(
        "POST",
        "/crm/v3/objects/companies/search",
        token,
        {"filterGroups": [{"filters": filters}], "limit": 1},
    )
    results = data.get("results") or []
    return str(results[0].get("id", "")) if results else ""


def _create_company(token: str, company: str, domain: str) -> str:
    properties = {"name": company}
    if domain:
        properties["domain"] = domain
    data = _api("POST", "/crm/v3/objects/companies", token, {"properties": properties})
    return str(data.get("id", ""))


def _find_or_create_contact(token: str, contact) -> str:
    """Find a contact by email, creating it if absent. "" when contact has no email."""
    if contact is None or not contact.email:
        return ""
    data = _api(
        "POST",
        "/crm/v3/objects/contacts/search",
        token,
        {
            "filterGroups": [
                {"filters": [{"propertyName": "email", "operator": "EQ", "value": contact.email}]}
            ],
            "limit": 1,
        },
    )
    results = data.get("results") or []
    if results:
        return str(results[0].get("id", ""))

    name_parts = (contact.name or "").split()
    properties = {
        "email": contact.email,
        "firstname": name_parts[0] if name_parts else "",
        "lastname": " ".join(name_parts[1:]),
        "jobtitle": contact.position or "",
    }
    data = _api("POST", "/crm/v3/objects/contacts", token, {"properties": properties})
    return str(data.get("id", ""))


def _create_note(token: str, body: str, company_id: str, contact_id: str = "") -> str:
    """Create a Note engagement associated to the company (and contact, if any)."""
    associations = [
        {
            "to": {"id": company_id},
            "types": [
                {
                    "associationCategory": "HUBSPOT_DEFINED",
                    "associationTypeId": ASSOC_NOTE_TO_COMPANY,
                }
            ],
        }
    ]
    if contact_id:
        associations.append(
            {
                "to": {"id": contact_id},
                "types": [
                    {
                        "associationCategory": "HUBSPOT_DEFINED",
                        "associationTypeId": ASSOC_NOTE_TO_CONTACT,
                    }
                ],
            }
        )
    data = _api(
        "POST",
        "/crm/v3/objects/notes",
        token,
        {
            "properties": {
                "hs_note_body": body,
                "hs_timestamp": str(int(time.time() * 1000)),
            },
            "associations": associations,
        },
    )
    return str(data.get("id", ""))


def push_to_hubspot(state: BDRState, *, dry_run: bool = False) -> CRMSyncResult:
    """
    Push the finalised prospect to HubSpot. Returns a CRMSyncResult; never raises.

    Reads the generic sync toggle from state["sync_to_notion"] — the key predates
    multi-provider support and is threaded through the whole UI, so it stays.
    Skips (does not error) when the toggle is off or no token is configured.
    Set dry_run=True (or BDR_CRM_DRY_RUN=1) to build all payloads with zero
    HTTP calls.
    """
    if not state.get("sync_to_notion", False):
        return CRMSyncResult(
            success=False, skipped=True, skip_reason="Sync disabled in UI.", provider="hubspot"
        )

    dry_run = dry_run or os.environ.get("BDR_CRM_DRY_RUN") == "1"

    token = _resolve_token(state.get("tenant"))
    if not token and not dry_run:
        return CRMSyncResult(
            success=False,
            skipped=True,
            skip_reason="HUBSPOT_ACCESS_TOKEN missing in .env (or tenant crm.hubspot_token_env unset).",
            provider="hubspot",
        )

    enrichment = state.get("enrichment")
    company = (enrichment.company if enrichment else state.get("company", "")) or "Unknown"
    domain = enrichment.domain if enrichment else ""
    top_contact = enrichment.contacts[0] if enrichment and enrichment.contacts else None

    note_body = build_note_body(state)

    if dry_run:
        # Everything above is built; nothing below runs — zero HTTP calls.
        return CRMSyncResult(success=True, page_id="dry-run", provider="hubspot", dry_run=True)

    try:
        company_id = _find_company(token, company, domain) or _create_company(token, company, domain)
    except Exception as exc:
        return CRMSyncResult(
            success=False, error=f"HubSpot company upsert failed: {exc}", provider="hubspot"
        )

    contact_id = ""
    try:
        if top_contact is not None:
            contact_id = _find_or_create_contact(token, top_contact)
    except Exception as exc:
        return CRMSyncResult(
            success=False, error=f"HubSpot contact upsert failed: {exc}", provider="hubspot"
        )

    try:
        _create_note(token, note_body, company_id, contact_id)
    except Exception as exc:
        return CRMSyncResult(
            success=False, error=f"HubSpot note create failed: {exc}", provider="hubspot"
        )

    return CRMSyncResult(success=True, page_id=company_id, page_url="", provider="hubspot")
