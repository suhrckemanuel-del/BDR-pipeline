"""
schema.py — Pydantic v2 schema for tenant configuration.

A "tenant" is one positioning of the BDR pipeline: a product, an ICP, three
outreach angles, brand strings, sender identity. Tenants live under
`tenants/<slug>/` at the repo root and contain:

  config.yaml   — brand, sender, persona, business description, ICP tiers, CRM
  icp.txt       — freeform ICP definition (loaded into Streamlit ICP editor)
  angles.json   — list of 3 OutreachAngle objects (strategist menu)
  copy.json     — humanizer copy banks (proof points, CTAs, follow-ups, breakups)
  data/prospects.csv — pre-researched target companies (one row per prospect)
  logo.png      — sidebar logo (optional)

The TenantConfig object is loaded once per Streamlit session, cached, and
threaded through BDRState["tenant"] so every agent reads from the same source.
"""
from __future__ import annotations

from pathlib import Path
from typing import List, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class BrandConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(description="Display name shown in UI and prompts (e.g. 'Acme Corp').")
    short_name: str = Field(
        default="",
        description="Optional shorter name for compact UI elements. Falls back to name.",
    )
    icon: str = Field(default="🚀", description="Emoji or single character icon for sidebar.")
    tagline: str = Field(default="", description="One-line tagline shown below the brand name.")
    primary_color: str = Field(default="#000000", description="Hex color for accent UI elements.")

    @field_validator("primary_color")
    @classmethod
    def _validate_hex(cls, v: str) -> str:
        v = v.strip()
        if not v.startswith("#") or len(v) not in (4, 7):
            raise ValueError(f"primary_color must be a hex string like '#000000', got {v!r}")
        return v


class SenderConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(description="Sender's full name. Used in email signature and DM signoff.")
    title: str = Field(default="", description="Sender title (e.g. 'GTM Lead'). Optional.")
    email_signature: str = Field(
        default="",
        description="Email signature block. Falls back to {name} alone if empty.",
    )
    dm_signoff: str = Field(
        default="",
        description="LinkedIn DM signoff (typically just first name). Falls back to first token of name.",
    )

    def resolved_signature(self) -> str:
        return self.email_signature.strip() or self.name

    def resolved_dm_signoff(self) -> str:
        return self.dm_signoff.strip() or self.name.split()[0]


class CRMConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    enabled: bool = Field(default=False, description="Whether to show Notion sync UI for this tenant.")
    provider: Literal["notion", "hubspot", "salesforce", "pipedrive", "none"] = Field(
        default="notion",
        description="Which CRM the crm_sync node targets. Default preserves the original Notion behavior.",
    )
    notion_database_id: Optional[str] = Field(
        default=None,
        description="If set, overrides the NOTION_DATABASE_ID env var for this tenant.",
    )
    hubspot_token_env: Optional[str] = Field(
        default=None,
        description=(
            "If set, name of the env var holding this tenant's HubSpot private-app token. "
            "Falls back to HUBSPOT_ACCESS_TOKEN. Never put the token itself in config.yaml."
        ),
    )
    salesforce_token_env: Optional[str] = Field(
        default=None,
        description=(
            "If set, name of the env var holding this tenant's Salesforce access token. "
            "Falls back to SALESFORCE_ACCESS_TOKEN. Never put the token itself in config.yaml."
        ),
    )
    salesforce_instance_url: Optional[str] = Field(
        default=None,
        description=(
            "Salesforce instance URL (e.g. https://acme.my.salesforce.com). Not a secret, so it "
            "may live in config; falls back to the SALESFORCE_INSTANCE_URL env var."
        ),
    )
    pipedrive_token_env: Optional[str] = Field(
        default=None,
        description=(
            "If set, name of the env var holding this tenant's Pipedrive API token. "
            "Falls back to PIPEDRIVE_API_TOKEN. Never put the token itself in config.yaml."
        ),
    )


class OutreachToolsConfig(BaseModel):
    """Sending-tool campaign wiring for live push (Instantly / Smartlead).

    Campaign ids identify where pushed leads land. API keys never live in
    config files — *_api_key_env optionally names an env var per tenant,
    falling back to INSTANTLY_API_KEY / SMARTLEAD_API_KEY.
    """
    model_config = ConfigDict(extra="forbid")

    instantly_campaign_id: Optional[str] = Field(
        default=None,
        description="Instantly campaign id that pushed leads join. Unset disables Instantly push.",
    )
    smartlead_campaign_id: Optional[str] = Field(
        default=None,
        description="Smartlead campaign id that pushed leads join. Unset disables Smartlead push.",
    )
    instantly_api_key_env: Optional[str] = Field(
        default=None,
        description="If set, name of the env var holding this tenant's Instantly API key.",
    )
    smartlead_api_key_env: Optional[str] = Field(
        default=None,
        description="If set, name of the env var holding this tenant's Smartlead API key.",
    )


class ScoringWeights(BaseModel):
    """Relative weights for the five account-score components.

    Weights are relative, not percentages — they are normalized at scoring
    time, so any positive mix works. Defaults reproduce the historical
    hardcoded 25/25/20/20/10 split exactly.
    """
    model_config = ConfigDict(extra="forbid")

    icp_fit: float = Field(default=25, description="Weight of the ICP-tier fit component.")
    pain_evidence: float = Field(default=25, description="Weight of observed pain-signal strength.")
    trigger_strength: float = Field(default=20, description="Weight of buying-moment / job-posting intent.")
    contact_confidence: float = Field(default=20, description="Weight of contact seniority/confidence.")
    evidence_quality: float = Field(default=10, description="Weight of overall evidence sourcing quality.")

    @model_validator(mode="after")
    def _check_weights(self) -> "ScoringWeights":
        weights = self.as_map()
        negative = [name for name, w in weights.items() if w < 0]
        if negative:
            raise ValueError(f"scoring weights must be >= 0, got negative: {negative}")
        if sum(weights.values()) <= 0:
            raise ValueError("scoring weights must sum to a positive number")
        return self

    def as_map(self) -> dict[str, float]:
        return {
            "icp_fit": self.icp_fit,
            "pain_evidence": self.pain_evidence,
            "trigger_strength": self.trigger_strength,
            "contact_confidence": self.contact_confidence,
            "evidence_quality": self.evidence_quality,
        }


class ICPConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    tier1_label: str = Field(
        default="Tier 1 — Strategic Fit",
        description="Human-readable label for Tier 1 in the UI.",
    )
    tier2_label: str = Field(default="Tier 2 — Mid-Fit")
    tier3_label: str = Field(default="Tier 3 — Below Threshold")
    tier_criteria: str = Field(
        default="",
        description=(
            "One-line summary of tier criteria, used inside the ICP classification prompt. "
            "Full ICP definition lives in icp.txt."
        ),
    )
    scoring_weights: ScoringWeights = Field(
        default_factory=ScoringWeights,
        description=(
            "Relative weights for the composite 0-100 account score. "
            "Omit to keep the default 25/25/20/20/10 split."
        ),
    )
    exa_query_templates: List[str] = Field(
        default_factory=list,
        description=(
            "Optional Exa search templates with {company}/{industry} placeholders. "
            "The first template runs with 5 results (news slot); the rest run with "
            "3 each (jobs slot). Empty = the built-in persona-driven queries."
        ),
    )


class PersonaConfig(BaseModel):
    """Decision-maker persona that the outreach is targeting."""
    model_config = ConfigDict(extra="forbid")

    title: str = Field(description="Primary target title (e.g. 'Chief Procurement Officer').")
    title_alternates: List[str] = Field(
        default_factory=list,
        description="Acceptable title variants (e.g. ['VP Procurement', 'Head of Sourcing']).",
    )
    seniority_filter: List[str] = Field(
        default_factory=lambda: ["c_suite", "executive", "director", "vp"],
        description="Hunter.io seniority bands to keep when scoring contacts.",
    )


class BusinessConfig(BaseModel):
    """How the LLM should describe the tenant in prompts."""
    model_config = ConfigDict(extra="forbid")

    description: str = Field(
        description=(
            "One paragraph describing what the company does, written in the third person. "
            "This appears in every system prompt — keep it sharp and specific."
        ),
    )
    headline_metric: str = Field(
        default="",
        description=(
            "Headline ROI claim used in copy (e.g. 'up to 90% faster strategy refresh'). "
            "Optional but recommended — the humanizer will reference it if present."
        ),
    )
    reference_customers: List[str] = Field(
        default_factory=list,
        description="2-5 named reference customers used in proof points.",
    )


class OutreachAngle(BaseModel):
    """One of three positioning angles the strategist chooses between."""
    model_config = ConfigDict(extra="forbid")

    key: str = Field(
        pattern=r"^angle[1-3]$",
        description="Stable identifier: angle1, angle2, or angle3.",
    )
    name: str = Field(description="Human-readable angle name (e.g. 'Strategy Speed Gap').")
    tab_label: str = Field(description="Short label for tabbed UI (max ~14 chars).")
    description: str = Field(
        description=(
            "One sentence describing when this angle fits. Used in the strategist prompt menu."
        ),
    )
    core_insight: str = Field(
        description=(
            "1-3 sentences: the strategic reasoning behind the angle. "
            "Tells the LLM why it works, not just when to pick it."
        ),
    )
    avoid: str = Field(
        default="",
        description="What to NOT say with this angle. Helps the LLM stay in the lane.",
    )


class AngleCopy(BaseModel):
    """Copy bank for one angle — feeds the deterministic humanizer assembler."""
    model_config = ConfigDict(extra="forbid")

    key: str = Field(pattern=r"^angle[1-3]$")

    proof_points: List[str] = Field(
        min_length=3,
        max_length=3,
        description="3 variants of the proof point. Pick one deterministically by company hash.",
    )
    email_offers: List[str] = Field(
        min_length=3,
        max_length=3,
        description=(
            "3 variants of the email closing CTA. Use {company} and {industry} placeholders."
        ),
    )
    dm_offers: List[str] = Field(
        min_length=3,
        max_length=3,
        description="3 variants of the LinkedIn DM CTA (shorter, ~9 words each).",
    )
    subject_templates: List[str] = Field(
        min_length=3,
        max_length=3,
        description="3 email subject variants. Use {company} placeholder.",
    )
    followup_bodies: List[str] = Field(
        min_length=3,
        max_length=3,
        description="3 variants of the Day-3 follow-up email body.",
    )
    followup_subjects: List[str] = Field(
        min_length=3,
        max_length=3,
        description="3 variants of the Day-3 follow-up email subject.",
    )
    social_proof_bodies: List[str] = Field(
        min_length=3,
        max_length=3,
        description="3 variants of the Day-7 social proof email body (second customer reference).",
    )
    social_proof_subjects: List[str] = Field(min_length=3, max_length=3)
    breakup_bodies: List[str] = Field(
        min_length=3,
        max_length=3,
        description="3 variants of the Day-21 breakup email body.",
    )
    breakup_subjects: List[str] = Field(min_length=3, max_length=3)
    linkedin_connect_notes: List[str] = Field(
        min_length=3,
        max_length=3,
        description="3 variants of the Day-0 LinkedIn connection request note (~200 chars).",
    )
    email_filler_p1: str = Field(description="Sentence appended to paragraph 1 if email is too short.")
    email_filler_p2: str = Field(description="Sentence appended to paragraph 2 if email is too short.")
    email_filler_p3: str = Field(description="Sentence appended to paragraph 3 if email is too short.")


class HumanizerCopy(BaseModel):
    """All copy banks — one entry per angle key."""
    model_config = ConfigDict(extra="forbid")

    angles: List[AngleCopy] = Field(min_length=3, max_length=3)

    @field_validator("angles")
    @classmethod
    def _check_keys(cls, v: List[AngleCopy]) -> List[AngleCopy]:
        keys = sorted(a.key for a in v)
        expected = ["angle1", "angle2", "angle3"]
        if keys != expected:
            raise ValueError(f"angles must have keys {expected}, got {keys}")
        return v

    def by_key(self, key: str) -> AngleCopy:
        for a in self.angles:
            if a.key == key:
                return a
        raise KeyError(f"No copy for angle {key!r}")


SEQUENCE_TOUCH_TYPES = (
    "linkedin_connect",
    "intro_email",
    "followup_email",
    "social_proof_email",
    "linkedin_dm",
    "breakup_email",
)


class SequenceTouchPlan(BaseModel):
    """One planned touch in a sequence variant — a copy-bank slot plus a day."""
    model_config = ConfigDict(extra="forbid")

    type: Literal[
        "linkedin_connect",
        "intro_email",
        "followup_email",
        "social_proof_email",
        "linkedin_dm",
        "breakup_email",
    ] = Field(description="Which copy bank fills this touch.")
    day: int = Field(ge=0, description="Day offset from sequence start.")


class SequenceVariant(BaseModel):
    """One named sequence track (e.g. founder vs. enterprise)."""
    model_config = ConfigDict(extra="forbid")

    key: str = Field(
        pattern=r"^[a-z0-9_-]+$",
        description="Stable URL-safe identifier for this track.",
    )
    name: str = Field(description="Human-readable track name shown in the UI.")
    description: str = Field(default="", description="One line on when to use this track.")
    touches: List[SequenceTouchPlan] = Field(
        min_length=1,
        description="Touch plan, in send order. Days must be non-decreasing.",
    )

    @field_validator("touches")
    @classmethod
    def _check_days_ordered(cls, v: List[SequenceTouchPlan]) -> List[SequenceTouchPlan]:
        days = [t.day for t in v]
        if days != sorted(days):
            raise ValueError(f"touch days must be non-decreasing, got {days}")
        return v


class SequenceVariantsConfig(BaseModel):
    """Optional named sequence tracks. Absent = the built-in 6-touch plan."""
    model_config = ConfigDict(extra="forbid")

    default: Optional[str] = Field(
        default=None,
        description="Key of the track used when a run doesn't pick one. Defaults to the first.",
    )
    variants: List[SequenceVariant] = Field(min_length=1)

    @model_validator(mode="after")
    def _check_keys(self) -> "SequenceVariantsConfig":
        keys = [v.key for v in self.variants]
        if len(keys) != len(set(keys)):
            raise ValueError(f"variant keys must be unique, got {keys}")
        if self.default is not None and self.default not in keys:
            raise ValueError(f"default {self.default!r} is not a variant key ({keys})")
        return self

    def by_key(self, key: str) -> Optional[SequenceVariant]:
        return next((v for v in self.variants if v.key == key), None)

    def resolve(self, key: str = "") -> SequenceVariant:
        """The variant for a requested key, falling back to default, then first."""
        return (
            (self.by_key(key) if key else None)
            or (self.by_key(self.default) if self.default else None)
            or self.variants[0]
        )


class ModelsConfig(BaseModel):
    """Optional per-agent Claude model overrides.

    Unset agents keep their hardcoded default (see each agent module's model
    constant), so omitting this block changes nothing.
    """
    model_config = ConfigDict(extra="forbid")

    enrichment: Optional[str] = Field(
        default=None, description="Model for enrichment summaries + ICP classification (default: Haiku)."
    )
    strategist: Optional[str] = Field(
        default=None, description="Model for angle selection (default: Sonnet)."
    )
    humanizer: Optional[str] = Field(
        default=None, description="Model for observation generation (default: Sonnet)."
    )
    critic: Optional[str] = Field(
        default=None, description="Model for sequence critique + rewrites (default: Sonnet)."
    )


class TenantConfig(BaseModel):
    """Top-level tenant config. One per `tenants/<slug>/` folder."""
    model_config = ConfigDict(extra="forbid", arbitrary_types_allowed=True)

    tenant_id: str = Field(
        pattern=r"^[a-z0-9_-]+$",
        description="URL-safe slug matching the folder name under tenants/.",
    )
    brand: BrandConfig
    business: BusinessConfig
    persona: PersonaConfig
    icp: ICPConfig = Field(default_factory=ICPConfig)
    sender: SenderConfig
    crm: CRMConfig = Field(default_factory=CRMConfig)
    outreach: OutreachToolsConfig = Field(default_factory=OutreachToolsConfig)
    angles: List[OutreachAngle] = Field(min_length=3, max_length=3)
    models: ModelsConfig = Field(default_factory=ModelsConfig)
    sequence_variants: Optional[SequenceVariantsConfig] = Field(
        default=None,
        description=(
            "Optional named sequence tracks (e.g. founder vs. enterprise). "
            "Absent keeps the built-in 6-touch plan."
        ),
    )

    # Loaded from sibling files, not config.yaml itself
    icp_definition: str = Field(default="", description="Loaded from icp.txt.")
    humanizer_copy: HumanizerCopy = Field(description="Loaded from copy.json.")
    root_dir: Path = Field(description="Absolute path to tenants/<tenant_id>/.")

    @field_validator("angles")
    @classmethod
    def _check_angle_keys(cls, v: List[OutreachAngle]) -> List[OutreachAngle]:
        keys = sorted(a.key for a in v)
        if keys != ["angle1", "angle2", "angle3"]:
            raise ValueError(f"angles must have keys angle1/angle2/angle3, got {keys}")
        return v

    def angle_by_key(self, key: str) -> OutreachAngle:
        for a in self.angles:
            if a.key == key:
                return a
        raise KeyError(f"No angle {key!r} in tenant {self.tenant_id!r}")

    @property
    def prospects_csv(self) -> Path:
        return self.root_dir / "data" / "prospects.csv"

    @property
    def logo_path(self) -> Optional[Path]:
        p = self.root_dir / "logo.png"
        return p if p.exists() else None
