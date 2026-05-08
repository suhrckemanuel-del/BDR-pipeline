"""
enrichment.py — Step 1: Enrichment Agent.

Four live actions (Exa + Hunter run in parallel via asyncio.gather):
  1. Exa news search      -> recent news / triggers about the company
  2. Exa job-posting scan -> open roles related to the tenant's persona
  3. Hunter.io search     -> executive contacts (filtered by tenant.persona)
  4. LLM call (Haiku)     -> research summary + ICP tier + composite 0-100 score

All tenant-specific positioning (product description, persona title, ICP
criteria) comes from `state["tenant"]`. No hardcoded brand strings.

Writes EnrichmentResult into the BDRState.
"""
from __future__ import annotations

import asyncio
import os
from typing import List

import requests
from langchain_anthropic import ChatAnthropic
from langchain_core.messages import HumanMessage, SystemMessage

from app.tenants.schema import TenantConfig

from .state import (
    BDRState,
    ContactLead,
    EnrichmentResult,
    ICPClassification,
    LiveSignal,
)

HAIKU_MODEL = "claude-haiku-4-5-20251001"
SONNET_MODEL = "claude-sonnet-4-6"


# ---------------------------------------------------------------------------
# Exa query templates — built per-tenant from persona + business signals
# ---------------------------------------------------------------------------
def _build_news_query(company: str, tenant: TenantConfig) -> str:
    persona_terms = " OR ".join(
        f'"{t}"' for t in [tenant.persona.title, *tenant.persona.title_alternates]
    ) or '"strategy"'
    return f"{company} news OR earnings OR transformation OR {persona_terms}"


def _build_jobs_query(company: str, tenant: TenantConfig) -> str:
    persona_terms = " OR ".join(
        f'"{t}"' for t in [tenant.persona.title, *tenant.persona.title_alternates]
    ) or '"head of strategy"'
    return f'"{company}" {persona_terms} site:linkedin.com'


# ---------------------------------------------------------------------------
# Exa — generic web fetch
# ---------------------------------------------------------------------------
def _fetch_exa(query: str, num_results: int = 5) -> List[LiveSignal]:
    api_key = os.environ.get("EXA_API_KEY", "")
    if not api_key:
        return []
    try:
        from exa_py import Exa  # type: ignore
    except ImportError:
        return []
    try:
        exa = Exa(api_key=api_key)
        results = exa.search_and_contents(
            query,
            num_results=num_results,
            text={"max_characters": 600},
        )
    except Exception:
        try:
            exa = Exa(api_key=api_key)
            results = exa.search(query, num_results=num_results)
        except Exception:
            return []

    signals: List[LiveSignal] = []
    for r in (results.results or [])[:num_results]:
        title = (getattr(r, "title", "") or "").strip()
        url = (getattr(r, "url", "") or "").strip()
        snippet = (getattr(r, "text", "") or "").strip()
        if title or snippet:
            signals.append(LiveSignal(title=title, url=url, snippet=snippet[:600]))
    return signals


async def _async_fetch_exa(query: str, num_results: int) -> List[LiveSignal]:
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(None, _fetch_exa, query, num_results)


async def _async_fetch_hunter(
    company: str, tenant: TenantConfig
) -> tuple[str, List[ContactLead]]:
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(None, _fetch_hunter_contacts, company, tenant)


# ---------------------------------------------------------------------------
# Hunter.io — persona-aware ranking
# ---------------------------------------------------------------------------
def _persona_keywords(tenant: TenantConfig) -> tuple[str, ...]:
    """Lowercased title fragments for matching contact positions."""
    raw = [tenant.persona.title, *tenant.persona.title_alternates]
    fragments: list[str] = []
    for r in raw:
        # Split on common connectors, keep tokens of length >= 4
        for tok in r.lower().replace("/", " ").split():
            if len(tok) >= 4 and tok not in {"chief", "head", "vice", "president"}:
                fragments.append(tok)
    return tuple(sorted(set(fragments))) or ("strategy",)


def _fetch_hunter_contacts(
    company: str, tenant: TenantConfig, limit: int = 10
) -> tuple[str, List[ContactLead]]:
    """Returns (resolved_domain, persona-prioritized contacts)."""
    api_key = os.environ.get("HUNTER_API_KEY", "")
    if not api_key:
        return "", []

    seniority_filter = ",".join(tenant.persona.seniority_filter) or "senior,executive"

    try:
        resp = requests.get(
            "https://api.hunter.io/v2/domain-search",
            params={
                "company": company,
                "seniority": seniority_filter,
                "limit": limit,
                "api_key": api_key,
            },
            timeout=15,
        )
    except requests.RequestException:
        return "", []

    if resp.status_code != 200:
        return "", []

    data = (resp.json() or {}).get("data") or {}
    domain = (data.get("domain") or "").strip()
    emails = data.get("emails") or []

    persona_kw = _persona_keywords(tenant)
    contacts: List[ContactLead] = []
    persona_hits: List[ContactLead] = []
    for e in emails:
        position = (e.get("position") or "").strip()
        first = (e.get("first_name") or "").strip()
        last = (e.get("last_name") or "").strip()
        full = f"{first} {last}".strip()
        linkedin_url = (e.get("linkedin_url") or "").strip()
        lead = ContactLead(
            name=full,
            email=(e.get("value") or "").strip(),
            position=position,
            seniority=(e.get("seniority") or "").strip(),
            department=(e.get("department") or "").strip(),
            confidence=int(e.get("confidence") or 0),
            linkedin_url=linkedin_url,
        )
        contacts.append(lead)
        if any(k in position.lower() for k in persona_kw):
            persona_hits.append(lead)

    if persona_hits:
        return domain, persona_hits[:5]
    contacts.sort(key=lambda c: c.confidence, reverse=True)
    return domain, contacts[:5]


# ---------------------------------------------------------------------------
# Composite ICP score (deterministic — no LLM)
# ---------------------------------------------------------------------------
def _compute_icp_score(
    industry: str,
    signals: List[LiveSignal],
    job_signals: List[LiveSignal],
    contacts: List[ContactLead],
    tenant: TenantConfig,
) -> tuple[int, dict]:
    """
    Generic 0-100 composite score:
      - Industry fit (0-30):      Match against tenant business description keywords
      - Technographic fit (0-30): Reference customers and persona keywords in signals
      - Intent signals (0-25):    Hiring, transformation, earnings keywords
      - Contact quality (0-15):   Persona-title matches from Hunter

    The industry/tech fits are deliberately tenant-agnostic — tenants who want
    finer-grained scoring can hand-tune their ICP definition; this is the
    floor. The score is a hint, not a verdict.
    """
    score = 0
    breakdown: dict = {}

    combined_text = " ".join(s.snippet + " " + s.title for s in signals).lower()
    business_text = (tenant.business.description or "").lower()
    business_tokens = {tok for tok in business_text.split() if len(tok) >= 5}

    # --- Industry fit (30 pts): overlap between tenant description and target industry ---
    ind_lower = industry.lower()
    ind_tokens = {tok for tok in ind_lower.split() if len(tok) >= 4}
    overlap = len(business_tokens & ind_tokens)
    if overlap >= 3:
        industry_score = 30
    elif overlap >= 1:
        industry_score = 20
    elif ind_lower:
        industry_score = 10
    else:
        industry_score = 5
    breakdown["industry_fit"] = industry_score
    score += industry_score

    # --- Technographic fit (30 pts): reference customers or persona keywords appear in signals ---
    tech_score = 5
    for cust in tenant.business.reference_customers:
        if cust.lower() in combined_text:
            tech_score = max(tech_score, 25)
            break
    persona_kw = _persona_keywords(tenant)
    if any(k in combined_text for k in persona_kw):
        tech_score = max(tech_score, 15)
    breakdown["technographic"] = tech_score
    score += tech_score

    # --- Intent signals (25 pts) — generic buying-signal keywords ---
    intent_keywords = [
        "transformation", "restructur", "cost reduction", "efficiency program",
        "hiring", "series", "raised", "acquisition", "merger", "earnings",
        "layoff", "expansion", "ipo",
    ]
    intent_score = min(25, sum(5 for k in intent_keywords if k in combined_text))
    if job_signals:
        intent_score = min(25, intent_score + len(job_signals) * 4)
    breakdown["intent_signals"] = intent_score
    score += intent_score

    # --- Contact quality (15 pts): persona-title matches ---
    contact_score = min(15, sum(
        5 for c in contacts
        if any(t in (c.position or "").lower() for t in persona_kw)
    ))
    breakdown["contact_quality"] = contact_score
    score += contact_score

    return min(100, score), breakdown


def _tier_from_score(score: int, tenant: TenantConfig) -> tuple[int, str]:
    if score >= 65:
        return 1, tenant.icp.tier1_label
    elif score >= 40:
        return 2, tenant.icp.tier2_label
    else:
        return 3, tenant.icp.tier3_label


# ---------------------------------------------------------------------------
# ICP Tier classification via Haiku (structured output) — tenant-aware prompt
# ---------------------------------------------------------------------------
def _build_icp_system_prompt(tenant: TenantConfig) -> str:
    icp_def = tenant.icp_definition or tenant.icp.tier_criteria or "(no ICP definition supplied)"
    return (
        f"You are the ICP-tier classifier for {tenant.brand.name}.\n\n"
        f"Product context:\n{tenant.business.description.strip()}\n\n"
        f"ICP definition:\n{icp_def}\n\n"
        f"Tier labels:\n"
        f"  - Tier 1: {tenant.icp.tier1_label}\n"
        f"  - Tier 2: {tenant.icp.tier2_label}\n"
        f"  - Tier 3: {tenant.icp.tier3_label}\n\n"
        "Use any clues in the research summary (regions, divisions, revenue, "
        "headcount, technographic signals) to make your call. Be decisive — "
        "pick exactly one tier."
    )


def _classify_icp(
    company: str,
    industry: str,
    summary: str,
    score: int,
    score_breakdown: dict,
    tenant: TenantConfig,
) -> ICPClassification:
    api_key = os.environ.get("ANTHROPIC_API_KEY", "")
    tier, tier_label = _tier_from_score(score, tenant)

    if not api_key:
        return ICPClassification(
            tier=tier,
            tier_label=f"{tier_label} (no API key)",
            rationale="ANTHROPIC_API_KEY missing — tier derived from composite score.",
            score=score,
            score_breakdown=score_breakdown,
        )

    llm = ChatAnthropic(
        model=HAIKU_MODEL,
        api_key=api_key,
        max_tokens=300,
        temperature=0.0,
        extra_headers={"anthropic-beta": "prompt-caching-2024-07-31"},
    )
    structured = llm.with_structured_output(ICPClassification)

    system_msg = SystemMessage(
        content=_build_icp_system_prompt(tenant),
        additional_kwargs={"cache_control": {"type": "ephemeral"}},
    )
    user = (
        f"Company: {company}\n"
        f"Industry: {industry or 'unknown'}\n"
        f"Composite ICP score: {score}/100 "
        f"(industry_fit={score_breakdown.get('industry_fit', 0)}, "
        f"technographic={score_breakdown.get('technographic', 0)}, "
        f"intent={score_breakdown.get('intent_signals', 0)}, "
        f"contacts={score_breakdown.get('contact_quality', 0)})\n\n"
        f"Research summary:\n{summary or '(no signals available)'}\n\n"
        f"Classify this account into Tier 1, 2, or 3 for {tenant.brand.name}."
    )
    try:
        result: ICPClassification = structured.invoke(
            [system_msg, HumanMessage(content=user)]
        )
        result.score = score
        result.score_breakdown = score_breakdown
        if result.tier not in (1, 2, 3):
            result.tier = tier
        if not result.tier_label:
            result.tier_label = tier_label
    except Exception:
        result = ICPClassification(
            tier=tier,
            tier_label=tier_label,
            rationale="LLM classification failed — derived from composite score.",
            score=score,
            score_breakdown=score_breakdown,
        )
    return result


# ---------------------------------------------------------------------------
# Research summary (persona lens) — Haiku
# ---------------------------------------------------------------------------
def _build_research_system_prompt(tenant: TenantConfig) -> str:
    return (
        f"You are the Research Agent in a B2B sales workflow for {tenant.brand.name}.\n\n"
        f"Product context:\n{tenant.business.description.strip()}\n\n"
        f"Read the provided live signals and contact list. Summarise what matters "
        f"specifically to a {tenant.persona.title} at this company:\n"
        "  - What is changing in their operating model, team, or strategy?\n"
        "  - What time / cost / coverage pressures sit on them today?\n"
        "  - Anything in the public signals hinting at disruption to their function?\n"
        "  - Any open roles that signal headcount/coverage gaps in their org?\n\n"
        "3-5 short bullets. No marketing language. If signals are thin, say so plainly."
    )


def _summarise(
    company: str,
    industry: str,
    signals: List[LiveSignal],
    job_signals: List[LiveSignal],
    contacts: List[ContactLead],
    tenant: TenantConfig,
) -> str:
    api_key = os.environ.get("ANTHROPIC_API_KEY", "")
    if not api_key:
        return "ANTHROPIC_API_KEY missing — no LLM summary available."

    sig_block = "\n\n".join(
        f"[{i+1}] {s.title}\n  URL: {s.url}\n  {s.snippet}"
        for i, s in enumerate(signals)
    ) or "(no signals)"

    job_block = (
        "\n".join(f"- {s.title}: {s.snippet[:200]}" for s in job_signals)
        if job_signals else "(no job postings found)"
    )

    contact_block = "\n".join(
        f"- {c.name or '(no name)'} — {c.position or 'unknown role'} ({c.email or 'no email'})"
        for c in contacts
    ) or "(no contacts found)"

    user = (
        f"Company: {company}\n"
        f"Industry: {industry or 'unknown'}\n\n"
        f"Live signals (Exa):\n{sig_block}\n\n"
        f"Open roles (Exa job scan):\n{job_block}\n\n"
        f"Contacts (Hunter.io):\n{contact_block}\n\n"
        f"Summarise through the {tenant.persona.title} lens."
    )
    llm = ChatAnthropic(
        model=HAIKU_MODEL,
        api_key=api_key,
        max_tokens=500,
        temperature=0.2,
        extra_headers={"anthropic-beta": "prompt-caching-2024-07-31"},
    )
    system_msg = SystemMessage(
        content=_build_research_system_prompt(tenant),
        additional_kwargs={"cache_control": {"type": "ephemeral"}},
    )
    try:
        resp = llm.invoke([system_msg, HumanMessage(content=user)])
    except Exception as exc:
        return f"(summary failed: {exc})"
    content = resp.content
    if isinstance(content, str):
        return content.strip()
    return "".join(c.get("text", "") for c in content if isinstance(c, dict)).strip()


# ---------------------------------------------------------------------------
# LangGraph node — async with parallel Exa + Hunter
# ---------------------------------------------------------------------------
async def _run_enrichment_async(company: str, tenant: TenantConfig) -> dict:
    news_q = _build_news_query(company, tenant)
    jobs_q = _build_jobs_query(company, tenant)

    news_task = _async_fetch_exa(news_q, 5)
    jobs_task = _async_fetch_exa(jobs_q, 3)
    hunter_task = _async_fetch_hunter(company, tenant)

    signals, job_signals, (domain, contacts) = await asyncio.gather(
        news_task, jobs_task, hunter_task
    )
    return {
        "signals": signals,
        "job_signals": job_signals,
        "domain": domain,
        "contacts": contacts,
    }


def run_enrichment(state: BDRState) -> dict:
    """LangGraph node — parallel Exa + Hunter → composite ICP score → research summary."""
    company = state.get("company", "").strip()
    industry = state.get("industry", "").strip()
    tenant = state.get("tenant")
    trace = list(state.get("agent_trace", []))

    if not company:
        return {"error": "Enrichment: company name is required.", "agent_trace": trace}
    if tenant is None:
        return {"error": "Enrichment: tenant config missing from state.", "agent_trace": trace}

    trace.append("Enrichment: fetching Exa signals + Hunter.io contacts in parallel")

    try:
        loop = asyncio.new_event_loop()
        io_result = loop.run_until_complete(_run_enrichment_async(company, tenant))
        loop.close()
    except Exception as exc:
        trace.append(f"Enrichment: parallel fetch failed ({exc}), falling back to sequential")
        signals = _fetch_exa(_build_news_query(company, tenant))
        job_signals: List[LiveSignal] = []
        domain, contacts = _fetch_hunter_contacts(company, tenant)
        io_result = {"signals": signals, "job_signals": job_signals, "domain": domain, "contacts": contacts}

    signals = io_result["signals"]
    job_signals = io_result["job_signals"]
    domain = io_result["domain"]
    contacts = io_result["contacts"]

    trace.append(
        f"Enrichment: {len(signals)} news signals · {len(job_signals)} job signals · "
        f"{len(contacts)} Hunter contacts" + (f" @ {domain}" if domain else "")
    )

    trace.append("Enrichment: computing composite ICP score (0-100)")
    icp_score, score_breakdown = _compute_icp_score(industry, signals, job_signals, contacts, tenant)
    trace.append(
        f"Enrichment: ICP score {icp_score}/100 "
        f"(industry={score_breakdown.get('industry_fit', 0)}, "
        f"tech={score_breakdown.get('technographic', 0)}, "
        f"intent={score_breakdown.get('intent_signals', 0)}, "
        f"contacts={score_breakdown.get('contact_quality', 0)})"
    )

    trace.append("Enrichment: synthesising research summary (Haiku)")
    summary = _summarise(company, industry, signals, job_signals, contacts, tenant)

    trace.append("Enrichment: classifying ICP tier (Haiku)")
    icp = _classify_icp(company, industry, summary, icp_score, score_breakdown, tenant)
    trace.append(f"Enrichment: {icp.tier_label} · score {icp_score}/100")

    trigger_headline = ""
    if signals:
        trigger_headline = signals[0].title or signals[0].snippet[:120]

    enrichment = EnrichmentResult(
        company=company,
        industry=industry,
        domain=domain,
        live_signals=signals,
        job_signals=job_signals,
        contacts=contacts,
        research_summary=summary,
        icp=icp,
    )
    return {
        "enrichment": enrichment,
        "trigger_headline": trigger_headline,
        "agent_trace": trace,
    }
