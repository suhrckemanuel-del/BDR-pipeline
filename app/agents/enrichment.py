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
    AccountScoringResult,
    BDRState,
    ContactLead,
    EnrichmentResult,
    EvidenceCard,
    ICPClassification,
    LiveSignal,
    ScoreComponent,
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
# Evidence cards (deterministic trust layer)
# ---------------------------------------------------------------------------
def _meaningful(text: str, min_chars: int = 40) -> bool:
    return len((text or "").strip()) >= min_chars


def _clip(text: str, limit: int = 240) -> str:
    clean = " ".join((text or "").split())
    if len(clean) <= limit:
        return clean
    return clean[: limit - 1].rstrip() + "..."


def _signal_confidence(signal: LiveSignal) -> tuple[str, int, bool]:
    has_url = bool((signal.url or "").strip())
    has_title = _meaningful(signal.title, 12)
    has_snippet = _meaningful(signal.snippet, 50)
    if has_url and has_title and has_snippet:
        return "high", 85, True
    if (has_url and (has_title or has_snippet)) or (has_title and has_snippet):
        return "medium", 65, True
    return "low", 35, False


def _contact_confidence(contact: ContactLead) -> tuple[str, int, bool]:
    source_conf = max(0, min(100, int(contact.confidence or 0)))
    has_identity = bool(contact.email or contact.name or contact.position)
    has_source = bool(contact.linkedin_url)
    if source_conf >= 80:
        return "high", source_conf, has_identity
    if source_conf >= 50:
        return "medium", source_conf, has_identity
    if has_identity and has_source:
        return "medium", 55, True
    return "low", max(source_conf, 30 if has_identity else 20), False


def _signal_to_evidence(signal: LiveSignal, idx: int, source_type: str) -> EvidenceCard:
    label, score, safe = _signal_confidence(signal)
    title = signal.title or "Untitled source"
    excerpt = _clip(signal.snippet or signal.title, 260)
    claim = _clip(signal.title or signal.snippet, 180)
    prefix = "Live signal found" if source_type == "live_signal" else "Job signal found"
    return EvidenceCard(
        evidence_id=f"{source_type}-{idx}",
        claim=f"{prefix}: {claim}",
        source_title=title,
        source_url=signal.url or "",
        source_type=source_type,  # type: ignore[arg-type]
        support_type="observed",
        confidence_label=label,  # type: ignore[arg-type]
        confidence_score=score,
        excerpt=excerpt,
        safe_to_use=safe,
        notes="Confidence lowered when the source URL or excerpt is thin." if label == "low" else "",
    )


def _contact_to_evidence(contact: ContactLead, idx: int, domain: str) -> EvidenceCard:
    label, score, safe = _contact_confidence(contact)
    role = contact.position or "role unknown"
    identity = contact.name or contact.email or "Hunter contact"
    excerpt_bits = [
        identity,
        role,
        contact.department,
        contact.seniority.replace("_", " ").title() if contact.seniority else "",
        f"Hunter confidence {contact.confidence}" if contact.confidence else "",
    ]
    excerpt = " | ".join(bit for bit in excerpt_bits if bit)
    return EvidenceCard(
        evidence_id=f"contact-{idx}",
        claim=f"Hunter returned {identity} as {role}.",
        source_title=f"Hunter.io contact{f' at {domain}' if domain else ''}",
        source_url=contact.linkedin_url or "",
        source_type="contact",
        support_type="observed",
        confidence_label=label,  # type: ignore[arg-type]
        confidence_score=score,
        excerpt=excerpt,
        safe_to_use=safe,
        notes="Use as contact-routing evidence, not as a claim about business pain.",
    )


def _build_evidence_cards(
    signals: List[LiveSignal],
    job_signals: List[LiveSignal],
    contacts: List[ContactLead],
    icp: ICPClassification | None,
    domain: str,
    manual_trigger: str,
) -> List[EvidenceCard]:
    cards: List[EvidenceCard] = []
    if manual_trigger:
        cards.append(
            EvidenceCard(
                evidence_id="manual-trigger-1",
                claim=f"Manual trigger supplied: {_clip(manual_trigger, 180)}",
                source_title="Manual trigger",
                source_url="",
                source_type="manual_trigger",
                support_type="inferred",
                confidence_label="low",
                confidence_score=30,
                excerpt=_clip(manual_trigger, 260),
                safe_to_use=False,
                notes="No source URL was supplied, so this should be verified before outreach.",
            )
        )
    cards.extend(_signal_to_evidence(s, i + 1, "live_signal") for i, s in enumerate(signals))
    cards.extend(_signal_to_evidence(s, i + 1, "job_signal") for i, s in enumerate(job_signals))
    for i, contact in enumerate(contacts, start=1):
        if contact.email or contact.name or contact.position:
            cards.append(_contact_to_evidence(contact, i, domain))
    if icp:
        cards.append(
            EvidenceCard(
                evidence_id="icp-score-1",
                claim=f"Composite ICP score is {icp.score}/100 ({icp.tier_label}).",
                source_title="Internal ICP scoring model",
                source_url="",
                source_type="icp_score",
                support_type="derived",
                confidence_label="low",
                confidence_score=40,
                excerpt=_clip(icp.rationale or f"Score breakdown: {icp.score_breakdown}", 260),
                safe_to_use=False,
                notes="Derived internal fit score. Do not present as externally verified evidence.",
            )
        )
    return cards


# ---------------------------------------------------------------------------
# Transparent account-readiness scoring (deterministic)
# ---------------------------------------------------------------------------
PAIN_KEYWORDS = (
    "hiring", "expansion", "transformation", "migration", "efficiency",
    "cost", "manual", "operations", "workflow", "coverage", "support",
    "sales", "onboarding", "compliance", "integration", "data", "reporting",
)

TRIGGER_KEYWORDS = (
    "hiring", "expansion", "transformation", "migration", "restructur",
    "cost reduction", "efficiency program", "acquisition", "merger",
    "earnings", "layoff", "funding", "raised", "series", "launch",
    "new role", "open role",
)


def _card_text(card: EvidenceCard) -> str:
    return " ".join([card.claim, card.excerpt, card.source_title]).lower()


def _matching_cards(cards: List[EvidenceCard], keywords: tuple[str, ...]) -> List[EvidenceCard]:
    return [card for card in cards if any(k in _card_text(card) for k in keywords)]


def _observed_source_cards(cards: List[EvidenceCard]) -> List[EvidenceCard]:
    return [
        card for card in cards
        if card.support_type == "observed"
        and card.source_type in {"live_signal", "job_signal"}
    ]


def _component(label: str, score: int, rationale: str, evidence_ids: List[str] | None = None) -> ScoreComponent:
    return ScoreComponent(
        label=label,
        score=max(0, min(5, score)),
        rationale=rationale,
        evidence_ids=evidence_ids or [],
    )


def _score_icp_fit(
    icp: ICPClassification | None,
    industry: str,
    evidence_cards: List[EvidenceCard],
) -> ScoreComponent:
    if not icp:
        return _component("ICP fit", 0, "No ICP classification was available.")

    if icp.score >= 80:
        score = 5
    elif icp.score >= 65:
        score = 4
    elif icp.score >= 50:
        score = 3
    elif icp.score >= 35:
        score = 2
    elif industry or evidence_cards:
        score = 1
    else:
        score = 0

    return _component(
        "ICP fit",
        score,
        f"ICP score is {icp.score}/100 ({icp.tier_label}); this is a fit signal, not a send prediction.",
        ["icp-score-1"] if any(c.evidence_id == "icp-score-1" for c in evidence_cards) else [],
    )


def _score_pain_evidence(evidence_cards: List[EvidenceCard]) -> ScoreComponent:
    observed_cards = _observed_source_cards(evidence_cards)
    pain_cards = _matching_cards(observed_cards, PAIN_KEYWORDS)
    derived_pain = _matching_cards(
        [c for c in evidence_cards if c.support_type != "observed"],
        PAIN_KEYWORDS,
    )
    weight = sum(
        2 if c.confidence_label == "high" else 1 if c.confidence_label == "medium" else 0
        for c in pain_cards
    )

    if weight >= 6 or len(pain_cards) >= 4:
        score = 5
    elif weight >= 4 or len(pain_cards) >= 3:
        score = 4
    elif weight >= 2:
        score = 3
    elif pain_cards:
        score = 2
    elif derived_pain:
        score = 1
    else:
        score = 0

    if pain_cards:
        rationale = (
            f"{len(pain_cards)} observed source-backed card(s) contain simple pain/workflow keywords "
            f"such as hiring, operations, workflow, coverage, data, or reporting."
        )
    elif derived_pain:
        rationale = "Pain appears only in derived or inferred evidence, so it needs human verification."
    else:
        rationale = "No observed evidence card clearly points to a relevant business or workflow pain."
    return _component("Pain evidence", score, rationale, [c.evidence_id for c in pain_cards[:4]])


def _score_trigger_strength(
    evidence_cards: List[EvidenceCard],
    job_signals: List[LiveSignal],
    manual_trigger: str,
) -> ScoreComponent:
    observed_cards = _observed_source_cards(evidence_cards)
    trigger_cards = _matching_cards(observed_cards, TRIGGER_KEYWORDS)
    source_weight = sum(
        2 if c.confidence_label == "high" else 1 if c.confidence_label == "medium" else 0
        for c in trigger_cards
    )
    manual_present = bool(manual_trigger)

    if source_weight >= 6 or len(trigger_cards) >= 4:
        score = 5
    elif source_weight >= 4 or len(trigger_cards) >= 3:
        score = 4
    elif source_weight >= 2 or job_signals:
        score = 3
    elif manual_present:
        score = 2
    elif observed_cards:
        score = 1
    else:
        score = 0

    if manual_present and not trigger_cards:
        score = min(score, 2)

    if trigger_cards:
        rationale = f"{len(trigger_cards)} observed card(s) include buying-moment language or job-signal context."
    elif manual_present:
        rationale = "A manual trigger was supplied, but it is not source-backed in the evidence cards."
    elif job_signals:
        rationale = "Job signals exist, but trigger language is limited."
    else:
        rationale = "No clear source-backed timing trigger was found."
    return _component("Trigger strength", score, rationale, [c.evidence_id for c in trigger_cards[:4]])


def _score_contact_confidence(
    contacts: List[ContactLead],
    tenant: TenantConfig,
) -> ScoreComponent:
    if not contacts:
        return _component("Contact confidence", 0, "No Hunter contacts were found for this account.")

    persona_kw = _persona_keywords(tenant)
    persona_matches = [
        c for c in contacts
        if any(k in (c.position or "").lower() for k in persona_kw)
    ]
    emails = [c for c in contacts if c.email]
    high_conf = [c for c in contacts if (c.confidence or 0) >= 80]
    mid_conf = [c for c in contacts if (c.confidence or 0) >= 50]
    senior_hits = [
        c for c in contacts
        if any(
            term in " ".join([c.seniority, c.position]).lower()
            for term in ("senior", "executive", "director", "vp", "vice president", "head", "chief", "c-level")
        )
    ]

    if len(emails) >= 2 and persona_matches and high_conf:
        score = 5
    elif emails and persona_matches and (high_conf or mid_conf):
        score = 4
    elif emails and (persona_matches or senior_hits or mid_conf):
        score = 3
    elif emails or senior_hits:
        score = 2
    else:
        score = 1

    if contacts and not persona_matches:
        score = min(score, 3)

    rationale = (
        f"{len(contacts)} contact(s), {len(emails)} email(s), "
        f"{len(persona_matches)} persona-title match(es), {len(high_conf)} high-confidence Hunter record(s)."
    )
    if contacts and not persona_matches:
        rationale += " No clear persona-title match, so wrong-person risk is higher."
    return _component("Contact confidence", score, rationale)


def _score_evidence_quality(evidence_cards: List[EvidenceCard]) -> ScoreComponent:
    if not evidence_cards:
        return _component("Evidence quality", 0, "No evidence cards were created.")

    observed = [c for c in evidence_cards if c.support_type == "observed"]
    high_conf = [c for c in evidence_cards if c.confidence_label == "high"]
    source_urls = [c for c in evidence_cards if c.source_url]
    safe_cards = [c for c in evidence_cards if c.safe_to_use]

    if len(observed) >= 4 and len(high_conf) >= 2 and len(source_urls) >= 3 and len(safe_cards) >= 3:
        score = 5
    elif len(observed) >= 3 and (high_conf or len(source_urls) >= 2):
        score = 4
    elif len(observed) >= 2 and source_urls:
        score = 3
    elif observed or len(evidence_cards) >= 2:
        score = 2
    elif evidence_cards:
        score = 1
    else:
        score = 0

    rationale = (
        f"{len(evidence_cards)} total card(s): {len(observed)} observed, "
        f"{len(high_conf)} high-confidence, {len(source_urls)} with source URLs, "
        f"{len(safe_cards)} marked safe to use."
    )
    return _component("Evidence quality", score, rationale, [c.evidence_id for c in high_conf[:4]])


def _priority_label(overall: int, critical_warning: bool) -> str:
    if overall >= 75 and not critical_warning:
        return "high_priority"
    if overall >= 55:
        return "review"
    if overall >= 35:
        return "needs_more_research"
    return "do_not_send_yet"


def _recommended_action(priority: str) -> str:
    if priority == "high_priority":
        return "Review this account now; evidence and contact path are strong enough for human-approved outreach."
    if priority == "review":
        return "Review the evidence and contact path before approving outreach."
    if priority == "needs_more_research":
        return "Do more account and contact research before relying on the draft."
    return "Do not send yet; verify the account identity, source evidence, and contact path first."


def _compute_account_score(
    industry: str,
    contacts: List[ContactLead],
    job_signals: List[LiveSignal],
    evidence_cards: List[EvidenceCard],
    icp: ICPClassification | None,
    manual_trigger: str,
    tenant: TenantConfig,
) -> AccountScoringResult:
    icp_fit = _score_icp_fit(icp, industry, evidence_cards)
    pain_evidence = _score_pain_evidence(evidence_cards)
    trigger_strength = _score_trigger_strength(evidence_cards, job_signals, manual_trigger)
    contact_confidence = _score_contact_confidence(contacts, tenant)
    evidence_quality = _score_evidence_quality(evidence_cards)

    overall = round(
        (
            icp_fit.score * 25
            + pain_evidence.score * 25
            + trigger_strength.score * 20
            + contact_confidence.score * 20
            + evidence_quality.score * 10
        ) / 5
    )

    observed_cards = [c for c in evidence_cards if c.support_type == "observed"]
    high_source_cards = [
        c for c in observed_cards
        if c.confidence_label == "high" and c.source_url and c.source_type in {"live_signal", "job_signal"}
    ]
    warnings: List[str] = []
    if not high_source_cards:
        warnings.append("No high-confidence source-backed evidence found.")
    if not contacts:
        warnings.append("No contacts found; contact discovery needs manual review.")
    if manual_trigger:
        warnings.append("Manual trigger supplied without a source URL.")
    if pain_evidence.score <= 1:
        warnings.append("Pain evidence is inferred rather than observed.")
    if contact_confidence.score <= 2:
        warnings.append("Contact confidence is low; verify before sending.")
    if evidence_quality.score <= 2:
        warnings.append("Evidence is thin; gather more source-backed research before outreach.")

    critical_warning = not observed_cards or not contacts or not high_source_cards
    priority = _priority_label(overall, critical_warning)
    if critical_warning and overall < 55:
        priority = "do_not_send_yet" if overall < 35 else "needs_more_research"

    return AccountScoringResult(
        overall_score=overall,
        priority_label=priority,  # type: ignore[arg-type]
        icp_fit=icp_fit,
        pain_evidence=pain_evidence,
        trigger_strength=trigger_strength,
        contact_confidence=contact_confidence,
        evidence_quality=evidence_quality,
        recommended_action=_recommended_action(priority),
        warnings=warnings,
    )


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
    manual_trigger = (state.get("trigger_headline", "") or "").strip()

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

    evidence_cards = _build_evidence_cards(
        signals=signals,
        job_signals=job_signals,
        contacts=contacts,
        icp=icp,
        domain=domain,
        manual_trigger=manual_trigger,
    )
    high_conf = sum(1 for c in evidence_cards if c.confidence_label == "high")
    trace.append(f"Enrichment: built {len(evidence_cards)} evidence cards ({high_conf} high confidence)")

    account_score = _compute_account_score(
        industry=industry,
        contacts=contacts,
        job_signals=job_signals,
        evidence_cards=evidence_cards,
        icp=icp,
        manual_trigger=manual_trigger,
        tenant=tenant,
    )
    trace.append(
        f"Enrichment: account score {account_score.overall_score}/100 · "
        f"{account_score.priority_label}"
    )

    trigger_headline = manual_trigger
    if signals:
        trigger_headline = trigger_headline or signals[0].title or signals[0].snippet[:120]

    enrichment = EnrichmentResult(
        company=company,
        industry=industry,
        domain=domain,
        live_signals=signals,
        job_signals=job_signals,
        contacts=contacts,
        evidence_cards=evidence_cards,
        account_score=account_score,
        research_summary=summary,
        icp=icp,
    )
    return {
        "enrichment": enrichment,
        "trigger_headline": trigger_headline,
        "agent_trace": trace,
    }
