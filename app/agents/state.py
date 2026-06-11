"""
state.py — Shared state and Pydantic schemas for the BDR multi-agent workflow.

The BDRState dict is the single object passed between LangGraph nodes:

    Enrichment  ->  Strategist  ->  Humanizer  ->  Critic  ->  CRM Sync

Each agent reads from the state, mutates one slice, and returns a partial dict
that LangGraph merges in. Pydantic models give us guaranteed JSON schemas for
the LLM tool calls — no regex parsing anywhere.

Tenant configuration (positioning, angles, copy banks, persona) is loaded once
in the UI and passed through state["tenant"]. Agents must never read tenant
files directly — always go through the TenantConfig.
"""
from __future__ import annotations

from typing import Any, List, Literal, Optional, TypedDict

from pydantic import BaseModel, ConfigDict, Field

from app.tenants.schema import TenantConfig

# Stable angle identifiers — every tenant MUST define exactly these three keys.
ANGLE_KEYS = ("angle1", "angle2", "angle3")


# ---------------------------------------------------------------------------
# Enrichment schemas — Exa + Hunter.io + ICP
# ---------------------------------------------------------------------------
class LiveSignal(BaseModel):
    """A single live web signal pulled from Exa."""
    title: str = ""
    url: str = ""
    snippet: str = ""
    signal_source: str = ""
    signal_type: str = ""


class ContactLead(BaseModel):
    """A single contact pulled from Hunter.io domain search."""
    name: str = ""
    email: str = ""
    position: str = ""
    seniority: str = ""
    department: str = ""
    confidence: int = 0
    linkedin_url: str = ""


class EvidenceCard(BaseModel):
    """Evidence-backed claim surfaced from enrichment data."""
    evidence_id: str
    claim: str = ""
    source_title: str = ""
    source_url: str = ""
    source_type: Literal["live_signal", "job_signal", "contact", "icp_score", "manual_trigger"]
    support_type: Literal["observed", "derived", "inferred"]
    confidence_label: Literal["high", "medium", "low"]
    confidence_score: int = Field(default=0, ge=0, le=100)
    excerpt: str = ""
    safe_to_use: bool = False
    used_in_outreach: bool = False
    notes: str = ""


class ScoreComponent(BaseModel):
    """One transparent component of the account-readiness score."""
    label: str
    score: int = Field(default=0, ge=0, le=5)
    rationale: str
    evidence_ids: List[str] = []


class AccountScoringResult(BaseModel):
    """Transparent account-readiness score for founder review."""
    overall_score: int = Field(default=0, ge=0, le=100)
    priority_label: Literal["high_priority", "review", "needs_more_research", "do_not_send_yet"]
    icp_fit: ScoreComponent
    pain_evidence: ScoreComponent
    trigger_strength: ScoreComponent
    contact_confidence: ScoreComponent
    evidence_quality: ScoreComponent
    recommended_action: str
    warnings: List[str] = []


class TargetProfile(BaseModel):
    """User-provided person context for hyper-personalized outreach."""
    name: str = ""
    title: str = ""
    company: str = ""
    linkedin_url: str = ""
    notes: str = Field(
        default="",
        description="Profile headline/about/recent post notes supplied by the user.",
    )
    source: str = "manual_linkedin"


class ICPClassification(BaseModel):
    """The Enrichment agent's ICP-tier judgement with composite 0-100 score."""

    tier: Literal[1, 2, 3] = Field(
        description="ICP tier: 1=Strategic Fit, 2=Mid-Fit, 3=Below Threshold. Tenant-specific labels and criteria are loaded from tenant config.",
    )
    tier_label: str = Field(
        description="Human-readable tier label.",
        examples=["Tier 1 — Strategic Fit", "Tier 2 — Mid-Fit", "Tier 3 — Below Threshold"],
    )
    rationale: str = Field(
        description=(
            "One sentence (max 30 words) citing specific evidence used. "
            "No generic statements."
        ),
    )
    score: int = Field(default=50, description="Composite ICP fit score 0-100.")
    score_breakdown: dict = Field(
        default_factory=dict,
        description="Score components: industry_fit, technographic, intent_signals, contact_quality.",
    )


class EnrichmentResult(BaseModel):
    """Output of the Enrichment agent."""
    model_config = ConfigDict(extra="ignore")

    company: str
    industry: str
    domain: str = ""
    live_signals: List[LiveSignal] = []
    job_signals: List[LiveSignal] = []
    contacts: List[ContactLead] = []
    evidence_cards: List[EvidenceCard] = []
    account_score: Optional[AccountScoringResult] = None
    icp: Optional[ICPClassification] = None
    research_summary: str = Field(
        default="", description="LLM-synthesised view of the live signals through the persona lens."
    )
    intent_score: int = Field(default=0, description="Composite intent score 0-100 from signal mix.")
    intent_top_trigger: str = Field(default="", description="Highest-weight trigger description.")
    intent_breakdown: dict = Field(default_factory=dict, description="Component scores by signal type.")


# ---------------------------------------------------------------------------
# Strategist schema
# ---------------------------------------------------------------------------
class StrategyDecision(BaseModel):
    """The Strategist agent's output — chosen angle plus rationale."""

    recommended_angle: Literal["angle1", "angle2", "angle3"] = Field(
        description="One of three tenant-configured outreach angles. See tenant.angles for the menu shown to the LLM.",
    )
    angle_name: str = Field(
        description="Human-readable name matching the chosen angle, copied from tenant.angles[i].name.",
    )
    rationale: str = Field(
        description=(
            "2-3 sentences grounded in the enrichment data. "
            "Cite specific signals (company news, ICP tier, technographics). "
            "No generic platitudes."
        ),
        min_length=40,
    )
    cpo_hypothesis: str = Field(
        description="Most likely decision-maker title at this company (matches tenant.persona context).",
    )
    pain_signal: str = Field(
        description=(
            "One sentence naming the specific pain at this company today. "
            "Reference an actual operating constraint or recent event."
        ),
    )


# ---------------------------------------------------------------------------
# Humanizer schemas
# ---------------------------------------------------------------------------
class HumanizerObservations(BaseModel):
    """
    LLM-only output from the Humanizer agent.

    The LLM produces ONLY short, specific observations (one per angle) and the
    Before/After narrative. Proof points and offer templates come from the
    tenant's copy banks — not from creative LLM writing. This is the anti-AI
    guarantee.
    """
    angle1_observation: str = Field(
        description=(
            "One sentence (max ~22 words) naming the angle-1 pain at this company. "
            "If the research summary contains a dated external trigger, lead with it. "
            "Must be falsifiable — specific to this company."
        ),
    )
    angle2_observation: str = Field(
        description=(
            "One sentence (max ~22 words) naming the angle-2 pain at this company. "
            "Must be falsifiable — specific to this company."
        ),
    )
    angle3_observation: str = Field(
        description=(
            "One sentence (max ~22 words) naming the angle-3 pain at this company. "
            "Must be falsifiable — specific to this company."
        ),
    )
    before_text: str = Field(
        description="One short paragraph: how this company operates today. Concrete pain.",
    )
    after_text: str = Field(
        description=(
            "One short paragraph describing the outcome with the tenant's product. "
            "Reference the headline metric from tenant.business if relevant. "
            "Must NOT start with 'With <Brand>:' — the assembler prepends that."
        ),
    )


class AngleDraft(BaseModel):
    """Final assembled draft for one angle."""
    angle_key: str
    name: str
    tab_label: str
    dm: str
    email_subject: str
    email_body: str


class SequenceTouch(BaseModel):
    """One touch in a multi-step outreach sequence."""
    touch_number: int
    day: int
    channel: str  # "email" | "linkedin" | "linkedin_connect"
    subject: str = ""
    body: str = ""
    cta: str = ""
    persona: str = ""
    word_count: int = 0
    note: str = ""


class OutreachSequence(BaseModel):
    """Full multi-touch sequence for one prospect."""
    recommended_angle: str
    entry_persona: str
    touches: List[SequenceTouch]


class ProspectCard(BaseModel):
    """Final assembled card — Before/After + 3 angle drafts + optional sequence."""
    before_after: str
    angles: List[AngleDraft]
    sequence: Optional[OutreachSequence] = None


# ---------------------------------------------------------------------------
# CRM sync schema
# ---------------------------------------------------------------------------
class CRMSyncResult(BaseModel):
    """Output of the Notion CRM connector."""
    success: bool
    page_id: str = ""
    page_url: str = ""
    error: str = ""
    skipped: bool = False
    skip_reason: str = ""


# ---------------------------------------------------------------------------
# BDRState — the shared mutable state passed between LangGraph nodes
# ---------------------------------------------------------------------------
class BDRState(TypedDict, total=False):
    """
    Filled progressively across the workflow.

    Tenant config (positioning, angles, copy banks) is set once at workflow
    start and never mutated. Every agent reads `state["tenant"]`.
    """
    # Tenant config (immutable across the run)
    tenant: TenantConfig

    # Inputs
    company: str
    industry: str
    sync_to_notion: bool
    trigger_headline: str
    prospect_notes: str
    target_profile: TargetProfile

    # Agent outputs
    enrichment: EnrichmentResult
    strategy: StrategyDecision
    card: ProspectCard
    crm_result: CRMSyncResult

    # Critic loop
    critic_result: Any
    critic_score: int
    critic_feedback: str
    critic_retries: int

    # Run state
    error: Optional[str]
    agent_trace: List[str]
