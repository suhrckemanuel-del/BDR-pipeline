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
from typing import List, Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator


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
    notion_database_id: Optional[str] = Field(
        default=None,
        description="If set, overrides the NOTION_DATABASE_ID env var for this tenant.",
    )


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
    angles: List[OutreachAngle] = Field(min_length=3, max_length=3)

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
