"""
main.py — BDR Pipeline Streamlit dashboard (white-label).

A single-tenant-at-a-time UI. The active tenant is selected from the sidebar
or pinned via the BDR_TENANT environment variable. All copy, branding, and
positioning come from the loaded TenantConfig — no hardcoded brand strings.

Run:
    streamlit run app/main.py

Pin a tenant for scripted runs:
    BDR_TENANT=demo streamlit run app/main.py
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

import pandas as pd
import streamlit as st

# Ensure the repo root is importable when Streamlit launches this file directly.
ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

# Hydrate env from Streamlit secrets when running on Streamlit Cloud
_SECRET_KEYS = (
    "ANTHROPIC_API_KEY", "EXA_API_KEY", "HUNTER_API_KEY",
    "NOTION_API_KEY", "NOTION_DATABASE_ID",
    "GMAIL_SENDER", "GMAIL_APP_PASSWORD",
    "LANGCHAIN_API_KEY", "BDR_TENANT",
)
try:
    for _k in _SECRET_KEYS:
        if _k not in os.environ and _k in st.secrets:
            os.environ[_k] = st.secrets[_k]
except Exception:
    pass

# Load .env from repo root if present
try:
    from dotenv import load_dotenv  # type: ignore
    load_dotenv(ROOT / ".env")
except ImportError:
    pass

from app.tenants import list_tenants, load_tenant  # noqa: E402

# ---------------------------------------------------------------------------
# Tenant selection — env var pin OR sidebar dropdown
# ---------------------------------------------------------------------------
_AVAILABLE = list_tenants()
if not _AVAILABLE:
    st.error(
        "No tenants found. Create one under `tenants/<slug>/` "
        "(see `tenants/README.md` for the file layout)."
    )
    st.stop()

_PIN = os.environ.get("BDR_TENANT", "").strip()
if _PIN and _PIN not in _AVAILABLE:
    st.warning(f"BDR_TENANT={_PIN!r} not found. Falling back to sidebar dropdown.")
    _PIN = ""

_DEFAULT = _PIN or st.session_state.get("active_tenant") or _AVAILABLE[0]


# ---------------------------------------------------------------------------
# Page config — must come BEFORE any other st.* calls in main script flow
# ---------------------------------------------------------------------------
_default_tenant = load_tenant(_DEFAULT)

st.set_page_config(
    page_title=f"{_default_tenant.brand.name} — BDR Pipeline",
    page_icon=_default_tenant.brand.icon,
    layout="wide",
    initial_sidebar_state="expanded",
)


@st.cache_resource(show_spinner=False)
def _build_workflow_for(tenant_id: str):  # noqa: ARG001  (cached per tenant)
    from app.agents.workflow_engine import build_workflow
    return build_workflow(use_checkpointer=False)


# ---------------------------------------------------------------------------
# Sidebar — tenant switcher + run inputs
# ---------------------------------------------------------------------------
with st.sidebar:
    st.markdown(f"### {_default_tenant.brand.icon} BDR Pipeline")
    st.caption("White-label outreach engine")

    pin_locked = bool(_PIN)
    selection = st.selectbox(
        "Tenant",
        options=_AVAILABLE,
        index=_AVAILABLE.index(_DEFAULT),
        disabled=pin_locked,
        help="Pinned via BDR_TENANT env var." if pin_locked else "Switch positioning / ICP / copy.",
    )

    if selection != st.session_state.get("active_tenant"):
        st.session_state["active_tenant"] = selection
        st.rerun()

    tenant = load_tenant(selection)

    st.markdown("---")
    st.markdown(f"**{tenant.brand.name}**")
    if tenant.brand.tagline:
        st.caption(tenant.brand.tagline)
    st.caption(f"Persona: {tenant.persona.title}")
    st.caption(f"Sender: {tenant.sender.name}")

    st.markdown("---")
    st.markdown("**Run a prospect**")

    company = st.text_input("Company name", placeholder="e.g. Globex Industries")
    industry = st.text_input("Industry", placeholder="e.g. B2B SaaS")
    trigger_headline = st.text_input(
        "Trigger headline (optional)",
        placeholder="Recent earnings miss, new CRO hire, etc.",
        help="Pasted into the humanizer to anchor the observation in a real event.",
    )

    sync_to_notion = st.checkbox(
        "Sync to Notion",
        value=tenant.crm.enabled,
        disabled=not tenant.crm.enabled,
        help="Disabled — set crm.enabled: true in tenant config.yaml to enable.",
    )

    run_clicked = st.button("Run pipeline", type="primary", use_container_width=True)

    st.markdown("---")
    with st.expander("Tenant prospects", expanded=False):
        if tenant.prospects_csv.exists():
            try:
                df = pd.read_csv(tenant.prospects_csv)
                st.dataframe(df, hide_index=True, use_container_width=True)
            except Exception as e:
                st.caption(f"Could not read prospects.csv: {e}")
        else:
            st.caption("No prospects.csv found for this tenant.")


# ---------------------------------------------------------------------------
# Main area — header + workflow output
# ---------------------------------------------------------------------------
st.markdown(f"## {tenant.brand.icon} {tenant.brand.name} — BDR Pipeline")
st.caption(tenant.business.description)

if not run_clicked or not company:
    st.info(
        "Enter a company name in the sidebar and click **Run pipeline** to "
        "generate enrichment, strategy, and a 5-touch outreach sequence "
        f"positioned for {tenant.brand.name}."
    )

    with st.expander("How the pipeline works", expanded=False):
        st.markdown(
            "1. **Enrichment** — Exa pulls live news + job signals; Hunter.io "
            "resolves domain + senior contacts; Claude Haiku synthesizes a "
            f"research summary through the {tenant.persona.title} lens.\n"
            "2. **Strategist** — Claude Sonnet picks one of three "
            "tenant-configured angles via structured output.\n"
            "3. **Humanizer** — Claude generates 3 specific observations + a "
            "Before/After narrative; the deterministic assembler glues them "
            "into emails using the tenant's fixed copy banks (no LLM \"voice\").\n"
            "4. **Critic** — Claude scores every touch on 4 dimensions "
            "(pain specificity, proof relevance, CTA clarity, human voice) and "
            "rewrites the first paragraph of any email touch with a failing dim.\n"
            "5. **CRM Sync** — Optional push to a Notion database."
        )

    with st.expander("Tenant angles", expanded=False):
        for angle in tenant.angles:
            st.markdown(f"**{angle.name}** ({angle.key}) — {angle.tab_label}")
            st.caption(angle.description)
            st.markdown(f"*Core insight:* {angle.core_insight}")
            if angle.avoid:
                st.markdown(f"*Avoid:* {angle.avoid}")
            st.markdown("---")

    st.stop()

# ---------------------------------------------------------------------------
# Run the workflow with streaming progress
# ---------------------------------------------------------------------------
from app.agents.workflow_engine import run_workflow_stream  # noqa: E402

workflow = _build_workflow_for(tenant.tenant_id)

progress_box = st.empty()
trace_box = st.empty()
final_state: dict | None = None

with progress_box.container():
    st.info(f"Running pipeline for **{company}**…")

trace_lines: list[str] = []
try:
    for latest, state in run_workflow_stream(
        workflow,
        company=company,
        industry=industry or "unknown",
        tenant=tenant,
        sync_to_notion=sync_to_notion,
        trigger_headline=trigger_headline,
    ):
        trace_lines = list(state.get("agent_trace", []))
        with trace_box.container():
            st.code("\n".join(trace_lines[-12:]) or latest, language="text")
        final_state = state
except Exception as exc:  # surface workflow errors visibly rather than crashing the UI
    st.exception(exc)
    st.stop()

progress_box.empty()

if not final_state:
    st.error("Pipeline produced no state.")
    st.stop()

if final_state.get("error"):
    st.error(f"Pipeline error: {final_state['error']}")
    with st.expander("Trace"):
        st.code("\n".join(trace_lines), language="text")
    st.stop()

# ---------------------------------------------------------------------------
# Render results
# ---------------------------------------------------------------------------
enrichment = final_state.get("enrichment")
strategy = final_state.get("strategy")
card = final_state.get("card")
critic_result = final_state.get("critic_result")
crm_result = final_state.get("crm_result")

st.success(f"Pipeline complete for **{company}**.")

col1, col2, col3 = st.columns(3)
with col1:
    if enrichment and enrichment.icp:
        st.metric(
            "ICP",
            f"{enrichment.icp.tier_label}",
            help=enrichment.icp.rationale,
        )
        st.caption(f"Score: {enrichment.icp.score}/100")
with col2:
    if strategy:
        st.metric("Recommended angle", strategy.angle_name)
        st.caption(strategy.cpo_hypothesis)
with col3:
    if critic_result:
        st.metric(
            "Critic quality",
            f"{critic_result.overall_quality:.1f} / 5.0",
            help=critic_result.critique_summary,
        )
        st.caption(f"Rewrites applied: {critic_result.rewrites_applied}")

st.markdown("---")

if strategy:
    st.markdown(f"### Strategy rationale")
    st.markdown(f"**Pain signal:** {strategy.pain_signal}")
    st.markdown(strategy.rationale)

if card:
    st.markdown("### Before / After")
    st.markdown(card.before_after)

    st.markdown("### Outreach drafts")
    angle_tabs = st.tabs([a.tab_label for a in card.angles])
    for tab, angle in zip(angle_tabs, card.angles):
        with tab:
            st.markdown(f"**{angle.name}** ({angle.angle_key})")
            st.markdown(f"**Subject:** {angle.email_subject}")
            st.markdown("**Email body:**")
            st.code(angle.email_body, language="text")
            st.markdown("**LinkedIn DM:**")
            st.code(angle.dm, language="text")

    if card.sequence:
        st.markdown("### Outreach sequence")
        st.caption(
            f"Recommended angle: {card.sequence.recommended_angle} · "
            f"Persona: {card.sequence.entry_persona} · "
            f"{len(card.sequence.touches)} touches"
        )
        for touch in card.sequence.touches:
            label = f"Touch {touch.touch_number} · Day {touch.day} · {touch.channel}"
            with st.expander(label):
                if touch.subject:
                    st.markdown(f"**Subject:** {touch.subject}")
                st.code(touch.body, language="text")
                if touch.note:
                    st.caption(touch.note)

if enrichment:
    with st.expander("Enrichment details", expanded=False):
        if enrichment.live_signals:
            st.markdown("**Live signals (Exa):**")
            for s in enrichment.live_signals:
                st.markdown(f"- [{s.title}]({s.url})" if s.url else f"- {s.title}")
        if enrichment.contacts:
            st.markdown("**Contacts (Hunter.io):**")
            contacts_df = pd.DataFrame(
                [c.model_dump() for c in enrichment.contacts]
            )[["name", "position", "email", "confidence"]]
            st.dataframe(contacts_df, hide_index=True, use_container_width=True)
        if enrichment.research_summary:
            st.markdown("**Research summary:**")
            st.markdown(enrichment.research_summary)

if crm_result and not crm_result.skipped:
    if crm_result.success:
        st.success(f"Synced to Notion: {crm_result.page_url}")
    else:
        st.warning(f"Notion sync failed: {crm_result.error}")

with st.expander("Full trace", expanded=False):
    st.code("\n".join(trace_lines), language="text")
