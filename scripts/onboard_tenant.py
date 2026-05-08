"""
onboard_tenant.py — Interactive wizard to scaffold a new tenant under tenants/.

Usage:
    python scripts/onboard_tenant.py
    python scripts/onboard_tenant.py --slug my-tenant --no-llm   # skeleton only

The wizard asks 8 questions, then calls Claude to draft the long-form content
(ICP definition, three angle objects, three copy banks). You review the output
in your editor before committing.

If --no-llm is set or ANTHROPIC_API_KEY is missing, the wizard writes a plain
skeleton with TODO markers — you fill in the copy by hand.
"""
from __future__ import annotations

import argparse
import io
import json
import os
import re
import sys
from pathlib import Path
from typing import Any

# Force UTF-8 stdout so emoji prompts display on Windows consoles.
if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

# Make app/ importable when run from repo root
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

# ---------------------------------------------------------------------------
# Q&A helpers
# ---------------------------------------------------------------------------
def ask(prompt: str, default: str = "", required: bool = False) -> str:
    suffix = f" [{default}]" if default else ""
    while True:
        val = input(f"{prompt}{suffix}: ").strip()
        if not val:
            val = default
        if val or not required:
            return val
        print("  (required — try again)")


def ask_multiline(prompt: str) -> str:
    print(f"{prompt}")
    print("  (paste freely. Enter a blank line when done.)")
    lines: list[str] = []
    while True:
        line = input("  > ")
        if not line and (not lines or not lines[-1].strip()):
            break
        lines.append(line)
    return "\n".join(lines).strip()


def slugify(s: str) -> str:
    s = s.lower().strip()
    s = re.sub(r"[^a-z0-9_-]+", "-", s)
    s = re.sub(r"-+", "-", s).strip("-")
    return s or "tenant"


# ---------------------------------------------------------------------------
# Claude draft generation
# ---------------------------------------------------------------------------
DRAFT_SYSTEM = """\
You are an expert B2B GTM strategist helping configure a tenant for a BDR
outreach pipeline. Given a freeform brief about a company, produce:

  1. An ICP definition (50-80 lines plain text)
  2. Three OutreachAngle objects (JSON)
  3. Three AngleCopy objects (JSON) with EXACTLY 3 variants per copy field

Be concrete. Cite real customer examples if the user provided them. Avoid
buzzwords (transformative, leverage, seamlessly, revolutionize, ecosystem).

Output as a single JSON object with three top-level keys:
{
  "icp_text": "...",
  "angles": [ {OutreachAngle}, {OutreachAngle}, {OutreachAngle} ],
  "copy":   [ {AngleCopy},     {AngleCopy},     {AngleCopy}     ]
}

OutreachAngle schema:
  - key: "angle1" | "angle2" | "angle3"
  - name: short human-readable name
  - tab_label: ~14 char label
  - description: 1 sentence (when this angle fits)
  - core_insight: 1-3 sentences (why it works)
  - avoid: 1 sentence (what NOT to say)

AngleCopy schema (every list field must have EXACTLY 3 strings):
  - key: matches the angle key
  - proof_points: 3 variants citing a named customer
  - email_offers: 3 closing CTAs (may use {company}, {industry})
  - dm_offers: 3 LinkedIn DM CTAs (~9 words)
  - subject_templates: 3 email subjects (may use {company})
  - followup_bodies: 3 Day-3 follow-up bodies
  - followup_subjects: 3 Day-3 subjects
  - social_proof_bodies: 3 Day-7 bodies (second customer reference)
  - social_proof_subjects: 3 Day-7 subjects
  - breakup_bodies: 3 Day-21 breakup bodies
  - breakup_subjects: 3 Day-21 subjects
  - linkedin_connect_notes: 3 connection request notes (~200 chars)
  - email_filler_p1, email_filler_p2, email_filler_p3: 1 sentence each

Copy variants should sound like a real human — short sentences, plain words,
no formulaic structure. Each variant should differ in opener and rhythm.
"""


def call_claude_for_draft(brief: dict[str, Any]) -> dict[str, Any]:
    """Call Claude to generate icp.txt + angles.json + copy.json content."""
    from anthropic import Anthropic  # type: ignore

    client = Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])
    user_msg = (
        f"Brand: {brief['brand_name']}\n"
        f"Tagline: {brief.get('tagline', '(none)')}\n"
        f"Business description:\n{brief['business_description']}\n\n"
        f"Reference customers: {', '.join(brief.get('reference_customers', [])) or '(none)'}\n"
        f"Headline metric: {brief.get('headline_metric', '(none)')}\n\n"
        f"Target persona: {brief['persona_title']}\n"
        f"Persona alternates: {', '.join(brief.get('persona_alternates', [])) or '(none)'}\n\n"
        f"Three angle ideas (raw user input):\n{brief['angle_brief']}\n\n"
        "Produce the JSON object as specified. Output ONLY valid JSON — no preamble, "
        "no markdown fences, no trailing commentary."
    )

    print("  Calling Claude (this takes ~30 seconds)...")
    resp = client.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=8000,
        system=DRAFT_SYSTEM,
        messages=[{"role": "user", "content": user_msg}],
    )
    text = resp.content[0].text if resp.content else ""

    # Strip markdown fences if Claude added them despite instructions
    text = re.sub(r"^```(?:json)?\s*", "", text.strip())
    text = re.sub(r"\s*```\s*$", "", text)

    try:
        return json.loads(text)
    except json.JSONDecodeError as exc:
        print(f"\n  Claude returned non-JSON. First 400 chars:\n  {text[:400]}\n")
        raise SystemExit(f"Wizard aborted: {exc}")


def skeleton_draft() -> dict[str, Any]:
    """Fallback: empty skeleton with TODO markers."""
    angle_keys = ("angle1", "angle2", "angle3")
    return {
        "icp_text": "TODO — write 50-80 lines of ICP definition here. See tenants/demo/icp.txt for the structure.",
        "angles": [
            {
                "key": k,
                "name": f"TODO Angle {i+1}",
                "tab_label": f"Angle {i+1}",
                "description": "TODO — when this angle fits, one sentence.",
                "core_insight": "TODO — why it works, 1-3 sentences.",
                "avoid": "TODO — what not to say.",
            }
            for i, k in enumerate(angle_keys)
        ],
        "copy": [
            {
                "key": k,
                "proof_points": ["TODO proof 1", "TODO proof 2", "TODO proof 3"],
                "email_offers": [
                    "TODO email CTA 1 for {company}",
                    "TODO email CTA 2 for {company}",
                    "TODO email CTA 3 for {company}",
                ],
                "dm_offers": [
                    "TODO DM 1 for {company}",
                    "TODO DM 2 for {company}",
                    "TODO DM 3 for {company}",
                ],
                "subject_templates": [
                    "TODO subject 1 at {company}",
                    "TODO subject 2 at {company}",
                    "TODO subject 3 at {company}",
                ],
                "followup_bodies": ["TODO followup 1", "TODO followup 2", "TODO followup 3"],
                "followup_subjects": ["TODO 1", "TODO 2", "TODO 3"],
                "social_proof_bodies": ["TODO 1", "TODO 2", "TODO 3"],
                "social_proof_subjects": ["TODO 1", "TODO 2", "TODO 3"],
                "breakup_bodies": ["TODO 1", "TODO 2", "TODO 3"],
                "breakup_subjects": ["TODO 1", "TODO 2", "TODO 3"],
                "linkedin_connect_notes": ["TODO 1", "TODO 2", "TODO 3"],
                "email_filler_p1": "TODO filler sentence 1.",
                "email_filler_p2": "TODO filler sentence 2.",
                "email_filler_p3": "TODO filler sentence 3.",
            }
            for k in angle_keys
        ],
    }


# ---------------------------------------------------------------------------
# YAML output — render via PyYAML so we never have to fight indentation by hand
# ---------------------------------------------------------------------------
def write_config_yaml(path: Path, brief: dict[str, Any]) -> None:
    import yaml  # PyYAML

    config = {
        "brand": {
            "name": brief["brand_name"],
            "short_name": brief.get("short_name", brief["brand_name"]),
            "icon": brief.get("icon", "🚀"),
            "tagline": brief.get("tagline", ""),
            "primary_color": brief.get("primary_color", "#000000"),
        },
        "business": {
            "description": brief["business_description"],
            "headline_metric": brief.get("headline_metric", ""),
            "reference_customers": list(brief.get("reference_customers", [])),
        },
        "persona": {
            "title": brief["persona_title"],
            "title_alternates": list(brief.get("persona_alternates", [])),
            "seniority_filter": ["c_suite", "executive", "director", "vp"],
        },
        "icp": {
            "tier1_label": brief.get("tier1_label", "Tier 1 — Strategic Fit"),
            "tier2_label": brief.get("tier2_label", "Tier 2 — Mid-Fit"),
            "tier3_label": brief.get("tier3_label", "Tier 3 — Below Threshold"),
            "tier_criteria": brief.get("tier_criteria", ""),
        },
        "sender": {
            "name": brief["sender_name"],
            "title": brief.get("sender_title", ""),
            "email_signature": brief.get("email_signature", ""),
            "dm_signoff": brief.get("dm_signoff", brief["sender_name"].split()[0]),
        },
        "crm": {
            "enabled": False,
            "notion_database_id": None,
        },
    }
    header = "# Generated by scripts/onboard_tenant.py — review and edit before shipping.\n\n"
    body = yaml.safe_dump(
        config,
        sort_keys=False,
        allow_unicode=True,
        default_flow_style=False,
        width=120,
    )
    path.write_text(header + body, encoding="utf-8")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
def main() -> int:
    parser = argparse.ArgumentParser(description="Scaffold a new BDR tenant under tenants/.")
    parser.add_argument("--slug", help="Tenant slug (folder name). Asked interactively if omitted.")
    parser.add_argument("--no-llm", action="store_true", help="Skip Claude draft; write skeleton only.")
    parser.add_argument("--force", action="store_true", help="Overwrite existing tenant folder if it exists.")
    args = parser.parse_args()

    # Try to load .env if available
    try:
        from dotenv import load_dotenv  # type: ignore
        load_dotenv(ROOT / ".env")
    except ImportError:
        pass

    print("─" * 60)
    print("  BDR Tenant Onboarding Wizard")
    print("─" * 60)
    print()

    brand_name = ask("Brand / company name", required=True)
    slug = args.slug or slugify(ask("Tenant slug (folder name)", default=slugify(brand_name)))

    target_dir = ROOT / "tenants" / slug
    if target_dir.exists() and not args.force:
        print(f"\n  Tenant folder already exists: {target_dir}")
        print("  Re-run with --force to overwrite, or pick a different slug.")
        return 1

    print(f"\n  Slug: {slug}")
    print(f"  Will write to: {target_dir}\n")

    short_name = ask("Short name (for compact UI)", default=brand_name)
    icon = ask("Icon (emoji or single char)", default="🚀")
    tagline = ask("Tagline (one line, optional)")
    primary_color = ask("Primary hex color", default="#2563EB")

    print()
    business_description = ask_multiline(
        "Describe what the company does (one paragraph, third person):"
    )
    if not business_description:
        print("  (required — re-run when you have this written)")
        return 1

    headline_metric = ask("Headline metric (e.g. 'up to 90% faster X', optional)")

    print()
    print("Reference customers — comma-separated, 2-5 names. Leave blank to skip.")
    customers_raw = ask("Customers")
    reference_customers = [c.strip() for c in customers_raw.split(",") if c.strip()]

    print()
    persona_title = ask("Target persona title", required=True, default="VP of Sales")
    print("Persona alternates — comma-separated. Leave blank to skip.")
    alternates_raw = ask("Alternates")
    persona_alternates = [a.strip() for a in alternates_raw.split(",") if a.strip()]

    print()
    sender_name = ask("Sender name (used in email signature)", required=True, default="Your Name")
    sender_title = ask("Sender title (optional)")
    email_signature = ask_multiline(
        f"Email signature (multi-line, optional — defaults to '{sender_name}'):"
    )

    print()
    angle_brief = ask_multiline(
        "Describe your three outreach angles in plain English. "
        "Tell me when each one fits, and what makes each work. "
        "Don't worry about format — Claude will structure it:"
    )
    if not angle_brief and not args.no_llm:
        print("  (warning: no angle brief provided — angles will be drafted from business description alone)")

    # ----- Generate draft content -----
    use_llm = (not args.no_llm) and bool(os.environ.get("ANTHROPIC_API_KEY"))
    print()
    if use_llm:
        brief = {
            "brand_name": brand_name,
            "tagline": tagline,
            "business_description": business_description,
            "reference_customers": reference_customers,
            "headline_metric": headline_metric,
            "persona_title": persona_title,
            "persona_alternates": persona_alternates,
            "angle_brief": angle_brief or "(no specific angles provided — invent three plausible ones based on the business description)",
        }
        try:
            draft = call_claude_for_draft(brief)
        except Exception as exc:
            print(f"  Claude draft failed: {exc}")
            print("  Falling back to skeleton.")
            draft = skeleton_draft()
    else:
        if args.no_llm:
            print("  --no-llm set — writing skeleton only.")
        else:
            print("  ANTHROPIC_API_KEY not set — writing skeleton only.")
        draft = skeleton_draft()

    # ----- Write files -----
    target_dir.mkdir(parents=True, exist_ok=True)
    (target_dir / "data").mkdir(exist_ok=True)

    write_config_yaml(
        target_dir / "config.yaml",
        {
            "brand_name": brand_name,
            "short_name": short_name,
            "icon": icon,
            "tagline": tagline,
            "primary_color": primary_color,
            "business_description": business_description,
            "headline_metric": headline_metric,
            "reference_customers": reference_customers,
            "persona_title": persona_title,
            "persona_alternates": persona_alternates,
            "sender_name": sender_name,
            "sender_title": sender_title,
            "email_signature": email_signature,
        },
    )

    (target_dir / "icp.txt").write_text(draft["icp_text"], encoding="utf-8")
    (target_dir / "angles.json").write_text(
        json.dumps(draft["angles"], indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    (target_dir / "copy.json").write_text(
        json.dumps(draft["copy"], indent=2, ensure_ascii=False),
        encoding="utf-8",
    )

    # Empty prospects.csv with the expected header
    prospects_path = target_dir / "data" / "prospects.csv"
    if not prospects_path.exists():
        prospects_path.write_text("company,industry,domain,notes\n", encoding="utf-8")

    print()
    print("─" * 60)
    print(f"  Wrote tenant: tenants/{slug}/")
    print("    config.yaml")
    print("    icp.txt")
    print("    angles.json")
    print("    copy.json")
    print("    data/prospects.csv")
    print("─" * 60)
    print()

    # ----- Validate -----
    print("  Validating tenant...")
    try:
        from app.tenants import load_tenant
        # Bust the lru_cache so we read the just-written files
        load_tenant.cache_clear()  # type: ignore[attr-defined]
        t = load_tenant(slug)
        print(f"  ✓ Tenant loads cleanly: {t.brand.name} ({t.persona.title})")
        print(f"  ✓ Angles: {[a.name for a in t.angles]}")
        print()
        print(f"  Try it: BDR_TENANT={slug} streamlit run app/main.py")
    except Exception as exc:
        print(f"  ✗ Validation failed: {exc}")
        print(f"  Edit files under tenants/{slug}/ and run: python scripts/check_tenant.py {slug}")
        return 1

    return 0


if __name__ == "__main__":
    sys.exit(main())
