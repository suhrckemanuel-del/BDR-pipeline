"""
main.py — BDR Pipeline Streamlit dashboard (white-label).

Thin orchestrator: hydrates env, resolves the active tenant, injects the Chalk
theme, then delegates rendering to app.ui. The active tenant is selected from
the sidebar dropdown or pinned via the BDR_TENANT environment variable.

Run:
    streamlit run app/main.py

Pin a tenant for scripted runs:
    BDR_TENANT=demo streamlit run app/main.py
"""
from __future__ import annotations

import os
import sys
from datetime import datetime, timezone
from pathlib import Path

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

try:
    from dotenv import load_dotenv  # type: ignore
    load_dotenv(ROOT / ".env")
except ImportError:
    pass

from app.tenants import list_tenants, load_tenant  # noqa: E402
from app.ui import inject_css, render_sidebar, render_main, render_empty  # noqa: E402
from app.ui.layout import render_running  # noqa: E402
from app.ui.components import STAGE_LABELS  # noqa: E402

# ---------------------------------------------------------------------------
# Tenant resolution (must precede st.set_page_config)
# ---------------------------------------------------------------------------
_AVAILABLE = list_tenants()
if not _AVAILABLE:
    st.error("No tenants found. Create one under `tenants/<slug>/` (see `tenants/README.md`).")
    st.stop()

_PIN = os.environ.get("BDR_TENANT", "").strip()
if _PIN and _PIN not in _AVAILABLE:
    st.warning(f"BDR_TENANT={_PIN!r} not found. Falling back to sidebar dropdown.")
    _PIN = ""

_DEFAULT = _PIN or st.session_state.get("active_tenant") or _AVAILABLE[0]
_default_tenant = load_tenant(_DEFAULT)

st.set_page_config(
    page_title=f"{_default_tenant.brand.name} — BDR Pipeline",
    page_icon=_default_tenant.brand.icon,
    layout="wide",
    initial_sidebar_state="expanded",
)

# ---------------------------------------------------------------------------
# Theme — accent driven by tenant.brand.primary_color
# ---------------------------------------------------------------------------
inject_css(primary_color=_default_tenant.brand.primary_color)


@st.cache_resource(show_spinner=False)
def _build_workflow_for(tenant_id: str):  # noqa: ARG001  (cached per tenant)
    from app.agents.workflow_engine import build_workflow
    return build_workflow(use_checkpointer=False)


# ---------------------------------------------------------------------------
# Sidebar — collect inputs
# ---------------------------------------------------------------------------
inputs = render_sidebar(
    tenant=_default_tenant,
    available_tenants=_AVAILABLE,
    pin_locked=bool(_PIN),
)

# Tenant switch — rerun with new accent
if inputs.selected_tenant != _DEFAULT:
    st.session_state["active_tenant"] = inputs.selected_tenant
    st.rerun()

tenant = _default_tenant

if inputs.clear_last_result_clicked:
    for _key in (
        "last_final_state",
        "last_tenant_id",
        "last_company",
        "last_industry",
        "last_run_completed_at",
    ):
        st.session_state.pop(_key, None)
    st.rerun()

# ---------------------------------------------------------------------------
# Run store — persisted history + review queue (fails soft when unavailable)
# ---------------------------------------------------------------------------
from app.services.run_store import RunStore, hydrate_state  # noqa: E402


def _handle_store_actions() -> None:
    """Approve-queue actions and run hydration from the sidebar."""
    if not (inputs.approve_run_id or inputs.open_run_id):
        return
    store = RunStore()
    try:
        if not store.available():
            return
        if inputs.approve_run_id:
            store.set_approved(inputs.approve_run_id, True)
            st.toast(f"Approved run {inputs.approve_run_id}")
        if inputs.open_run_id:
            loaded = store.load_state(inputs.open_run_id)
            if loaded:
                state = hydrate_state(loaded)
                st.session_state["last_final_state"] = state
                st.session_state["last_tenant_id"] = state.get("tenant_id") or tenant.tenant_id
                st.session_state["last_company"] = state.get("company") or "persisted run"
                st.session_state["last_industry"] = state.get("industry") or ""
                st.session_state["last_run_completed_at"] = state.get("updated_at") or ""
    except Exception as exc:  # store problems must never block the UI
        st.warning(f"Run store unavailable: {exc}")
    finally:
        store.close()
    if inputs.approve_run_id or inputs.open_run_id:
        st.rerun()


_handle_store_actions()

# ---------------------------------------------------------------------------
# Routing — empty / running / complete
# ---------------------------------------------------------------------------
if not inputs.run_clicked:
    last_state = st.session_state.get("last_final_state")
    if last_state and st.session_state.get("last_tenant_id") == tenant.tenant_id:
        last_company = st.session_state.get("last_company") or last_state.get("company") or "last account"
        st.caption(f"Showing last completed run for {last_company}. Run again to refresh.")
        render_main(tenant, last_state)
        st.stop()
    render_empty(tenant)
    st.stop()

if not inputs.company:
    render_empty(tenant)
    st.stop()

# ---------------------------------------------------------------------------
# Run the workflow with streaming progress
# ---------------------------------------------------------------------------
from app.agents.workflow_engine import run_workflow_stream  # noqa: E402

workflow = _build_workflow_for(tenant.tenant_id)

panel = st.empty()
final_state: dict | None = None
_run_store = RunStore()
_run_id: str | None = None
try:
    if _run_store.available():
        _run_id = _run_store.start_run({
            "tenant": tenant,
            "company": inputs.company,
            "industry": inputs.industry,
        })
except Exception:
    _run_id = None  # store unavailable — run proceeds unpersisted

# Map trace lines back to stage keys so the nav can light up the active stage.
_STAGE_KEYS = [k for k, _ in STAGE_LABELS]
done_stages: list[str] = []
active_stage = _STAGE_KEYS[0]

try:
    for latest, state in run_workflow_stream(
        workflow,
        company=inputs.company,
        industry=inputs.industry or "unknown",
        tenant=tenant,
        sync_to_notion=inputs.sync_to_notion,
        trigger_headline=inputs.trigger_headline,
    ):
        # Infer stage progress from which slices the state has populated.
        new_done: list[str] = []
        if state.get("enrichment"):  new_done.append("enrichment")
        if state.get("strategy"):    new_done.append("strategist")
        if state.get("card"):        new_done.append("humanizer")
        if state.get("critic_result") is not None: new_done.append("critic")
        if state.get("crm_result") is not None:    new_done.append("crm_sync")
        done_stages = new_done

        next_stage = next((k for k in _STAGE_KEYS if k not in done_stages), "")
        active_stage = next_stage or ""

        if _run_id:
            try:
                _run_store.update_run(_run_id, state)
            except Exception:
                _run_id = None  # stop persisting rather than break the run

        with panel.container():
            render_running(tenant, inputs.company, active_stage, tuple(done_stages))
            st.info(f"Running pipeline for **{inputs.company}** — {active_stage or 'finishing up'}…")

        final_state = state
except Exception as exc:
    st.exception(exc)
    st.stop()

panel.empty()

if not final_state:
    st.error("Pipeline produced no state.")
    st.stop()

if final_state.get("error"):
    st.error(f"Pipeline error: {final_state['error']}")
    st.stop()

st.session_state["last_final_state"] = final_state
st.session_state["last_tenant_id"] = tenant.tenant_id
st.session_state["last_company"] = final_state.get("company") or inputs.company
st.session_state["last_industry"] = final_state.get("industry") or inputs.industry
st.session_state["last_run_completed_at"] = datetime.now(timezone.utc).isoformat()

if _run_id:
    try:
        _run_store.finish_run(_run_id, final_state)
    except Exception:
        pass
    finally:
        _run_store.close()

# ---------------------------------------------------------------------------
# Render results
# ---------------------------------------------------------------------------
render_main(tenant, final_state)
