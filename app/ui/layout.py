"""
layout.py — Composition of the sidebar and main panel.

Pure layout: collects sidebar inputs and renders the result panel from a final
BDRState. The orchestrator (app/main.py) handles workflow execution, session
state, and stage-progress tracking.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Optional

import pandas as pd
import streamlit as st

from app.tenants.schema import TenantConfig
from app.ui import components as C


@dataclass
class SidebarInputs:
    selected_tenant: str
    company: str
    industry: str
    trigger_headline: str
    sync_to_notion: bool
    run_clicked: bool


# ---------------------------------------------------------------------------
# Sidebar
# ---------------------------------------------------------------------------
def render_sidebar(
    tenant: TenantConfig,
    available_tenants: list[str],
    pin_locked: bool = False,
) -> SidebarInputs:
    with st.sidebar:
        C.brand_block(
            icon=tenant.brand.icon,
            name=tenant.brand.name,
            tagline=tenant.brand.tagline or "Outreach Pipeline",
            show_demo_chip=(tenant.tenant_id == "demo"),
        )

        # Tenant switcher
        selection = st.selectbox(
            "Tenant",
            options=available_tenants,
            index=available_tenants.index(tenant.tenant_id),
            disabled=pin_locked,
            label_visibility="collapsed",
            help="Pinned via BDR_TENANT env var." if pin_locked else "Switch positioning / ICP / copy.",
        )

        C.sidebar_section("Positioning")
        st.caption(f"Persona · {tenant.persona.title}")
        st.caption(f"Sender · {tenant.sender.name}")

        C.sidebar_section("Run a prospect")
        company = st.text_input(
            "Company",
            placeholder="Globex Industries",
            label_visibility="collapsed",
            key="ui_company",
        )
        industry = st.text_input(
            "Industry",
            placeholder="Industry (optional)",
            label_visibility="collapsed",
            key="ui_industry",
        )
        trigger_headline = st.text_input(
            "Trigger headline",
            placeholder="Trigger headline (optional)",
            label_visibility="collapsed",
            key="ui_trigger",
        )

        sync_to_notion = st.checkbox(
            "Sync to Notion",
            value=tenant.crm.enabled,
            disabled=not tenant.crm.enabled,
            help="Set crm.enabled: true in tenant config.yaml to enable.",
        )

        run_clicked = st.button(
            "▶  Run pipeline",
            type="primary",
            use_container_width=True,
            disabled=not company.strip(),
        )

        # Prospect list
        prospects = _load_prospects(tenant)
        if prospects:
            C.sidebar_section(f"Prospects · {len(prospects)}")
            C.prospect_list(prospects, active_company=company.strip())
        else:
            C.sidebar_section("Prospects")
            st.caption("No prospects.csv for this tenant.")

    return SidebarInputs(
        selected_tenant=selection,
        company=company.strip(),
        industry=industry.strip(),
        trigger_headline=trigger_headline.strip(),
        sync_to_notion=sync_to_notion,
        run_clicked=run_clicked,
    )


def _load_prospects(tenant: TenantConfig) -> list[dict]:
    if not tenant.prospects_csv.exists():
        return []
    try:
        df = pd.read_csv(tenant.prospects_csv, dtype=str).fillna("")
        return df.to_dict(orient="records")
    except Exception:
        return []


# ---------------------------------------------------------------------------
# Empty / running states
# ---------------------------------------------------------------------------
def render_empty(tenant: TenantConfig) -> None:
    C.stage_nav()  # all idle
    C.empty_state(
        icon=tenant.brand.icon or "✨",
        headline=f"Run your first prospect through the {tenant.brand.name} pipeline",
        sub=(
            f"Enter a company name in the sidebar and hit Run. "
            f"You'll get an ICP read, a research summary through the {tenant.persona.title} lens, "
            "and a 5-touch outreach sequence — all positioned around the angles defined in this tenant config."
        ),
    )

    C.section_title("Tenant angles")
    cols = st.columns(3)
    for col, angle in zip(cols, tenant.angles):
        with col:
            st.markdown(
                f'<div class="draft-card" style="margin-bottom:0">'
                f'<div class="draft-label">{angle.tab_label}</div>'
                f'<div class="draft-subject">{angle.name}</div>'
                f'<div class="draft-body">{angle.description}</div>'
                "</div>",
                unsafe_allow_html=True,
            )


def render_running(tenant: TenantConfig, company: str, active_stage: str, done_stages: tuple[str, ...]) -> None:
    C.header_block(company, industry="", tier_label="")
    C.stage_nav(active_key=active_stage, done_keys=done_stages)


# ---------------------------------------------------------------------------
# Main result panel
# ---------------------------------------------------------------------------
def render_main(tenant: TenantConfig, state: dict) -> None:
    enrichment = state.get("enrichment")
    strategy = state.get("strategy")
    card = state.get("card")
    critic_result = state.get("critic_result")
    crm_result = state.get("crm_result")

    company = state.get("company") or ""
    industry = state.get("industry") or ""
    tier_label = ""
    if enrichment and enrichment.icp:
        tier_label = enrichment.icp.tier_label or f"Tier {enrichment.icp.tier}"

    C.header_block(company, industry=industry, tier_label=tier_label)

    # All stages completed
    done = tuple(k for k, _ in C.STAGE_LABELS)
    C.stage_nav(active_key="", done_keys=done)

    # KPI strip
    C.kpi_strip(_build_kpis(enrichment, card, critic_result))

    # Strategy rationale + before/after
    if strategy:
        C.strategy_rationale_card(strategy)
    if card:
        C.before_after_block(
            before=_extract_before(card.before_after),
            after=_extract_after(card.before_after),
        )

    # Tabs
    tab_seq, tab_research, tab_contacts, tab_drafts = st.tabs(
        ["Sequence", "Research", "Contacts", "Drafts"]
    )

    with tab_seq:
        _render_sequence_tab(card)
    with tab_research:
        _render_research_tab(enrichment)
    with tab_contacts:
        _render_contacts_tab(enrichment)
    with tab_drafts:
        _render_drafts_tab(card)

    # CRM sync footer
    if crm_result and not getattr(crm_result, "skipped", False):
        if getattr(crm_result, "success", False):
            st.success(f"Synced to Notion: {crm_result.page_url}")
        elif getattr(crm_result, "error", ""):
            st.warning(f"Notion sync failed: {crm_result.error}")


# ---------------------------------------------------------------------------
# KPI builder
# ---------------------------------------------------------------------------
def _build_kpis(enrichment: Any, card: Any, critic_result: Any) -> list[tuple[str, str, str]]:
    items: list[tuple[str, str, str]] = []

    if enrichment and enrichment.icp:
        tier = enrichment.icp.tier
        tone = "good" if tier == 1 else "warn" if tier == 2 else "bad"
        items.append(("ICP Tier", f"T{tier}", tone))
    else:
        items.append(("ICP Tier", "—", ""))

    contacts = getattr(enrichment, "contacts", []) if enrichment else []
    items.append(("Contacts", str(len(contacts)), ""))

    signals = getattr(enrichment, "live_signals", []) if enrichment else []
    items.append(("Signals", str(len(signals)), ""))

    intent = getattr(enrichment, "intent_score", 0) if enrichment else 0
    if intent:
        tone = "good" if intent >= 60 else "warn" if intent >= 30 else ""
        items.append(("Intent", str(intent), tone))

    sequence = getattr(card, "sequence", None) if card else None
    if sequence:
        items.append(("Touches", str(len(sequence.touches)), ""))
        if sequence.touches:
            window = max(t.day for t in sequence.touches)
            items.append(("Window", f"{window}d", ""))

    if critic_result is not None:
        score = getattr(critic_result, "overall_quality", None)
        if score is not None:
            tone = "good" if score >= 4.0 else "warn" if score >= 3.0 else "bad"
            items.append(("Critic", f"{score:.1f}/5", tone))

    return items


# ---------------------------------------------------------------------------
# Tab renderers
# ---------------------------------------------------------------------------
def _render_sequence_tab(card: Any) -> None:
    sequence = getattr(card, "sequence", None) if card else None
    if not sequence or not sequence.touches:
        C.empty_state("✉️", "No sequence yet", "The humanizer didn't produce an outreach sequence for this run.")
        return
    C.section_title(
        f"5-touch sequence · angle {sequence.recommended_angle} · {sequence.entry_persona}"
    )
    C.sequence_timeline(sequence.touches)
    for t in sequence.touches:
        C.touch_card(t)


def _render_research_tab(enrichment: Any) -> None:
    if not enrichment:
        C.empty_state("🔍", "No enrichment", "Run the pipeline to gather research.")
        return

    summary = getattr(enrichment, "research_summary", "") or ""
    if summary:
        C.section_title("Research summary")
        st.markdown(
            f'<div class="rationale-card"><div class="rationale-body">{summary}</div></div>',
            unsafe_allow_html=True,
        )

    live = getattr(enrichment, "live_signals", []) or []
    if live:
        C.section_title("Live signals")
        C.signals_grid(live)

    jobs = getattr(enrichment, "job_signals", []) or []
    if jobs:
        C.section_title("Job signals")
        C.signals_grid(jobs)


def _render_contacts_tab(enrichment: Any) -> None:
    contacts = getattr(enrichment, "contacts", []) if enrichment else []
    if not contacts:
        C.empty_state("👤", "No contacts", "Hunter.io returned no senior contacts at this domain.")
        return
    domain = getattr(enrichment, "domain", "") or ""
    C.section_title(f"Senior contacts · {domain}" if domain else "Senior contacts")
    C.contacts_table(contacts)


def _render_drafts_tab(card: Any) -> None:
    angles = getattr(card, "angles", []) if card else []
    if not angles:
        C.empty_state("✍️", "No drafts yet", "The humanizer didn't produce any angle drafts for this run.")
        return
    sub_tabs = st.tabs([a.tab_label or a.name for a in angles])
    for tab, angle in zip(sub_tabs, angles):
        with tab:
            C.section_title(f"{angle.name} · {angle.angle_key}")
            C.draft_card("Email", angle.email_subject, angle.email_body)
            C.draft_card("LinkedIn DM", "", angle.dm)


# ---------------------------------------------------------------------------
# Before/After extraction
# The humanizer returns a single `before_after` string. Try to split it on the
# common "Before:" / "After:" markers; fall back to dumping the whole thing
# into the After column.
# ---------------------------------------------------------------------------
def _extract_before(text: str) -> str:
    if not text:
        return ""
    lower = text.lower()
    if "before:" in lower and "after:" in lower:
        b_idx = lower.index("before:") + len("before:")
        a_idx = lower.index("after:")
        return text[b_idx:a_idx].strip(" \n:")
    return text.split("\n\n", 1)[0].strip()


def _extract_after(text: str) -> str:
    if not text:
        return ""
    lower = text.lower()
    if "after:" in lower:
        a_idx = lower.index("after:") + len("after:")
        return text[a_idx:].strip(" \n:")
    parts = text.split("\n\n", 1)
    return parts[1].strip() if len(parts) == 2 else ""
