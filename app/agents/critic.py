"""
critic.py — Quality-gate node for the BDR pipeline.

Multi-agent critique council: `tenant.critic.council_size` independent scorer
calls (parallel, model/temperature-diverse) each score every touch on four
dimensions. Scores are aggregated per-dimension by MEDIAN (robust against a
single biased rater), overall quality by mean, and any dimension where scorers
differ by >=2 is surfaced as a disagreement for the gate and the UI.

Flow inside this node:

    1. Council scoring  -> aggregated SequenceCritique
    2. Rewrites         -> failing first paragraphs rewritten (one call per touch,
                           all failing dimensions merged into a single prompt)
    3. Quality gate     -> judges the FINAL post-rewrite sequence (so the verdict
                           always describes the copy the founder actually sees)

Fixed-bank content (proof points, CTAs, subjects) is never touched — only the
LLM-generated first paragraph (the observation paragraph) may be rewritten.

Tenant-aware: brand name, persona, and product context come from
`state["tenant"]`. Critic prompts are built per-tenant. `council_size: 1`
reproduces the original single-rater behavior exactly.
"""
from __future__ import annotations

import logging
import os
import statistics
from concurrent.futures import ThreadPoolExecutor, as_completed
from copy import deepcopy
from typing import Literal

from langchain_anthropic import ChatAnthropic
from langchain_core.messages import HumanMessage, SystemMessage
from pydantic import BaseModel, Field, model_validator

from app.services.humanizer_rules import humanize
from app.services.model_router import build_client, cached_system_message
from app.services.token_accounting import UsageTracker, capture_usage
from app.tenants.schema import CriticConfig, TenantConfig

from .state import BDRState, ProspectCard, SequenceTouch

logger = logging.getLogger(__name__)

DEFAULT_MODEL = "claude-sonnet-4-6"
FALLBACK_MODEL = "claude-haiku-4-5-20251001"


# ---------------------------------------------------------------------------
# Pydantic schemas
# ---------------------------------------------------------------------------

class TouchScore(BaseModel):
    """Four-dimension scoring with per-dimension critique."""
    touch_number: int
    pain_specificity: int = Field(default=3, ge=1, le=5, description="How concrete is the pain reference for THIS company?")
    proof_relevance: int = Field(default=3, ge=1, le=5, description="How well does the proof point map to the pain?")
    cta_clarity: int = Field(default=3, ge=1, le=5, description="Is the ask clear, low-friction, single?")
    human_voice: int = Field(default=3, ge=1, le=5, description="Does it sound like a real person, no buzzwords?")
    average: float = 0.0
    feedback: str = ""
    pain_critique: str = ""
    proof_critique: str = ""
    cta_critique: str = ""
    voice_critique: str = ""
    needs_rewrite: bool = False
    failing_dims: list[str] = Field(default_factory=list)
    rewrite_attempts: int = 0

    @model_validator(mode="after")
    def _compute_average(self) -> "TouchScore":
        dims = [self.pain_specificity, self.proof_relevance, self.cta_clarity, self.human_voice]
        self.average = round(sum(dims) / len(dims), 2)
        self.failing_dims = [
            name for name, val in (
                ("pain_specificity", self.pain_specificity),
                ("proof_relevance", self.proof_relevance),
                ("cta_clarity", self.cta_clarity),
                ("human_voice", self.human_voice),
            ) if val < 3
        ]
        self.needs_rewrite = bool(self.failing_dims)
        return self


class SequenceCritique(BaseModel):
    touch_scores: list[TouchScore]
    overall_quality: float
    critique_summary: str


class RiskFlag(BaseModel):
    risk_type: Literal[
        "unsupported_claim",
        "overclaiming",
        "generic_copy",
        "weak_personalization",
        "wrong_person_risk",
        "thin_evidence",
        "contact_confidence",
        "deliverability_language",
        "unclear_cta",
        "tone_issue",
    ]
    severity: Literal["low", "medium", "high"]
    touch_number: int | None = None
    text_excerpt: str = ""
    rationale: str = ""
    recommended_fix: str = ""
    evidence_ids: list[str] = Field(default_factory=list)


class QualityGate(BaseModel):
    verdict: Literal["approved", "needs_edit", "needs_more_research", "do_not_send_yet"]
    safe_to_send: bool
    confidence: Literal["high", "medium", "low"]
    summary: str
    required_edits: list[str] = Field(default_factory=list)
    risk_flags: list[RiskFlag] = Field(default_factory=list)
    unsupported_claim_count: int = Field(default=0, ge=0)
    evidence_coverage_note: str = ""


class CriticResult(BaseModel):
    touch_scores: list[TouchScore] = Field(default_factory=list)
    overall_quality: float = 0.0
    rewrites_applied: int = 0
    critique_summary: str = ""
    quality_gate: QualityGate | None = None
    # Council extensions (additive; defaults keep old serialized results valid)
    council_size: int = 1
    scorer_overall_scores: list[float] = Field(default_factory=list)
    disagreements: list[str] = Field(default_factory=list)
    agreement_level: Literal["high", "medium", "low", "single_rater"] = "single_rater"


# ---------------------------------------------------------------------------
# Council helpers
# ---------------------------------------------------------------------------

DIMENSION_FIELDS = {
    "pain_specificity": ("pain_specificity", "pain_critique"),
    "proof_relevance": ("proof_relevance", "proof_critique"),
    "cta_clarity": ("cta_clarity", "cta_critique"),
    "human_voice": ("human_voice", "voice_critique"),
}


def judge_disagreement_level(disagreements: list[str]) -> Literal["high", "medium", "low", "single_rater"]:
    """Map a disagreement list to a coarse UI label. Pure function."""
    if not disagreements:
        return "high"
    if len(disagreements) == 1:
        return "medium"
    return "low"


def needs_council_escalation(
    critique: "SequenceCritique",
    critic_cfg: "CriticConfig",
) -> tuple[bool, list[str]]:
    """Cascade decision (B4): does this single-rater result need the panel?

    Pure function over the single rater's critique. Escalation triggers
    (any one suffices):
      - the rater's mean overall quality falls in the configured borderline
        band [cascade_borderline_low, cascade_borderline_high] — a perfect
        5.0 never escalates on its own
      - any touch needs a rewrite (failing dimensions present)

    Returns (escalate, reasons). reasons are human-readable trace lines.
    """
    reasons: list[str] = []
    mean = float(critique.overall_quality)
    low = critic_cfg.cascade_borderline_low
    high = critic_cfg.cascade_borderline_high
    if low <= mean <= high and mean < 5.0:
        reasons.append(f"borderline score {mean:.1f} in [{low}, {high}]")
    failing = [ts.touch_number for ts in critique.touch_scores if ts.needs_rewrite]
    if failing:
        reasons.append(f"touches need rewrite: {failing}")
    return (bool(reasons), reasons)


def _aggregate(
    critiques: list[SequenceCritique],
) -> tuple[SequenceCritique, list[float], list[str]]:
    """
    Merge independent scorer critiques into one. Pure function.

    Per-touch, per-dimension: MEDIAN score (robust against one biased rater).
    Critique strings: first non-empty critique among scorers (they describe the
    same weakness; medians fix the numbers, prose needs no averaging).
    A dimension whose scorer scores span >= 2 points is flagged as a
    disagreement ("T{n}.{dim}: {scores}").

    Returns (aggregated critique, scorer overall scores, disagreement strings).
    """
    if not critiques:
        raise ValueError("aggregate requires at least one critique")
    if len(critiques) == 1:
        sole = critiques[0]
        return sole, [sole.overall_quality], []

    by_touch: dict[int, list[TouchScore]] = {}
    for c in critiques:
        for ts in c.touch_scores:
            by_touch.setdefault(ts.touch_number, []).append(ts)

    aggregated_touches: list[TouchScore] = []
    disagreements: list[str] = []
    for touch_number in sorted(by_touch):
        scores = by_touch[touch_number]

        # Compute all four medians first, then build the final TouchScore once.
        medians: dict[str, int] = {}
        for field_name, _ in DIMENSION_FIELDS.values():
            values = [int(getattr(ts, field_name)) for ts in scores]
            medians[field_name] = int(statistics.median(values))
            if max(values) - min(values) >= 2:
                disagreements.append(
                    f"T{touch_number}.{field_name}: scorers {values}"
                )

        critiques_for_dim: dict[str, str] = {}
        for field_name, critique_field in DIMENSION_FIELDS.values():
            best = ""
            for ts in scores:
                candidate = (getattr(ts, critique_field) or "").strip()
                if candidate:
                    best = candidate
                    break
            critiques_for_dim[field_name] = best

        feedback = ""
        for ts in scores:
            if (ts.feedback or "").strip():
                feedback = ts.feedback.strip()
                break

        aggregated_touches.append(
            TouchScore(
                touch_number=touch_number,
                pain_specificity=medians["pain_specificity"],
                proof_relevance=medians["proof_relevance"],
                cta_clarity=medians["cta_clarity"],
                human_voice=medians["human_voice"],
                feedback=feedback,
                pain_critique=critiques_for_dim["pain_specificity"],
                proof_critique=critiques_for_dim["proof_relevance"],
                cta_critique=critiques_for_dim["cta_clarity"],
                voice_critique=critiques_for_dim["human_voice"],
            )
        )

    overall = round(sum(c.overall_quality for c in critiques) / len(critiques), 2)
    summary = ""
    for c in critiques:
        if (c.critique_summary or "").strip():
            summary = c.critique_summary.strip()
            break
    if disagreements:
        summary = (summary + " ").strip() + (
            f"Scorer disagreement on {len(disagreements)} dimension(s); medians used."
        )

    aggregated = SequenceCritique(
        touch_scores=aggregated_touches,
        overall_quality=overall,
        critique_summary=summary,
    )
    scorer_overalls = [round(c.overall_quality, 2) for c in critiques]
    return aggregated, scorer_overalls, disagreements


def _model_for_scorer(critic_cfg: CriticConfig, idx: int) -> str:
    models = critic_cfg.council_models or [DEFAULT_MODEL]
    if not models:
        models = [DEFAULT_MODEL]
    return models[idx % len(models)]


def _temperature_for_scorer(idx: int) -> float:
    # Slight per-scorer variation adds rater diversity on identical models.
    return round(0.2 + 0.1 * (idx % 3), 2)


def _score_with_council(
    touches: list[SequenceTouch],
    company: str,
    evidence_context: str,
    tenant: TenantConfig,
    critic_cfg: CriticConfig,
    api_key: str,
    tracker: "UsageTracker | None" = None,
    seed_critique: "SequenceCritique | None" = None,
) -> tuple[SequenceCritique | None, list[float], list[str], list[str]]:
    """
    Run independent council scorers in parallel, then aggregate.

    Scorers that fail are skipped; if none succeed, returns (None, [], [], []).
    Also returns per-scorer notes about failures for the trace.

    B4 cascade: when ``seed_critique`` is provided (the cascade's single-rater
    result), it joins the panel as scorer 0 and live scorers run for indexes
    1..size-1 — the already-spent single-rater call is never repeated.
    """
    size = max(1, critic_cfg.council_size)
    start_idx = 0
    if seed_critique is not None:
        start_idx = 1  # index 0's call already happened in the cascade probe

    scorer_route = tenant.models.route_for("gate")
    scorer_route = scorer_route.model_copy(update={"node": "gate"})

    def score_one(idx: int) -> SequenceCritique:
        scorer_kwargs: dict = {"callbacks": [tracker]} if tracker is not None else {}
        # Anthropic scorers may be model-mixed via critic.council_models (rater
        # diversity); non-Anthropic providers use the route's model for all
        # scorers (their model ids are provider-specific already).
        if scorer_route.provider == "anthropic":
            route = scorer_route.model_copy(
                update={"model": _model_for_scorer(critic_cfg, idx)}
            )
        else:
            route = scorer_route
        llm = build_client(
            route,
            api_key=api_key,
            max_tokens=4000,
            temperature=_temperature_for_scorer(idx),
            extra_kwargs=scorer_kwargs,
        )
        critic_llm = llm.with_structured_output(SequenceCritique)
        return critic_llm.invoke(
            [
                cached_system_message(route, _build_critic_system(tenant)),
                HumanMessage(content=_build_critic_human_message(touches, company, evidence_context)),
            ]
        )

    critiques: list[SequenceCritique] = []
    scorer_notes: list[str] = []
    if seed_critique is not None:
        critiques.append(seed_critique)
    with ThreadPoolExecutor(max_workers=min(max(size - start_idx, 1), 4)) as pool:
        futures = {pool.submit(score_one, i): i for i in range(start_idx, size)}
        for future in as_completed(futures):
            idx = futures[future]
            try:
                critiques.append(future.result())
            except Exception as exc:  # noqa: BLE001
                logger.warning("Critic: scorer %d/%d failed: %s", idx + 1, size, exc)
                scorer_notes.append(f"scorer {idx + 1}/{size} failed ({type(exc).__name__})")

    if not critiques:
        return None, [], [], scorer_notes

    critiques.sort(key=lambda c: c.overall_quality)  # deterministic order
    aggregated, scorer_overalls, disagreements = _aggregate(critiques)
    return aggregated, scorer_overalls, disagreements, scorer_notes


def _build_panel_context(
    scorer_overall_scores: list[float],
    disagreements: list[str],
) -> str:
    """Render council panel context for the quality-gate prompt."""
    if not scorer_overall_scores:
        return ""
    if len(scorer_overall_scores) == 1:
        return f"Review panel: single scorer (overall {scorer_overall_scores[0]:.1f}/5)."
    spread = max(scorer_overall_scores) - min(scorer_overall_scores)
    lines = [
        "Review panel:",
        f"- {len(scorer_overall_scores)} independent scorers, overall quality: "
        + ", ".join(f"{s:.1f}" for s in scorer_overall_scores)
        + f" (spread {spread:.1f}).",
    ]
    if disagreements:
        lines.append(
            "- Scorers disagreed (medians used): " + "; ".join(disagreements[:6])
        )
    else:
        lines.append("- Scorers agreed on every dimension.")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# System prompts (tenant-aware)
# ---------------------------------------------------------------------------

def _build_critic_system(tenant: TenantConfig) -> str:
    return (
        f"You are a senior B2B sales coach reviewing a cold outreach sequence "
        f"for {tenant.brand.name}.\n\n"
        f"Product context: {tenant.business.description.strip()}\n"
        f"Target persona: {tenant.persona.title}\n\n"
        "You are one of several independent scorers on a review panel; score "
        "strictly on the merits of the copy in front of you.\n\n"
        "You score each touch on FOUR dimensions (1–5 scale each):\n\n"
        "PAIN_SPECIFICITY\n"
        "  5 = references a concrete, falsifiable pain unique to THIS company\n"
        "  3 = pain is plausible but generic for the segment\n"
        "  1 = no pain reference, or pain that applies to any company\n\n"
        "PROOF_RELEVANCE\n"
        "  5 = the proof point/customer reference clearly maps to the named pain\n"
        "  3 = proof is real but only loosely related to the pain\n"
        "  1 = proof is missing, generic, or mismatched\n\n"
        "CTA_CLARITY\n"
        "  5 = one clear, low-friction ask (15-min call, Loom, or direct reply)\n"
        "  3 = ask is present but slightly vague or buried\n"
        "  1 = vague, missing, or multiple competing asks\n\n"
        "HUMAN_VOICE\n"
        "  5 = sounds like a real person wrote it, no buzzwords, natural rhythm\n"
        "  3 = mostly natural but a few stiff or corporate phrases\n"
        "  1 = clearly AI-generated copy, heavy buzzwords or formulaic structure\n\n"
        "Scoring rules:\n"
        "  - DO NOT penalise short emails — brevity is a feature, not a flaw.\n"
        "  - DO NOT penalise absence of company news when none was available.\n"
        "  - LinkedIn touches (channel=\"linkedin\") are short by design; judge on a\n"
        "    40–60 word standard — a tight 45-word DM should score 4–5 if it is\n"
        "    specific and ends with a genuine question.\n"
        "  - Break-up emails should score high on human_voice if they are direct,\n"
        "    respectful, and offer a free resource with no hard pitch.\n\n"
        "Score EVERY touch supplied in the user message, in order. The structured-\n"
        "output schema defines the exact fields to return — fill every required\n"
        "field (touch_scores list, overall_quality, critique_summary); do not add\n"
        "prose, JSON, or commentary outside it.\n"
    )


def _build_quality_gate_system(tenant: TenantConfig) -> str:
    return (
        f"You are the Quality + Risk Gate for a founder-safe AI BDR workflow for {tenant.brand.name}.\n\n"
        f"Product context: {tenant.business.description.strip()}\n"
        f"Target persona: {tenant.persona.title}\n\n"
        "You evaluate whether the sequence is safe and evidence-backed enough for a human founder to review.\n\n"
        "Assess copy quality and risk:\n"
        "  - Is each specific company claim supported by the evidence cards?\n"
        "  - Is the copy overclaiming outcomes, reply rates, revenue, meetings, or certainty?\n"
        "  - Is personalization more specific than the evidence allows?\n"
        "  - Is recipient/contact confidence weak?\n"
        "  - Is the account-readiness score low enough to require more research?\n"
        "  - Is the CTA clear and low-friction?\n"
        "  - Does the language feel spammy or AI-generated?\n\n"
        "Rules:\n"
        "  - Evidence cards are the only allowed support set. Do not invent evidence.\n"
        "  - If a claim is plausible but not sourced, flag it as unsupported or inferred.\n"
        "  - If evidence is thin, use needs_more_research or needs_edit.\n"
        "  - If account_score.priority_label is do_not_send_yet, the verdict should normally be do_not_send_yet.\n"
        "  - If account_score.priority_label is needs_more_research, do not return approved unless risks are clearly low.\n"
        "  - The sequence you review is the FINAL rewritten version. Judge it as-is.\n"
        "  - Do not claim the gate guarantees deliverability, reply quality, or safety for auto-send.\n"
        "  - Human review is still required before sending.\n\n"
        "Fill the structured-output schema exactly as defined; keep the summary and\n"
        "fixes concise, specific, and founder-friendly."
    )


def _build_rewriter_system(tenant: TenantConfig) -> str:
    return (
        f"You are a B2B cold-email rewriter for {tenant.brand.name}.\n\n"
        "You receive:\n"
        "  - The original outreach email body\n"
        "  - One or more failing dimensions, each with its critique and rewrite instruction\n"
        "  - The allowed evidence context\n\n"
        "Your job: produce a replacement first paragraph (1–2 sentences) that fixes "
        "ALL failing dimensions together. Keep the rest of the email's structure and "
        "meaning intact — only fix what the critiques say is broken.\n\n"
        "Rules:\n"
        "  - Match the approximate word count of the original first paragraph.\n"
        "  - Start with the company name or a specific observation about that company.\n"
        "    NEVER start with \"I\" or \"We\".\n"
        "  - No buzzwords. Forbidden: leverage, transformative, seamlessly,\n"
        "    revolutionize, streamline, empower, ecosystem, unlock, cutting-edge,\n"
        "    holistic, innovative, synergy, paradigm, robust, scalable, world-class,\n"
        "    game-changing.\n"
        "  - Do not add facts that are not present in the evidence context.\n"
        "  - If evidence is thin or unsupported, make the paragraph more cautious and less specific.\n"
        "  - Be direct and concrete — name an actual product, market, workflow, or pain.\n"
        "  - Output ONLY the replacement paragraph text. No preamble, no explanation,\n"
        "    no quotation marks around the output."
    )


DIMENSION_INSTRUCTIONS = {
    "pain_specificity": (
        "The pain reference is too generic. Rewrite the first paragraph so it names "
        "a SPECIFIC, falsifiable operational problem unique to this company — cite "
        "an actual product, market, language, or workflow step, not a category."
    ),
    "proof_relevance": (
        "The proof point is loose or mismatched to the pain. Rewrite the first "
        "paragraph so the observation it sets up will pair tightly with the proof "
        "that follows — keep both pointing at the same workflow."
    ),
    "cta_clarity": (
        "The ask is vague or buried. Rewrite the first paragraph so the natural "
        "next step (the CTA later in the email) feels low-friction and specific — "
        "frame the pain in a way that makes a 15-min call feel obviously useful."
    ),
    "human_voice": (
        "The voice is corporate or formulaic. Rewrite the first paragraph in a "
        "natural, conversational register — short sentences, plain words, the kind "
        "of phrasing a peer would use over a coffee conversation. No buzzwords."
    ),
}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _evidence_sort_key(card: object) -> tuple[int, int, int, int]:
    support_rank = {"observed": 0, "derived": 1, "inferred": 2}
    confidence_rank = {"high": 0, "medium": 1, "low": 2}
    source_rank = {"live_signal": 0, "job_signal": 1, "contact": 2, "manual_trigger": 3, "icp_score": 4}
    return (
        0 if getattr(card, "safe_to_use", False) else 1,
        support_rank.get(getattr(card, "support_type", "inferred"), 3),
        confidence_rank.get(getattr(card, "confidence_label", "low"), 3),
        source_rank.get(getattr(card, "source_type", ""), 5),
    )


def _format_evidence_context(enrichment: object | None, limit: int = 8) -> str:
    cards = getattr(enrichment, "evidence_cards", []) if enrichment else []
    if not cards:
        return "Evidence cards: (none)"

    lines = ["Evidence cards:"]
    for card in sorted(cards, key=_evidence_sort_key)[:limit]:
        lines.append(
            f"- {getattr(card, 'evidence_id', '')} | "
            f"{getattr(card, 'source_type', '')}/{getattr(card, 'support_type', '')}/"
            f"{getattr(card, 'confidence_label', '')} | "
            f"safe_to_use={getattr(card, 'safe_to_use', False)}\n"
            f"  Claim: {getattr(card, 'claim', '')}\n"
            f"  Excerpt: {getattr(card, 'excerpt', '')}"
        )
    return "\n".join(lines)


def _format_account_score_context(enrichment: object | None) -> str:
    score = getattr(enrichment, "account_score", None) if enrichment else None
    if not score:
        return "Account-readiness score: (none)"
    warnings = getattr(score, "warnings", []) or []
    return (
        "Account-readiness score:\n"
        f"- overall_score: {getattr(score, 'overall_score', 0)}/100\n"
        f"- priority_label: {getattr(score, 'priority_label', '')}\n"
        f"- recommended_action: {getattr(score, 'recommended_action', '')}\n"
        f"- warnings: {', '.join(warnings) if warnings else '(none)'}"
    )


def _format_contact_context(enrichment: object | None) -> str:
    contacts = getattr(enrichment, "contacts", []) if enrichment else []
    if not contacts:
        return "Contacts: (none)"
    lines = ["Contacts:"]
    for contact in contacts[:6]:
        lines.append(
            f"- {getattr(contact, 'name', '') or '(unknown)'} | "
            f"title={getattr(contact, 'position', '') or '(unknown)'} | "
            f"email_present={bool(getattr(contact, 'email', ''))} | "
            f"confidence={getattr(contact, 'confidence', 0)} | "
            f"seniority={getattr(contact, 'seniority', '') or '(unknown)'}"
        )
    return "\n".join(lines)


def _format_touch_score_context(touch_scores: list[TouchScore]) -> str:
    if not touch_scores:
        return "Touch scores: (none)"
    lines = ["Touch scores:"]
    for score in touch_scores:
        lines.append(
            f"- T{score.touch_number}: avg={score.average:.1f}, "
            f"pain={score.pain_specificity}, proof={score.proof_relevance}, "
            f"cta={score.cta_clarity}, voice={score.human_voice}, "
            f"failing={','.join(score.failing_dims) or '(none)'}"
        )
    return "\n".join(lines)


def _sequence_block(touches: list[SequenceTouch]) -> str:
    lines: list[str] = []
    for touch in touches:
        lines.append(f"--- Touch {touch.touch_number} | Day {touch.day} | Channel: {touch.channel} ---")
        if touch.subject:
            lines.append(f"Subject: {touch.subject}")
        lines.append(f"Body:\n{touch.body}")
        if touch.cta:
            lines.append(f"CTA: {touch.cta}")
        lines.append("")
    return "\n".join(lines)


def _build_critic_human_message(
    touches: list[SequenceTouch],
    company: str,
    evidence_context: str = "",
) -> str:
    lines: list[str] = [
        f"Company: {company}",
        f"Sequence has {len(touches)} touches.",
        "",
    ]
    if evidence_context:
        lines.append(evidence_context)
        lines.append("")
    lines.append(_sequence_block(touches))
    lines.append(
        "Score every touch and return a SequenceCritique with touch_scores, "
        "overall_quality, and critique_summary."
    )
    return "\n".join(lines)


def _build_quality_gate_human_message(
    touches: list[SequenceTouch],
    company: str,
    enrichment: object | None,
    critique: SequenceCritique,
    evidence_context: str,
    panel_context: str = "",
) -> str:
    parts = [
        f"Company: {company}",
        _format_account_score_context(enrichment),
        _format_contact_context(enrichment),
    ]
    if panel_context:
        parts.append(panel_context)
    parts.extend(
        [
            evidence_context,
            _format_touch_score_context(critique.touch_scores),
            f"Overall copy quality: {critique.overall_quality:.1f}/5",
            f"Critique summary: {critique.critique_summary}",
            "",
            "Sequence:",
            _sequence_block(touches),
            "",
            "Return a QualityGate verdict for human review. Do not approve unsupported claims.",
        ]
    )
    return "\n".join(parts)


def _fallback_quality_gate(
    enrichment: object | None,
    critique: SequenceCritique | None,
    reason: str = "",
) -> QualityGate:
    account_score = getattr(enrichment, "account_score", None) if enrichment else None
    priority = getattr(account_score, "priority_label", "") if account_score else ""
    account_warnings = list(getattr(account_score, "warnings", []) or []) if account_score else []
    touch_scores = getattr(critique, "touch_scores", []) if critique else []
    low_copy = bool(getattr(critique, "overall_quality", 0.0) and getattr(critique, "overall_quality", 0.0) < 3.0)

    risk_flags: list[RiskFlag] = []
    if any("No contacts" in warning for warning in account_warnings):
        risk_flags.append(
            RiskFlag(
                risk_type="contact_confidence",
                severity="high",
                rationale="Account scoring found no contacts.",
                recommended_fix="Run or manually verify contact discovery before sending.",
            )
        )
    if any("Evidence is thin" in warning or "No high-confidence" in warning for warning in account_warnings):
        risk_flags.append(
            RiskFlag(
                risk_type="thin_evidence",
                severity="high" if priority == "do_not_send_yet" else "medium",
                rationale="Account scoring found weak or thin source-backed evidence.",
                recommended_fix="Gather stronger observed evidence before approving outreach.",
            )
        )
    for score in touch_scores:
        if score.needs_rewrite:
            risk_flags.append(
                RiskFlag(
                    risk_type="generic_copy" if "pain_specificity" in score.failing_dims else "tone_issue",
                    severity="medium",
                    touch_number=score.touch_number,
                    rationale=score.feedback or "Touch failed one or more copy quality dimensions.",
                    recommended_fix="Edit the touch before review.",
                )
            )

    if priority == "do_not_send_yet":
        verdict = "do_not_send_yet"
    elif priority == "needs_more_research":
        verdict = "needs_more_research"
    elif low_copy or risk_flags:
        verdict = "needs_edit"
    else:
        verdict = "approved"

    return QualityGate(
        verdict=verdict,  # type: ignore[arg-type]
        safe_to_send=(verdict == "approved"),
        confidence="low" if reason else "medium",
        summary=(
            f"Fallback quality gate used{f' after {reason}' if reason else ''}. "
            "Human review is still required before sending."
        ),
        required_edits=account_warnings[:4],
        risk_flags=risk_flags[:8],
        unsupported_claim_count=0,
        evidence_coverage_note="Fallback gate did not validate claim-level evidence coverage.",
    )


def _build_rewriter_human_message(
    first_para: str,
    score: TouchScore,
    company: str,
    touch_number: int,
    dimension: str = "",
    critique: str = "",
    evidence_context: str = "",
) -> str:
    instruction = DIMENSION_INSTRUCTIONS.get(dimension, "")
    dim_block = ""
    if dimension:
        dim_block = (
            f"\nFailing dimension: {dimension}\n"
            f"Critique: {critique or score.feedback}\n"
            f"Rewrite instruction: {instruction}\n"
        )
    return (
        f"Company: {company}\n"
        f"Touch number: {touch_number}\n"
        f"Quality score: {score.average:.1f}/5\n"
        f"{dim_block}\n"
        f"Allowed evidence context:\n{evidence_context or '(no evidence cards available)'}\n\n"
        f"Original first paragraph:\n{first_para}\n\n"
        "Write a replacement first paragraph only."
    )


def _build_consolidated_rewriter_human_message(
    first_para: str,
    score: TouchScore,
    company: str,
    touch_number: int,
    evidence_context: str = "",
) -> str:
    """
    One prompt covering ALL failing dimensions of a touch at once, so a single
    rewrite call fixes the whole paragraph instead of one dimension per call.
    """
    dim_blocks: list[str] = []
    for dim in score.failing_dims or []:
        critique_field = DIMENSION_FIELDS[dim][1]
        critique = (getattr(score, critique_field) or score.feedback or "").strip()
        instruction = DIMENSION_INSTRUCTIONS.get(dim, "")
        dim_blocks.append(
            f"- {dim}\n  Critique: {critique}\n  Rewrite instruction: {instruction}"
        )
    dims_block = "\n".join(dim_blocks) or f"- {score.feedback or 'overall quality below threshold'}"

    return (
        f"Company: {company}\n"
        f"Touch number: {touch_number}\n"
        f"Quality score: {score.average:.1f}/5\n\n"
        f"Failing dimensions ({len(dim_blocks)}):\n{dims_block}\n\n"
        f"Allowed evidence context:\n{evidence_context or '(no evidence cards available)'}\n\n"
        f"Original first paragraph:\n{first_para}\n\n"
        "Write a replacement first paragraph only, fixing ALL failing dimensions together."
    )


def _replace_first_paragraph(body: str, new_para: str) -> str:
    parts = body.split("\n\n")
    if len(parts) <= 1:
        return new_para
    return new_para + "\n\n" + "\n\n".join(parts[1:])


def _rewrite_failing_touches(
    touches_needing_rewrite: list[TouchScore],
    card: ProspectCard,
    company: str,
    evidence_context: str,
    tenant: TenantConfig,
    api_key: str,
    tracker: "UsageTracker | None" = None,
) -> tuple[ProspectCard, int, list[str]]:
    """
    Rewrite the first paragraph of each failing email touch.

    One rewrite call per touch (all failing dimensions merged into a single
    prompt), with one in-place retry if the model returns something unusable.
    Returns (updated card, rewrites applied, trace lines).
    """
    updated_card = deepcopy(card)
    if updated_card.sequence is None:
        return updated_card, 0, []
    touch_map: dict[int, SequenceTouch] = {
        t.touch_number: t for t in updated_card.sequence.touches
    }

    rewriter_kwargs: dict = {"callbacks": [tracker]} if tracker is not None else {}
    rewriter_route = tenant.models.route_for("rewriter")
    rewriter_llm = build_client(
        rewriter_route,
        api_key=api_key,
        max_tokens=300,
        temperature=0.4,
        extra_kwargs=rewriter_kwargs,
    )
    rewriter_system = _build_rewriter_system(tenant)

    rewrites_applied = 0
    trace: list[str] = []

    for ts in touches_needing_rewrite:
        touch = touch_map.get(ts.touch_number)
        if touch is None or touch.channel != "email" or not touch.body:
            continue

        first_para = touch.body.split("\n\n")[0]
        rewriter_human = _build_consolidated_rewriter_human_message(
            first_para=first_para,
            score=ts,
            company=company,
            touch_number=touch.touch_number,
            evidence_context=evidence_context,
        )

        for attempt in (1, 2):
            try:
                response = rewriter_llm.invoke(
                    [
                        cached_system_message(rewriter_route, rewriter_system),
                        HumanMessage(content=rewriter_human),
                    ]
                )
                new_para = (response.content or "").strip()
                if new_para and new_para != first_para:
                    new_body = _replace_first_paragraph(touch.body, new_para)
                    new_body = humanize(new_body)
                    touch.body = new_body
                    touch.word_count = len(new_body.replace("\n", " ").split())
                    ts.rewrite_attempts = attempt
                    rewrites_applied += 1
                    dims = ",".join(ts.failing_dims) or "quality"
                    trace.append(
                        f"Critic: rewrote T{touch.touch_number} ({dims}) — single consolidated call"
                    )
                    break
            except Exception as rewrite_exc:  # noqa: BLE001
                logger.warning(
                    "Critic: rewrite failed for T%d: %s", ts.touch_number, rewrite_exc
                )
                trace.append(
                    f"Critic: rewrite failed T{ts.touch_number} — {rewrite_exc}"
                )
                break

    return updated_card, rewrites_applied, trace


# ---------------------------------------------------------------------------
# LangGraph node
# ---------------------------------------------------------------------------

def run_critic(state: BDRState) -> dict:
    """
    Quality-gate node.

    1. Council scoring: `tenant.critic.council_size` independent scorers run in
       parallel; scores are aggregated by per-dimension median.
    2. Rewrite: failing email touches get ONE consolidated rewrite call each.
    3. Gate: the quality gate runs LAST, on the final post-rewrite sequence,
       fed the panel's aggregated scores and disagreement notes.

    On scorer failure: remaining scorers still count; if ALL scorers fail, a
    neutral CriticResult with a fallback gate is returned (pipeline continues).
    On any exception: log to trace and return {} (pipeline continues).
    """
    trace: list[str] = list(state.get("agent_trace", []))

    try:
        if state.get("error"):
            return {}

        tenant: TenantConfig | None = state.get("tenant")
        if tenant is None:
            trace.append("Critic: tenant config missing — skipping quality gate")
            return {"agent_trace": trace}

        card: ProspectCard | None = state.get("card")
        if card is None or card.sequence is None:
            trace.append("Critic: no sequence found — skipping quality gate")
            return {"agent_trace": trace}

        sequence = card.sequence
        touches = sequence.touches
        if not touches:
            trace.append("Critic: empty touch list — skipping quality gate")
            return {"agent_trace": trace}

        company: str = state.get("company") or ""
        enrichment = state.get("enrichment")
        evidence_context = _format_evidence_context(enrichment)

        api_key = os.environ.get("ANTHROPIC_API_KEY", "")
        if not api_key:
            trace.append("Critic: ANTHROPIC_API_KEY missing — skipping quality gate")
            return {"agent_trace": trace}

        critic_cfg = tenant.critic or CriticConfig()
        council_size = max(1, critic_cfg.council_size)

        # --- 1. Council scoring (cascade-aware, B4) -----------------------
        tracker = UsageTracker(node="critic", default_model=DEFAULT_MODEL)
        escalated = False
        if critic_cfg.cascade_enabled and council_size > 1:
            # Single rater first (scorer 0). If it lands clean, we are done —
            # council_size stays 1 for this run. Otherwise the SAME scorer's
            # critique seeds the full panel (no call is wasted on escalation).
            single_critique, _, _, single_notes = _score_with_council(
                touches=touches,
                company=company,
                evidence_context=evidence_context,
                tenant=tenant,
                critic_cfg=CriticConfig(council_size=1, council_models=[critic_cfg.council_models[0]]),
                api_key=api_key,
                tracker=tracker,
            )
            if single_critique is None:
                critique, scorer_overalls, disagreements, scorer_notes = None, [], [], single_notes
            else:
                escalate, reasons = needs_council_escalation(single_critique, critic_cfg)
                if escalate:
                    escalated = True
                    trace.append(f"Critic: cascade escalated — {'; '.join(reasons)}")
                    critique, scorer_overalls, disagreements, scorer_notes = _score_with_council(
                        touches=touches,
                        company=company,
                        evidence_context=evidence_context,
                        tenant=tenant,
                        critic_cfg=critic_cfg,
                        api_key=api_key,
                        tracker=tracker,
                        seed_critique=single_critique,
                    )
                else:
                    escalated = False
                    critique = single_critique
                    scorer_overalls = [single_critique.overall_quality]
                    disagreements = []
                    scorer_notes = single_notes
                    council_size = 1
                    trace.append("Critic: cascade — single rater clean, full council skipped")
        else:
            critique, scorer_overalls, disagreements, scorer_notes = _score_with_council(
                touches=touches,
                company=company,
                evidence_context=evidence_context,
                tenant=tenant,
                critic_cfg=critic_cfg,
                api_key=api_key,
                tracker=tracker,
            )

        if critique is None:
            logger.warning("Critic: all %d scorer call(s) failed — returning neutral result", council_size)
            trace.append(
                f"Critic: council scoring failed ({' · '.join(scorer_notes) or 'no scorers succeeded'})"
                " — neutral result"
            )
            return {
                "card": card,
                "critic_result": CriticResult(
                    overall_quality=0.0,
                    rewrites_applied=0,
                    quality_gate=_fallback_quality_gate(enrichment, None, reason="all scorers failed"),
                    critique_summary="Critic skipped — scoring calls failed.",
                    council_size=council_size,
                    scorer_overall_scores=[],
                    disagreements=[],
                    agreement_level="low",
                ),
                "agent_trace": trace,
            }

        for note in scorer_notes:
            trace.append(f"Critic: {note}")

        n_touches = len(critique.touch_scores)
        avg = critique.overall_quality
        if council_size > 1:
            trace.append(
                f"Critic: council of {council_size} scored {n_touches} touches "
                f"(aggregated avg: {avg:.1f}, scorer spread: "
                f"{(max(scorer_overalls) - min(scorer_overalls)) if scorer_overalls else 0:.1f})"
            )
        else:
            trace.append(f"Critic: scored {n_touches} touches (avg: {avg:.1f})")

        # --- 2. Rewrites (BEFORE the gate, so the gate sees final copy) ----
        rewrites_applied = 0
        touches_needing_rewrite = [ts for ts in critique.touch_scores if ts.needs_rewrite]

        if touches_needing_rewrite:
            card, rewrites_applied, rewrite_trace = _rewrite_failing_touches(
                touches_needing_rewrite=touches_needing_rewrite,
                card=card,
                company=company,
                evidence_context=evidence_context,
                tenant=tenant,
                api_key=api_key,
                tracker=tracker,
            )
            trace.extend(rewrite_trace)
            touches = card.sequence.touches if card.sequence else touches

        trace.append(f"Critic: {rewrites_applied} rewrite(s) applied")

        # --- 3. Quality gate on the FINAL (post-rewrite) sequence ----------
        panel_context = _build_panel_context(scorer_overalls, disagreements)
        gate_kwargs: dict = {"callbacks": [tracker]}
        gate_route = tenant.models.route_for("gate")
        gate_llm = build_client(
            gate_route,
            api_key=api_key,
            max_tokens=4000,
            temperature=0.2,
            extra_kwargs=gate_kwargs,
        ).with_structured_output(QualityGate)
        gate_human_msg = _build_quality_gate_human_message(
            touches=touches,
            company=company,
            enrichment=enrichment,
            critique=critique,
            evidence_context=evidence_context,
            panel_context=panel_context,
        )
        try:
            quality_gate: QualityGate = gate_llm.invoke(
                [
                    cached_system_message(gate_route, _build_quality_gate_system(tenant)),
                    HumanMessage(content=gate_human_msg),
                ]
            )
        except Exception as gate_exc:  # noqa: BLE001
            logger.warning("Critic: quality gate call failed (%s) — using fallback", gate_exc)
            quality_gate = _fallback_quality_gate(enrichment, critique, reason=type(gate_exc).__name__)

        trace.append(
            f"Critic: quality gate verdict {quality_gate.verdict} · "
            f"{len(quality_gate.risk_flags)} risk flags"
        )
        trace.append(f"Critic: unsupported claims flagged: {quality_gate.unsupported_claim_count}")

        agreement = "single_rater" if council_size == 1 else judge_disagreement_level(disagreements)
        critic_result = CriticResult(
            touch_scores=critique.touch_scores,
            overall_quality=critique.overall_quality,
            rewrites_applied=rewrites_applied,
            critique_summary=critique.critique_summary,
            quality_gate=quality_gate,
            council_size=council_size,
            scorer_overall_scores=scorer_overalls,
            disagreements=disagreements,
            agreement_level=agreement,  # type: ignore[arg-type]
        )

        node_update = {
            "card": card,
            "critic_result": critic_result,
            "agent_trace": trace,
        }
        node_update.update(capture_usage(state, "critic", tracker))
        return node_update

    except Exception as exc:  # noqa: BLE001
        logger.exception("Critic node failed: %s", exc)
        trace.append(f"Critic: exception — {exc}")
        return {}
