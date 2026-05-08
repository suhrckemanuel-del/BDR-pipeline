"""
crm_sync.py - Step 4: Notion CRM Connector.

Pushes the finalised prospect (strategy + recommended angle + top contact +
drafted email) into the live Notion database via the official notion-client.

Schema-agnostic: we don't know the exact properties of the user's database, so
we:
  1. Retrieve the database to discover the title-property name (every Notion
     DB has exactly one title property).
  2. Set ONLY that title property to the company name (always succeeds).
  3. Append the rest as page-content blocks (rich-text paragraphs) - these are
     part of the page body, not properties, so no schema match required.

This keeps the connector robust across any user-defined Notion DB shape while
still pushing the full payload into the page.
"""
from __future__ import annotations

import os
from typing import Any

# notion_client is imported lazily inside push_to_notion so the rest of the
# app doesn't break if the package is missing.

from app.agents.state import BDRState, CRMSyncResult


def _build_blocks(state: BDRState) -> list[dict]:
    """Build the Notion page content blocks from the state."""
    enrichment = state.get("enrichment")
    strategy = state.get("strategy")
    card = state.get("card")
    if not (enrichment and strategy and card):
        return []

    if not card.angles:
        return []
    rec_key = strategy.recommended_angle
    rec_draft = next((a for a in card.angles if a.angle_key == rec_key), card.angles[0])
    top_contact = enrichment.contacts[0] if enrichment.contacts else None

    def heading(text: str) -> dict:
        return {
            "object": "block",
            "type": "heading_2",
            "heading_2": {"rich_text": [{"type": "text", "text": {"content": text}}]},
        }

    def paragraph(text: str) -> dict:
        return {
            "object": "block",
            "type": "paragraph",
            "paragraph": {"rich_text": [{"type": "text", "text": {"content": text[:1900]}}]},
        }

    blocks: list[dict] = []
    blocks.append(heading("Account context"))
    blocks.append(paragraph(f"Industry: {enrichment.industry or '-'}"))
    if enrichment.icp:
        blocks.append(paragraph(f"ICP: {enrichment.icp.tier_label} - {enrichment.icp.rationale}"))
    blocks.append(paragraph(f"Persona: {strategy.cpo_hypothesis}"))
    blocks.append(paragraph(f"Pain signal: {strategy.pain_signal}"))

    blocks.append(heading("Recommended angle"))
    blocks.append(paragraph(f"{strategy.angle_name} ({rec_key})"))
    blocks.append(paragraph(strategy.rationale))

    blocks.append(heading("Before / After"))
    blocks.append(paragraph(card.before_after))

    blocks.append(heading("LinkedIn DM (recommended angle)"))
    blocks.append(paragraph(rec_draft.dm))

    blocks.append(heading(f"Cold email - {rec_draft.email_subject}"))
    blocks.append(paragraph(rec_draft.email_body))

    if top_contact:
        blocks.append(heading("Top contact (Hunter.io)"))
        contact_lines = [
            f"Name: {top_contact.name or '-'}",
            f"Position: {top_contact.position or '-'}",
            f"Email: {top_contact.email or '-'}",
            f"Confidence: {top_contact.confidence}",
        ]
        blocks.append(paragraph("\n".join(contact_lines)))

    return blocks


def _find_title_property(db_meta: dict) -> str | None:
    props = db_meta.get("properties", {}) or {}
    # Pass 1: exact type/id match
    for name, info in props.items():
        info = info or {}
        if info.get("type") == "title" or info.get("id") == "title":
            return name
    # Pass 2: common names
    for fallback in ("Name", "Title", "Company", "name", "title"):
        if fallback in props:
            return fallback
    # Pass 3: last resort - use the first property
    if props:
        return next(iter(props))
    return None


def _resolve_notion_parent_and_schema(client: Any, database_id: str) -> tuple[dict, dict]:
    """
    Normalize Notion's legacy database schema and newer data-source schema.

    Older workspaces expose properties directly on the database object.
    Newer workspaces return a database shell with `data_sources`, and the
    writable schema lives on the retrieved data source.
    """
    db_meta = client.databases.retrieve(database_id=database_id)

    if db_meta.get("properties"):
        return {"database_id": database_id}, db_meta

    data_sources = db_meta.get("data_sources") or []
    if not data_sources:
        return {"database_id": database_id}, db_meta

    data_source = data_sources[0] or {}
    data_source_id = data_source.get("id")
    if not data_source_id:
        return {"database_id": database_id}, db_meta

    data_source_meta = client.data_sources.retrieve(data_source_id=data_source_id)
    return {"data_source_id": data_source_id}, data_source_meta


def push_to_notion(state: BDRState) -> CRMSyncResult:
    """
    Push the finalised prospect to Notion. Returns a CRMSyncResult.

    Skips (does not error) if the user disabled sync or env vars are missing.
    """
    if not state.get("sync_to_notion", False):
        return CRMSyncResult(success=False, skipped=True, skip_reason="Sync disabled in UI.")

    token = os.environ.get("NOTION_API_KEY", "")
    # Tenant config overrides env var when set; otherwise fall back to env.
    tenant = state.get("tenant")
    tenant_db_id = ""
    if tenant is not None and tenant.crm.notion_database_id:
        tenant_db_id = tenant.crm.notion_database_id
    db_id = tenant_db_id or os.environ.get("NOTION_DATABASE_ID", "")
    if not token or not db_id:
        return CRMSyncResult(
            success=False,
            skipped=True,
            skip_reason="NOTION_API_KEY or NOTION_DATABASE_ID missing in .env.",
        )

    try:
        from notion_client import Client  # type: ignore
    except ImportError:
        return CRMSyncResult(success=False, error="notion-client not installed.")

    client = Client(auth=token)

    try:
        parent, db_meta = _resolve_notion_parent_and_schema(client, db_id)
    except Exception as exc:
        return CRMSyncResult(success=False, error=f"Notion DB retrieve failed: {exc}")

    # DEBUG: surface the raw properties so we can see the actual structure
    import json as _json  # noqa: F401
    props_raw = db_meta.get("properties", {})
    props_debug = {k: {"type": v.get("type"), "id": v.get("id")} for k, v in (props_raw or {}).items()}

    title_prop = _find_title_property(db_meta)
    if not title_prop:
        return CRMSyncResult(
            success=False,
            error=f"No title property found. DB properties: {props_debug}"
        )

    enrichment = state.get("enrichment")
    company = (enrichment.company if enrichment else state.get("company", "")) or "Unknown"

    properties = {
        title_prop: {
            "title": [{"type": "text", "text": {"content": company}}]
        }
    }
    blocks = _build_blocks(state)

    try:
        page = client.pages.create(
            parent=parent,
            properties=properties,
            children=blocks[:90],  # Notion caps children per request at 100
        )
    except Exception as exc:
        return CRMSyncResult(success=False, error=f"Notion page create failed: {exc}")

    return CRMSyncResult(
        success=True,
        page_id=page.get("id", ""),
        page_url=page.get("url", ""),
    )


def run_crm_sync(state: BDRState) -> dict:
    """LangGraph node - push to Notion, attach result to state."""
    if state.get("error"):
        return {}
    trace = list(state.get("agent_trace", []))
    if state.get("sync_to_notion"):
        trace.append("CRM Sync: pushing to Notion database")
    else:
        trace.append("CRM Sync: skipped (sync toggle off)")

    result = push_to_notion(state)
    if result.skipped:
        trace.append(f"CRM Sync: skipped - {result.skip_reason}")
    elif result.success:
        trace.append("CRM Sync: page created in Notion")
    else:
        trace.append(f"CRM Sync: failed - {result.error}")
    return {"crm_result": result, "agent_trace": trace}
