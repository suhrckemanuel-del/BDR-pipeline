"""
humanizer.py — Step 3: Humanizer Drafter (anti-AI agent).

Pattern: [Specific Observation] + [Tenant Proof Point] + [Specific Workflow Offer]

Only the Specific Observations and the Before/After narrative come from an LLM
(under a strict structured-output schema with prompt caching). The proof
points, offer templates, subject lines, and breakup language all come from
the tenant's `copy.json` — eliminating LLM "voice", banned vocab, and
formulaic CTAs by construction.

Key patterns:
  - Prompt caching on the static system prompt
  - trigger_headline injected into observation generation
  - Contact first name used in DM personalization
  - Variant index hashed from (tenant_id + company) — deterministic across reruns
  - Day 1 LinkedIn connection request prepended to sequence (touch 0)
  - Tier-3 prospects get a shorter sequence (no Day 7 social proof touch)
"""
from __future__ import annotations

import os
import re

from langchain_anthropic import ChatAnthropic
from langchain_core.messages import HumanMessage, SystemMessage

from app.prompts import load_prompt
from app.services.humanizer_rules import humanize_angle_draft, humanize_sequence
from app.tenants.schema import AngleCopy, TenantConfig

from .model_config import resolve_model

from .state import (
    ANGLE_KEYS,
    AngleDraft,
    BDRState,
    HumanizerObservations,
    OutreachSequence,
    ProspectCard,
    SequenceTouch,
)

MODEL = "claude-sonnet-4-6"


# ---------------------------------------------------------------------------
# Text utilities
# ---------------------------------------------------------------------------
def _clean(text: str) -> str:
    cleaned = (text or "").replace("—", ", ")
    cleaned = cleaned.replace("\r", " ").replace("\n", " ")
    cleaned = re.sub(r"\s+", " ", cleaned)
    cleaned = re.sub(r"\s+([,.;:?!])", r"\1", cleaned)
    return cleaned.strip()


def _trim_words(text: str, max_words: int) -> str:
    words = text.split()
    if len(words) <= max_words:
        return text.strip()
    trimmed = " ".join(words[:max_words]).rstrip(" ,;:")
    if trimmed and trimmed[-1] not in ".!":
        trimmed += "."
    return trimmed


def _word_count(text: str) -> int:
    return len(text.replace("\n", " ").split())


# ---------------------------------------------------------------------------
# LLM call — produce only the things we cannot template
# ---------------------------------------------------------------------------
def _build_system_prompt(tenant: TenantConfig) -> str:
    headline = tenant.business.headline_metric
    after_hint = (
        f" The After paragraph should reference '{headline}' if it fits naturally — "
        f"this is the tenant's headline metric."
        if headline else ""
    )
    return (
        "You are the Observation Generator for the Humanizer agent.\n\n"
        f"Tenant: {tenant.brand.name}. {tenant.business.description.strip()}\n\n"
        "You are NOT writing emails. You are producing 5 short text snippets that "
        "the deterministic assembler will glue into emails using fixed templates. "
        "Your only job is specificity — name the actual category, the actual driver, "
        "the actual constraint at this company.\n\n"
        "Rules:\n"
        '  - No buzzwords: never use "actually", "additionally", "transformative", '
        '"leverage" (verb), "showcasing", "landscape", "actively".\n'
        "  - No questions, no rhetorical openers.\n"
        "  - Each observation must be ONE sentence, max ~22 words.\n"
        "  - If the research summary or trigger headline contains a dated external "
        "trigger (transformation programme name, hiring announcement, M&A, earnings "
        "call mention), LEAD the observation with it.\n"
        "  - The observation must be falsifiable — a reader who knows the company "
        "should recognise the fact as specific to them, not applicable to every peer.\n"
        "  - Reference a real category, function, or concrete operating constraint.\n"
        f"  - Before/After narrative: 2 short paragraphs. The After paragraph describes "
        f"the outcome with {tenant.brand.name}.{after_hint}\n"
        f"  - The After paragraph MUST NOT start with 'With {tenant.brand.name}:' — "
        "the assembler prepends that prefix.\n\n"
        "Your observations will be the FIRST paragraph of a cold email. Apply the "
        "principles below — the observation must read like a peer noticed something "
        "real, not like a template field was filled in.\n\n"
        + load_prompt("cold_email")
    )


def _generate_observations(
    company: str,
    industry: str,
    research_summary: str,
    pain_signal: str,
    persona: str,
    tenant: TenantConfig,
    trigger_headline: str = "",
    evidence_context: str = "",
) -> HumanizerObservations:
    api_key = os.environ.get("ANTHROPIC_API_KEY", "")
    if not api_key:
        return _default_observations(company, industry, tenant)

    llm = ChatAnthropic(
        model=resolve_model(tenant, "humanizer", MODEL),
        api_key=api_key,
        max_tokens=900,
        temperature=0.4,
        extra_headers={"anthropic-beta": "prompt-caching-2024-07-31"},
    )
    structured = llm.with_structured_output(HumanizerObservations)

    system_msg = SystemMessage(
        content=_build_system_prompt(tenant),
        additional_kwargs={"cache_control": {"type": "ephemeral"}},
    )

    trigger_line = f"\nTop trigger headline (use if specific): {trigger_headline}" if trigger_headline else ""
    evidence_line = f"\nEvidence context (use only if it directly supports the observation):\n{evidence_context}\n" if evidence_context else ""

    user = (
        f"Company: {company}\n"
        f"Industry: {industry or 'unknown'}\n"
        f"Likely persona: {persona}\n"
        f"Specific pain to anchor: {pain_signal}{trigger_line}{evidence_line}\n\n"
        f"Research summary (from prior agents):\n{research_summary or '(none)'}\n\n"
        "Produce HumanizerObservations: three one-sentence observations (one per angle) "
        "and the two-paragraph Before/After narrative. Be concrete and specific. "
        "Lead with external triggers when present."
    )
    try:
        return structured.invoke([system_msg, HumanMessage(content=user)])
    except Exception:
        return _default_observations(company, industry, tenant)


def _default_observations(
    company: str, industry: str, tenant: TenantConfig
) -> HumanizerObservations:
    headline = tenant.business.headline_metric or ""
    return HumanizerObservations(
        angle1_observation=f"{company} runs {tenant.angles[0].name.lower()} on long manual cycles.",
        angle2_observation=f"{company} has {tenant.angles[1].name.lower()} pain above existing tooling.",
        angle3_observation=f"{company} has {tenant.angles[2].name.lower()} coverage gaps.",
        before_text=f"{company}'s team operates today on a slow, manual workflow that creates the gaps each angle describes.",
        after_text=(
            f"{tenant.brand.name} replaces the slow workflow with a structured layer that "
            f"a {tenant.persona.title} can run end-to-end."
            + (f" {headline}." if headline else "")
        ),
    )


def _format_evidence_context(enrichment: object, limit: int = 3) -> str:
    cards = getattr(enrichment, "evidence_cards", []) or []
    usable = [
        c for c in cards
        if getattr(c, "support_type", "") == "observed"
        and getattr(c, "confidence_label", "") in {"high", "medium"}
    ]
    lines: list[str] = []
    for card in usable[:limit]:
        claim = getattr(card, "claim", "") or ""
        excerpt = getattr(card, "excerpt", "") or ""
        source = getattr(card, "source_title", "") or getattr(card, "source_type", "source")
        if claim or excerpt:
            lines.append(f"- {claim or excerpt} | Source: {source}")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Deterministic assembly
# ---------------------------------------------------------------------------
def _variant_index(tenant_id: str, company: str, angle_idx: int) -> int:
    """Salted with tenant_id so different tenants pick different variants for the same company."""
    seed = (tenant_id + ":" + company).lower()
    return (sum(ord(c) for c in seed) + angle_idx) % 3


def _assemble_email(
    angle_key: str,
    observation: str,
    company: str,
    industry: str,
    variant: int,
    copy: AngleCopy,
    signature: str,
) -> str:
    p1 = _clean(observation)
    p2 = copy.proof_points[variant]
    p3 = copy.email_offers[variant].format(company=company, industry=industry)

    sig_block = f"\n\n{signature}"

    email = "\n\n".join([p1, p2, p3]) + sig_block
    wc = _word_count(email)

    if wc < 80:
        p1 = f"{p1} {copy.email_filler_p1}"
        email = "\n\n".join([p1, p2, p3]) + sig_block
        wc = _word_count(email)
    if wc < 80:
        p3 = f"{p3} {copy.email_filler_p3}"
        email = "\n\n".join([p1, p2, p3]) + sig_block
        wc = _word_count(email)
    if wc < 80:
        p2 = f"{p2} {copy.email_filler_p2}"
        email = "\n\n".join([p1, p2, p3]) + sig_block
        wc = _word_count(email)
    if wc > 100:
        overflow = wc - 100
        target = max(18, len(p1.split()) - overflow)
        p1 = _trim_words(p1, target)
        email = "\n\n".join([p1, p2, p3]) + sig_block
    return email


def _assemble_dm(
    angle_key: str,
    observation: str,
    company: str,
    industry: str,
    variant: int,
    copy: AngleCopy,
    dm_signoff: str,
    contact_first_name: str = "",
) -> str:
    obs = _trim_words(_clean(observation), 14)
    proof = _trim_words(copy.proof_points[variant], 10)
    offer = _trim_words(
        copy.dm_offers[variant].format(company=company, industry=industry), 9
    )
    greeting = f"Hi {contact_first_name}, " if contact_first_name else ""
    dm = f"{greeting}{obs} {proof} {offer}\n\n{dm_signoff}"
    if _word_count(dm) > 65:
        dm = f"{greeting}{_trim_words(obs, 12)} {_trim_words(proof, 9)} {_trim_words(offer, 8)}\n\n{dm_signoff}"
    return dm


def _assemble_subject(copy: AngleCopy, company: str, variant: int) -> str:
    return copy.subject_templates[variant].format(company=company)


def _build_angle(
    angle_key: str,
    observation: str,
    company: str,
    industry: str,
    tenant: TenantConfig,
    contact_first_name: str = "",
) -> AngleDraft:
    angle_idx = ANGLE_KEYS.index(angle_key)
    variant = _variant_index(tenant.tenant_id, company, angle_idx)
    angle_meta = tenant.angle_by_key(angle_key)
    copy = tenant.humanizer_copy.by_key(angle_key)
    signature = tenant.sender.resolved_signature()
    dm_signoff = tenant.sender.resolved_dm_signoff()

    return AngleDraft(
        angle_key=angle_key,
        name=angle_meta.name,
        tab_label=angle_meta.tab_label,
        dm=_assemble_dm(angle_key, observation, company, industry, variant, copy, dm_signoff, contact_first_name),
        email_subject=_assemble_subject(copy, company, variant),
        email_body=_assemble_email(angle_key, observation, company, industry, variant, copy, signature),
    )


def _assemble_before_after(before: str, after: str, tenant: TenantConfig) -> str:
    before = _clean(before)
    after = _clean(after)
    # Strip a leading "With <Brand>:" if the LLM included it despite instructions
    after = re.sub(rf"^\s*With\s+{re.escape(tenant.brand.name)}:\s*", "", after, flags=re.I)
    return f"{before}\n\nWith {tenant.brand.name}: {after}"


# The built-in plan, expressed as (touch type, day). This is what every tenant
# without a sequence_variants block gets — behavior identical to the original
# hardcoded 6-touch builder (including the tier-3 social-proof drop below).
_DEFAULT_TOUCH_PLAN: list[tuple[str, int]] = [
    ("linkedin_connect", 0),
    ("intro_email", 0),
    ("followup_email", 3),
    ("social_proof_email", 7),
    ("linkedin_dm", 10),
    ("breakup_email", 21),
]


def _touch_builders(
    angle_key: str,
    observation: str,
    company: str,
    industry: str,
    variant: int,
    tenant: TenantConfig,
    contact_first_name: str,
):
    """(subject, body, channel, note) builder per sequence touch type."""
    copy = tenant.humanizer_copy.by_key(angle_key)
    signature = tenant.sender.resolved_signature()
    dm_signoff = tenant.sender.resolved_dm_signoff()

    def fmt(bank: list[str]) -> str:
        return bank[variant].format(company=company, industry=industry)

    return {
        "linkedin_connect": lambda: (
            "",
            fmt(copy.linkedin_connect_notes),
            "linkedin_connect",
            (
                "Send as a LinkedIn connection request (no note or brief note only). "
                "Being connected means your later DM arrives as a connection message, not cold InMail."
            ),
        ),
        "intro_email": lambda: (
            _assemble_subject(copy, company, variant),
            _assemble_email(angle_key, observation, company, industry, variant, copy, signature),
            "email",
            "",
        ),
        "followup_email": lambda: (fmt(copy.followup_subjects), fmt(copy.followup_bodies), "email", ""),
        "social_proof_email": lambda: (
            fmt(copy.social_proof_subjects), fmt(copy.social_proof_bodies), "email", "",
        ),
        "linkedin_dm": lambda: (
            "",
            _assemble_dm(angle_key, observation, company, industry, variant, copy, dm_signoff, contact_first_name),
            "linkedin",
            "Send as a LinkedIn DM (you're now connected from the Day 0 request).",
        ),
        "breakup_email": lambda: (fmt(copy.breakup_subjects), fmt(copy.breakup_bodies), "email", ""),
    }


def resolve_touch_plan(tenant: TenantConfig, sequence_variant: str = "") -> tuple[str, list[tuple[str, int]]]:
    """(variant_key, [(touch type, day), ...]) for a run.

    Tenants without sequence_variants get the built-in plan (key ""). An
    unknown requested key falls back to the config default, then the first
    variant — never raises.
    """
    variants = getattr(tenant, "sequence_variants", None)
    if variants is None:
        return "", list(_DEFAULT_TOUCH_PLAN)
    chosen = variants.resolve(sequence_variant)
    return chosen.key, [(t.type, t.day) for t in chosen.touches]


def _build_sequence(
    angle_key: str,
    observation: str,
    company: str,
    industry: str,
    tier: int,
    persona: str,
    tenant: TenantConfig,
    contact_first_name: str = "",
    sequence_variant: str = "",
) -> OutreachSequence:
    """Assemble the outreach sequence from the tenant's active touch plan."""
    angle_idx = ANGLE_KEYS.index(angle_key)
    variant = _variant_index(tenant.tenant_id, company, angle_idx)
    builders = _touch_builders(
        angle_key, observation, company, industry, variant, tenant, contact_first_name
    )

    variant_key, plan = resolve_touch_plan(tenant, sequence_variant)

    touches = []
    for number, (touch_type, day) in enumerate(plan):
        subject, body, channel, note = builders[touch_type]()
        touches.append(
            SequenceTouch(
                touch_number=number,
                day=day,
                channel=channel,
                subject=subject,
                body=body,
                persona=persona,
                word_count=_word_count(body),
                note=note,
            )
        )

    # Built-in plan only: tier-3 accounts drop the social-proof touch (original
    # behavior). Explicit variant plans are used exactly as configured.
    if not variant_key and tier >= 3:
        touches = [t for t in touches if t.touch_number != 3]
        for i, t in enumerate(touches):
            t.touch_number = i

    return OutreachSequence(
        recommended_angle=angle_key,
        entry_persona=persona,
        touches=touches,
    )


# ---------------------------------------------------------------------------
# LangGraph node
# ---------------------------------------------------------------------------
def run_humanizer(state: BDRState) -> dict:
    if state.get("error"):
        return {}

    enrichment = state.get("enrichment")
    strategy = state.get("strategy")
    tenant = state.get("tenant")
    if not enrichment or not strategy:
        return {"error": "Humanizer: missing enrichment or strategy."}
    if tenant is None:
        return {"error": "Humanizer: tenant config missing from state."}

    trace = list(state.get("agent_trace", []))
    trace.append("Humanizer: requesting observations under strict schema (cached prompt)")

    trigger_headline = state.get("trigger_headline", "")
    contact_first_name = ""
    if enrichment.contacts and enrichment.contacts[0].name:
        contact_first_name = enrichment.contacts[0].name.split()[0]

    obs = _generate_observations(
        company=enrichment.company,
        industry=enrichment.industry,
        research_summary=enrichment.research_summary,
        pain_signal=strategy.pain_signal,
        persona=strategy.cpo_hypothesis,
        tenant=tenant,
        trigger_headline=trigger_headline,
        evidence_context=_format_evidence_context(enrichment),
    )

    trace.append("Humanizer: assembling DMs + emails via tenant copy banks")
    company = enrichment.company
    industry = enrichment.industry or "your industry"

    angles = [
        humanize_angle_draft(_build_angle("angle1", obs.angle1_observation, company, industry, tenant, contact_first_name)),
        humanize_angle_draft(_build_angle("angle2", obs.angle2_observation, company, industry, tenant, contact_first_name)),
        humanize_angle_draft(_build_angle("angle3", obs.angle3_observation, company, industry, tenant, contact_first_name)),
    ]

    rec_angle = strategy.recommended_angle
    obs_map = {
        "angle1": obs.angle1_observation,
        "angle2": obs.angle2_observation,
        "angle3": obs.angle3_observation,
    }
    rec_obs = obs_map.get(rec_angle, obs.angle1_observation)
    tier = enrichment.icp.tier if enrichment.icp else 2

    requested_variant = (state.get("sequence_variant") or "").strip()
    variant_key, _plan = resolve_touch_plan(tenant, requested_variant)
    if variant_key:
        trace.append(f"Humanizer: using sequence track '{variant_key}'")

    sequence = _build_sequence(
        angle_key=rec_angle,
        observation=rec_obs,
        company=company,
        industry=industry,
        tier=tier,
        persona=strategy.cpo_hypothesis,
        tenant=tenant,
        contact_first_name=contact_first_name,
        sequence_variant=requested_variant,
    )
    sequence = humanize_sequence(sequence)

    card = ProspectCard(
        before_after=_assemble_before_after(obs.before_text, obs.after_text, tenant),
        angles=angles,
        sequence=sequence,
    )
    trace.append(
        f"Humanizer: card assembled + {len(sequence.touches)}-touch sequence + "
        "29-rule filter applied (incl. Day 0 LinkedIn connect)"
    )
    return {"card": card, "agent_trace": trace}
