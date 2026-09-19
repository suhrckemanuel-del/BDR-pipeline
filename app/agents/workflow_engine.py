"""
workflow_engine.py — Core orchestrator for the BDR pipeline.

    Enrichment  ->  Strategist  ->  Humanizer  ->  Critic  ->  CRM Sync  ->  END

The critic is the LLM-based 4-dimension quality gate (`app.agents.critic`)
which scores every touch and rewrites failing first paragraphs. No retry loop —
the critic does in-place rewrites and continues forward.

Tenant config is set once on the initial state and threaded through every node.
"""
from __future__ import annotations

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


def build_workflow(use_checkpointer: bool = False):
    """Compile and return the LangGraph workflow.

    Kept as a no-arg call site everywhere; the former SqliteSaver path was
    removed (A3 cleanup) — runs are short-lived and never resumed, so durable
    persistence lives in the run store, not a LangGraph checkpointer.
    """
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

    return graph.compile()


def _build_initial_state(
    company: str,
    industry: str,
    tenant: TenantConfig,
    sync_to_notion: bool,
    trigger_headline: str = "",
) -> BDRState:
    return {
        "tenant": tenant,
        "company": company.strip(),
        "industry": industry.strip(),
        "sync_to_notion": bool(sync_to_notion),
        "trigger_headline": trigger_headline.strip(),
        "agent_trace": [],
        "critic_retries": 0,
        "degradations": [],
    }


def run_workflow_stream(
    app,
    company: str,
    industry: str,
    tenant: TenantConfig,
    sync_to_notion: bool = False,
    trigger_headline: str = "",
) -> Generator[Tuple[str, dict], None, None]:
    """
    Stream node-by-node updates. Yields (latest_trace_line, full_state).
    """
    initial = _build_initial_state(
        company, industry, tenant, sync_to_notion, trigger_headline
    )

    for event in app.stream(initial, stream_mode="values"):
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
) -> dict:
    """Synchronous variant — returns the final state dict."""
    initial = _build_initial_state(
        company, industry, tenant, sync_to_notion, trigger_headline
    )
    return app.invoke(initial)
