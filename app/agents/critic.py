"""
critic.py — Quality-gate node for the BDR pipeline.

Scores every touch in the outreach sequence on four dimensions, then rewrites
the first paragraph of any email touch where one or more dimensions fall below
the quality threshold (each dim < 3 on a 1-5 scale).

Fixed-bank content (proof points, CTAs, subjects) is never touched — only the
LLM-generated first paragraph (the observation paragraph) may be rewritten.

Tenant-aware: brand name, persona, and product context come from
`state["tenant"]`. Critic prompts are built per-tenant.
"""
from __future__ import annotations

import logging
import os
from copy import deepcopy
from typing import Literal

from langchain_anthropic import ChatAnthropic
from langchain_core.messages import HumanMessage, SystemMessage
from pydantic import BaseModel, Field, model_validator

from app.services.humanizer_rules import humanize
from app.tenants.schema import TenantConfig

from .state import BDRState, ProspectCard, SequenceTouch

logger = logging.getLogger(__name__)

MODEL = "claude-sonnet-4-6"


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


# ---------------------------------------------------------------------------
# System prompts (tenant-aware)
# ---------------------------------------------------------------------------

def _build_critic_system(tenant: TenantConfig) -> str:
    return (
        f"You are a senior B2B sales coach reviewing a cold outreach sequence "
        f"for {tenant.brand.name}.\n\n"
        f"Product context: {tenant.business.description.strip()}\n"
        f"Target persona: {tenant.persona.title}\n\n"
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
        "For each touch produce: touch_number, four dim scores (1–5), per-dim\n"
        "critique strings (or empty if score >= 3), and a one-sentence feedback.\n\n"
        "Also produce overall_quality (mean of all touch averages) and a 1–2 sentence\n"
        "critique_summary."
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
        "  - Do not claim the gate guarantees deliverability, reply quality, or safety for auto-send.\n"
        "  - Human review is still required before sending.\n\n"
        "Return a QualityGate. Keep the summary and fixes concise, specific, and founder-friendly."
    )


def _build_rewriter_system(tenant: TenantConfig) -> str:
    return (
        f"You are a B2B cold-email rewriter for {tenant.brand.name}.\n\n"
        "You receive:\n"
        "  - The original outreach email body\n"
        "  - The single failing dimension and its critique\n"
        "  - Instructions to rewrite ONLY the part of the email that addresses that dimension\n\n"
        "Your job: produce a replacement first paragraph (1–2 sentences) targeted at "
        "the failing dimension. Keep the rest of the email's structure and meaning "
        "intact — only fix what the critique says is broken.\n\n"
        "Rules:\n"
        "  - Match the approximate word count of the original first paragraph.\n"
        "  - Start with the company name or a specific observation about that company.\n"
        '    NEVER start with "I" or "We".\n'
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
) -> str:
    return "\n".join(
        [
            f"Company: {company}",
            _format_account_score_context(enrichment),
            _format_contact_context(enrichment),
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


def _replace_first_paragraph(body: str, new_para: str) -> str:
    parts = body.split("\n\n")
    if len(parts) <= 1:
        return new_para
    return new_para + "\n\n" + "\n\n".join(parts[1:])


# ---------------------------------------------------------------------------
# LangGraph node
# ---------------------------------------------------------------------------

def run_critic(state: BDRState) -> dict:
    """
    Quality-gate node.

    1. Extract sequence from state["card"].sequence.
    2. Score all touches with one structured Claude call.
    3. Rewrite first paragraph of email touches with any failing dimension.
    4. Return updated card, CriticResult, agent_trace.
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

        llm = ChatAnthropic(
            model=MODEL,
            api_key=api_key,
            max_tokens=4000,
            temperature=0.2,
        )
        critic_llm = llm.with_structured_output(SequenceCritique)

        human_msg = _build_critic_human_message(touches, company, evidence_context=evidence_context)
        try:
            critique: SequenceCritique = critic_llm.invoke(
                [SystemMessage(content=_build_critic_system(tenant)), HumanMessage(content=human_msg)]
            )
        except Exception as score_exc:  # noqa: BLE001
            # Structured output sometimes returns {} on long inputs; surface a neutral
            # CriticResult instead of crashing the pipeline.
            logger.warning("Critic: scoring call failed (%s) — returning neutral result", score_exc)
            trace.append(f"Critic: scoring failed ({type(score_exc).__name__}) — neutral result")
            return {
                "card": card,
                "critic_result": CriticResult(
                    overall_quality=0.0,
                    rewrites_applied=0,
                    quality_gate=_fallback_quality_gate(enrichment, None, reason=type(score_exc).__name__),
                    critique_summary="Critic skipped — scoring call failed.",
                ),
                "agent_trace": trace,
            }

        n_touches = len(critique.touch_scores)
        avg = critique.overall_quality
        trace.append(f"Critic: scored {n_touches} touches (avg: {avg:.1f})")

        gate_llm = llm.with_structured_output(QualityGate)
        gate_human_msg = _build_quality_gate_human_message(
            touches=touches,
            company=company,
            enrichment=enrichment,
            critique=critique,
            evidence_context=evidence_context,
        )
        try:
            quality_gate: QualityGate = gate_llm.invoke(
                [
                    SystemMessage(content=_build_quality_gate_system(tenant)),
                    HumanMessage(content=gate_human_msg),
                ]
            )
        except Exception as gate_exc:  # noqa: BLE001
            logger.warning("Critic: quality gate call failed (%s) â€” using fallback", gate_exc)
            quality_gate = _fallback_quality_gate(enrichment, critique, reason=type(gate_exc).__name__)

        trace.append(
            f"Critic: quality gate verdict {quality_gate.verdict} · "
            f"{len(quality_gate.risk_flags)} risk flags"
        )
        trace.append(f"Critic: unsupported claims flagged: {quality_gate.unsupported_claim_count}")

        rewrites_applied = 0
        touches_needing_rewrite = [ts for ts in critique.touch_scores if ts.needs_rewrite]

        if quality_gate.verdict == "do_not_send_yet":
            updated_card = card
            trace.append("Critic: do_not_send_yet verdict â€” skipping rewrite-to-approve behavior")
        elif touches_needing_rewrite:
            updated_card = deepcopy(card)
            touch_map: dict[int, SequenceTouch] = {
                t.touch_number: t for t in updated_card.sequence.touches
            }

            rewriter_llm = ChatAnthropic(
                model=MODEL,
                api_key=api_key,
                max_tokens=300,
                temperature=0.4,
            )

            MAX_ATTEMPTS_PER_DIM = 2
            rewriter_system = _build_rewriter_system(tenant)

            for ts in touches_needing_rewrite:
                touch = touch_map.get(ts.touch_number)
                if touch is None or touch.channel != "email" or not touch.body:
                    continue

                for dim in ts.failing_dims:
                    if dim == "cta_clarity":
                        dim_critique = ts.cta_critique or ts.feedback
                    elif dim == "human_voice":
                        dim_critique = ts.voice_critique or ts.feedback
                    elif dim == "pain_specificity":
                        dim_critique = ts.pain_critique or ts.feedback
                    elif dim == "proof_relevance":
                        dim_critique = ts.proof_critique or ts.feedback
                    else:
                        dim_critique = ts.feedback

                    attempt = 0
                    while attempt < MAX_ATTEMPTS_PER_DIM:
                        attempt += 1
                        first_para = touch.body.split("\n\n")[0]
                        rewriter_human = _build_rewriter_human_message(
                            first_para=first_para,
                            score=ts,
                            company=company,
                            touch_number=touch.touch_number,
                            dimension=dim,
                            critique=dim_critique,
                            evidence_context=evidence_context,
                        )
                        try:
                            response = rewriter_llm.invoke(
                                [
                                    SystemMessage(content=rewriter_system),
                                    HumanMessage(content=rewriter_human),
                                ]
                            )
                            new_para: str = (response.content or "").strip()
                            if new_para and new_para != first_para:
                                new_body = _replace_first_paragraph(touch.body, new_para)
                                new_body = humanize(new_body)
                                touch.body = new_body
                                touch.word_count = len(new_body.replace("\n", " ").split())
                                rewrites_applied += 1
                                ts.rewrite_attempts = attempt
                                trace.append(
                                    f"Critic: rewrote T{touch.touch_number}.{dim} "
                                    f"(attempt {attempt})"
                                )
                                break
                        except Exception as rewrite_exc:  # noqa: BLE001
                            logger.warning(
                                "Critic: rewrite failed for T%d.%s: %s",
                                ts.touch_number, dim, rewrite_exc,
                            )
                            trace.append(
                                f"Critic: rewrite failed T{ts.touch_number}.{dim} — {rewrite_exc}"
                            )
                            break
                    else:
                        trace.append(
                            f"Critic: T{ts.touch_number}.{dim} hit {MAX_ATTEMPTS_PER_DIM}-attempt cap"
                        )
        else:
            updated_card = card

        trace.append(f"Critic: {rewrites_applied} rewrites applied")

        critic_result = CriticResult(
            touch_scores=critique.touch_scores,
            overall_quality=critique.overall_quality,
            rewrites_applied=rewrites_applied,
            critique_summary=critique.critique_summary,
            quality_gate=quality_gate,
        )

        return {
            "card": updated_card,
            "critic_result": critic_result,
            "agent_trace": trace,
        }

    except Exception as exc:  # noqa: BLE001
        logger.exception("Critic node failed: %s", exc)
        trace.append(f"Critic: exception — {exc}")
        return {}
