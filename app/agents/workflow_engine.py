"""
workflow_engine.py — Core orchestrator for the BDR pipeline.

    Enrichment  ->  Strategist  ->  Humanizer  ->  Critic  ->  CRM Sync  ->  END

The critic is the LLM-based 4-dimension quality gate (`app.agents.critic`)
which scores every touch and rewrites failing first paragraphs. No retry loop —
the critic does in-place rewrites and continues forward.

Tenant config is set once on the initial state and threaded through every node.
"""
from __future__ import annotations

from pathlib import Path
from typing import Generator, Tuple

from langgraph.graph import END, StateGraph

from app.agents.critic import run_critic
from app.agents.enrichment import run_enrichment
from app.agents.humanizer import run_humanizer
from app.agents.state import BDRState
from app.agents.strategist import run_strategist
from app.services.crm_sync import run_crm_sync
from app.tenants.schema import TenantConfig

NODE_ORDER = ("enrichment", "strategist", "humanizer", "critic", "crm_sync")

_DB_PATH = Path(__file__).resolve().parents[2] / "pipeline" / "checkpoints.db"


def build_workflow(use_checkpointer: bool = True):
    """Compile and return the LangGraph workflow with optional SqliteSaver."""
    graph = StateGraph(BDRState)

    graph.add_node("enrichment", run_enrichment)
    graph.add_node("strategist", run_strategist)
    graph.add_node("humanizer", run_humanizer)
    graph.add_node("critic", run_critic)
    graph.add_node("crm_sync", run_crm_sync)

    graph.set_entry_point("enrichment")
    graph.add_edge("enrichment", "strategist")
    graph.add_edge("strategist", "humanizer")
    graph.add_edge("humanizer", "critic")
    graph.add_edge("critic", "crm_sync")
    graph.add_edge("crm_sync", END)

    checkpointer = None
    if use_checkpointer:
        try:
            from langgraph.checkpoint.sqlite import SqliteSaver  # type: ignore
            _DB_PATH.parent.mkdir(parents=True, exist_ok=True)
            checkpointer = SqliteSaver.from_conn_string(str(_DB_PATH))
        except Exception:
            pass

    return graph.compile(checkpointer=checkpointer)


def _build_initial_state(
    company: str,
    industry: str,
    tenant: TenantConfig,
    sync_to_notion: bool,
    trigger_headline: str = "",
    prospect_notes: str = "",
) -> BDRState:
    return {
        "tenant": tenant,
        "company": company.strip(),
        "industry": industry.strip(),
        "sync_to_notion": bool(sync_to_notion),
        "trigger_headline": trigger_headline.strip(),
        "prospect_notes": prospect_notes.strip(),
        "agent_trace": [],
        "critic_retries": 0,
    }


def run_workflow_stream(
    app,
    company: str,
    industry: str,
    tenant: TenantConfig,
    sync_to_notion: bool = False,
    trigger_headline: str = "",
    prospect_notes: str = "",
    thread_id: str | None = None,
) -> Generator[Tuple[str, dict], None, None]:
    """
    Stream node-by-node updates. Yields (latest_trace_line, full_state).
    thread_id is used as the checkpoint key (defaults to tenant_id + company slug).
    """
    import re as _re
    company_slug = _re.sub(r"[^a-z0-9]+", "_", company.lower()).strip("_")
    slug = thread_id or f"{tenant.tenant_id}_{company_slug}"

    initial = _build_initial_state(
        company, industry, tenant, sync_to_notion, trigger_headline, prospect_notes
    )

    config: dict = {}
    if app.checkpointer:
        config = {"configurable": {"thread_id": slug}}

    for event in app.stream(initial, config=config, stream_mode="values"):
        trace = event.get("agent_trace", [])
        latest = trace[-1] if trace else "init"
        yield latest, event


def run_workflow(
    app,
    company: str,
    industry: str,
    tenant: TenantConfig,
    sync_to_notion: bool = False,
    trigger_headline: str = "",
    prospect_notes: str = "",
) -> dict:
    """Synchronous variant — returns the final state dict."""
    initial = _build_initial_state(
        company, industry, tenant, sync_to_notion, trigger_headline, prospect_notes
    )
    return app.invoke(initial)
