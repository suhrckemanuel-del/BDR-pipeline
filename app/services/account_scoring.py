"""
account_scoring.py — Signal-weighted composite account scoring (deterministic).

Turns enrichment output (evidence cards, contacts, job signals, ICP
classification) into a transparent 0-100 account-readiness score with five
0-5 components:

  icp_fit             — how well the account matches the tenant ICP
  pain_evidence       — observed source-backed pain/workflow language
  trigger_strength    — buying-moment / timing language in observed sources
  contact_confidence  — persona-title matches and Hunter record quality
  evidence_quality    — breadth and sourcing quality of the evidence cards

Component weights live in tenant config (`icp.scoring_weights`) and default to
the historical hardcoded split (25/25/20/20/10), so tenants without the field
score exactly as before. The composite arithmetic is pure and offline —
`expected_overall()` is the single place the weighted sum happens, and
`pipeline_evals` re-runs it as an eval gate on every pipeline execution.

No LLM calls and no network. This module must never import
`app.agents.enrichment` (enrichment imports from here).
"""
from __future__ import annotations

from typing import List, Mapping

from app.agents.state import (
    AccountScoringResult,
    ContactLead,
    EvidenceCard,
    ICPClassification,
    LiveSignal,
    ScoreComponent,
)
from app.tenants.schema import ScoringWeights, TenantConfig

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

DEFAULT_WEIGHTS = ScoringWeights()


def persona_keywords(tenant: TenantConfig) -> tuple[str, ...]:
    """Lowercased title fragments for matching contact positions."""
    raw = [tenant.persona.title, *tenant.persona.title_alternates]
    fragments: list[str] = []
    for r in raw:
        # Split on common connectors, keep tokens of length >= 4
        for tok in r.lower().replace("/", " ").split():
            if len(tok) >= 4 and tok not in {"chief", "head", "vice", "president"}:
                fragments.append(tok)
    return tuple(sorted(set(fragments))) or ("strategy",)


def effective_weights(tenant: TenantConfig | None) -> ScoringWeights:
    """Tenant-configured weights when present, else the historical defaults."""
    weights = getattr(getattr(tenant, "icp", None), "scoring_weights", None)
    return weights if isinstance(weights, ScoringWeights) else DEFAULT_WEIGHTS


def expected_overall(components: Mapping[str, int], weights: ScoringWeights) -> int:
    """
    Deterministic composite: weighted mean of 0-5 component scores scaled to
    0-100. With the default weights this reduces exactly to the historical
    (icp*25 + pain*25 + trigger*20 + contact*20 + evidence*10) / 5 formula.
    """
    weight_map = weights.as_map()
    total_weight = sum(weight_map.values())
    weighted = sum(
        components.get(name, 0) * weight for name, weight in weight_map.items()
    )
    return round(100 * weighted / (5 * total_weight))


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

    persona_kw = persona_keywords(tenant)
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


def compute_account_score(
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

    overall = expected_overall(
        {
            "icp_fit": icp_fit.score,
            "pain_evidence": pain_evidence.score,
            "trigger_strength": trigger_strength.score,
            "contact_confidence": contact_confidence.score,
            "evidence_quality": evidence_quality.score,
        },
        effective_weights(tenant),
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
