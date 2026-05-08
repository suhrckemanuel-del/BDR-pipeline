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


class CriticResult(BaseModel):
    touch_scores: list[TouchScore] = Field(default_factory=list)
    overall_quality: float = 0.0
    rewrites_applied: int = 0
    critique_summary: str = ""


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

def _build_critic_human_message(touches: list[SequenceTouch], company: str) -> str:
    lines: list[str] = [
        f"Company: {company}",
        f"Sequence has {len(touches)} touches.",
        "",
    ]
    for touch in touches:
        lines.append(f"--- Touch {touch.touch_number} | Day {touch.day} | Channel: {touch.channel} ---")
        if touch.subject:
            lines.append(f"Subject: {touch.subject}")
        lines.append(f"Body:\n{touch.body}")
        if touch.cta:
            lines.append(f"CTA: {touch.cta}")
        lines.append("")
    lines.append(
        "Score every touch and return a SequenceCritique with touch_scores, "
        "overall_quality, and critique_summary."
    )
    return "\n".join(lines)


def _build_rewriter_human_message(
    first_para: str,
    score: TouchScore,
    company: str,
    touch_number: int,
    dimension: str = "",
    critique: str = "",
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

        api_key = os.environ.get("ANTHROPIC_API_KEY", "")
        if not api_key:
            trace.append("Critic: ANTHROPIC_API_KEY missing — skipping quality gate")
            return {"agent_trace": trace}

        llm = ChatAnthropic(
            model=MODEL,
            api_key=api_key,
            max_tokens=1500,
            temperature=0.2,
        )
        critic_llm = llm.with_structured_output(SequenceCritique)

        human_msg = _build_critic_human_message(touches, company)
        critique: SequenceCritique = critic_llm.invoke(
            [SystemMessage(content=_build_critic_system(tenant)), HumanMessage(content=human_msg)]
        )

        n_touches = len(critique.touch_scores)
        avg = critique.overall_quality
        trace.append(f"Critic: scored {n_touches} touches (avg: {avg:.1f})")

        rewrites_applied = 0
        touches_needing_rewrite = [ts for ts in critique.touch_scores if ts.needs_rewrite]

        if touches_needing_rewrite:
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
