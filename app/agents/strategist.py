"""
strategist.py — Step 2: Strategy Agent.

Reads the EnrichmentResult and picks ONE of three tenant-configured angles via
Pydantic structured output (Literal-typed enum). The angle menu, business
description, and reference customers all come from `state["tenant"]`.

Prompt caching on the dynamic system prompt — caches per-tenant, since the
prompt is identical for every company processed under a given tenant.
"""
from __future__ import annotations

import os

from langchain_anthropic import ChatAnthropic
from langchain_core.messages import HumanMessage, SystemMessage

from app.prompts import load_prompt
from app.tenants.schema import TenantConfig

from .state import (
    ANGLE_KEYS,
    BDRState,
    EnrichmentResult,
    StrategyDecision,
)

MODEL = "claude-sonnet-4-6"


def _build_system_prompt(tenant: TenantConfig) -> str:
    customers = ", ".join(tenant.business.reference_customers) or "(no reference customers configured)"
    headline = tenant.business.headline_metric or "(no headline metric configured)"

    angle_menu = "\n".join(
        f"  - {a.key} — {a.name}: {a.description}\n"
        f"    Core insight: {a.core_insight}\n"
        f"    Avoid: {a.avoid or '(none)'}"
        for a in tenant.angles
    )

    return (
        f"You are the Strategy Agent in a B2B sales workflow for {tenant.brand.name}.\n\n"
        f"Product context:\n{tenant.business.description.strip()}\n\n"
        f"Reference customers: {customers}\n"
        f"Headline proof: {headline}\n"
        f"Target persona: {tenant.persona.title}"
        f" (or: {', '.join(tenant.persona.title_alternates) if tenant.persona.title_alternates else 'no alternates'})\n\n"
        "Pick exactly ONE angle for this prospect. Be decisive — don't hedge.\n\n"
        f"Three angles:\n{angle_menu}\n\n"
        "Return your decision via the StrategyDecision schema. Be specific and "
        "grounded in the enrichment data — cite the actual signal, ICP score "
        "component, or technographic mention that drove your pick. No generic "
        "platitudes.\n\n"
        "Use evidence cards as the strongest grounding layer. Prefer observed, "
        "high-confidence cards for rationale and pain_signal. If the evidence is "
        "thin, say that plainly instead of stretching a claim.\n\n"
        "Use the account-readiness score as a transparent prioritization aid, "
        "not as a perfect truth or reply-rate predictor. If the priority is "
        "needs_more_research or do_not_send_yet, be cautious and avoid "
        "over-specific claims.\n\n"
        "The pain_signal and cpo_hypothesis you produce will feed the email writer. "
        "Write them so a human reader of the resulting email would say 'yes, that's "
        "us' — falsifiable, not boilerplate.\n\n"
        + load_prompt("cold_email")
    )


def _format_account_score(e: EnrichmentResult) -> str:
    account_score = getattr(e, "account_score", None)
    if not account_score:
        return ""

    components = [
        getattr(account_score, "icp_fit", None),
        getattr(account_score, "pain_evidence", None),
        getattr(account_score, "trigger_strength", None),
        getattr(account_score, "contact_confidence", None),
        getattr(account_score, "evidence_quality", None),
    ]
    component_lines = [
        f"  - {getattr(c, 'label', '')}: {getattr(c, 'score', 0)}/5 — {getattr(c, 'rationale', '')}"
        for c in components
        if c is not None
    ]
    warnings = getattr(account_score, "warnings", []) or []
    warning_block = "\nWarnings:\n" + "\n".join(f"  - {w}" for w in warnings) if warnings else ""
    return (
        "Transparent account-readiness score:\n"
        f"  Overall: {account_score.overall_score}/100\n"
        f"  Priority: {account_score.priority_label}\n"
        f"  Recommended action: {account_score.recommended_action}\n"
        "Components:\n"
        + "\n".join(component_lines)
        + warning_block
    )


def _format_evidence_cards(e: EnrichmentResult) -> str:
    cards = getattr(e, "evidence_cards", []) or []
    if not cards:
        return ""

    def sort_key(card: object) -> tuple[int, int]:
        label_rank = {"high": 0, "medium": 1, "low": 2}
        support_rank = {"observed": 0, "derived": 1, "inferred": 2}
        return (
            label_rank.get(getattr(card, "confidence_label", "low"), 3),
            support_rank.get(getattr(card, "support_type", "inferred"), 3),
        )

    lines: list[str] = []
    for card in sorted(cards, key=sort_key)[:6]:
        source = getattr(card, "source_title", "") or getattr(card, "source_type", "source")
        claim = getattr(card, "claim", "") or ""
        excerpt = getattr(card, "excerpt", "") or ""
        url = getattr(card, "source_url", "") or ""
        lines.append(
            f"  - [{getattr(card, 'confidence_label', 'low')}/"
            f"{getattr(card, 'support_type', 'inferred')}] {claim}\n"
            f"    Source: {source}{f' ({url})' if url else ''}\n"
            f"    Excerpt: {excerpt or '(none)'}"
        )
    return "Evidence cards:\n" + "\n".join(lines)


def _format_enrichment(e: EnrichmentResult) -> str:
    parts: list[str] = []
    account_score = _format_account_score(e)
    if account_score:
        parts.append(account_score)
    evidence = _format_evidence_cards(e)
    if evidence:
        parts.append(evidence)
    if e.icp:
        score_detail = ""
        if e.icp.score_breakdown:
            score_detail = (
                f" [score {e.icp.score}/100: "
                f"industry={e.icp.score_breakdown.get('industry_fit', 0)}, "
                f"tech={e.icp.score_breakdown.get('technographic', 0)}, "
                f"intent={e.icp.score_breakdown.get('intent_signals', 0)}, "
                f"contacts={e.icp.score_breakdown.get('contact_quality', 0)}]"
            )
        parts.append(f"ICP Tier: {e.icp.tier_label}{score_detail}\n  Why: {e.icp.rationale}")
    if e.contacts:
        parts.append(
            "Contacts on file (Hunter.io):\n"
            + "\n".join(
                f"  - {c.name or '(unknown)'} - {c.position or 'unknown'}"
                for c in e.contacts[:5]
            )
        )
    if e.research_summary:
        parts.append(f"Research summary:\n{e.research_summary}")
    if e.live_signals:
        parts.append(
            "Top live signals:\n"
            + "\n".join(f"  - {s.title}" for s in e.live_signals[:3])
        )
    if e.job_signals:
        parts.append(
            f"Open roles ({len(e.job_signals)} found):\n"
            + "\n".join(f"  - {s.title}" for s in e.job_signals[:3])
        )
    return "\n\n".join(parts) or "(no enrichment data)"


def run_strategist(state: BDRState) -> dict:
    """LangGraph node — pick the optimal angle."""
    if state.get("error"):
        return {}
    enrichment = state.get("enrichment")
    tenant = state.get("tenant")
    if not enrichment:
        return {"error": "Strategist: no enrichment data."}
    if tenant is None:
        return {"error": "Strategist: tenant config missing from state."}

    trace = list(state.get("agent_trace", []))
    trace.append(f"Strategist: scoring 3 angles against enrichment data + ICP score (tenant={tenant.tenant_id})")

    api_key = os.environ.get("ANTHROPIC_API_KEY", "")
    if not api_key:
        return {"error": "ANTHROPIC_API_KEY not set.", "agent_trace": trace}

    llm = ChatAnthropic(
        model=MODEL,
        api_key=api_key,
        max_tokens=800,
        temperature=0.3,
        extra_headers={"anthropic-beta": "prompt-caching-2024-07-31"},
    )
    structured = llm.with_structured_output(StrategyDecision)

    system_msg = SystemMessage(
        content=_build_system_prompt(tenant),
        additional_kwargs={"cache_control": {"type": "ephemeral"}},
    )

    trigger = state.get("trigger_headline", "")
    trigger_line = f"\nTop trigger headline: {trigger}" if trigger else ""

    angle_menu = "\n".join(f"- {a.key} ({a.name}): {a.description}" for a in tenant.angles)

    user = (
        f"Company: {enrichment.company}\n"
        f"Industry: {enrichment.industry or 'unknown'}{trigger_line}\n\n"
        f"Enrichment data:\n{_format_enrichment(enrichment)}\n\n"
        f"Available angles:\n{angle_menu}\n\n"
        "Pick the single best-fit angle and fill in the StrategyDecision schema. "
        "Reference the most specific evidence card or signal from the enrichment data "
        "in your rationale. Do not overstate low-confidence or derived evidence. "
        "If the account-readiness priority is needs_more_research or do_not_send_yet, "
        "state the uncertainty instead of forcing a strong outreach claim."
    )

    try:
        decision: StrategyDecision = structured.invoke(
            [system_msg, HumanMessage(content=user)]
        )
    except Exception as exc:
        return {"error": f"Strategist call failed: {exc}", "agent_trace": trace}

    # Literal type enforces valid values — guard as safety net
    if decision.recommended_angle not in ANGLE_KEYS:
        decision.recommended_angle = "angle1"
        decision.angle_name = tenant.angle_by_key("angle1").name

    trace.append(f"Strategist: chose {decision.recommended_angle} ({decision.angle_name})")
    return {"strategy": decision, "agent_trace": trace}
