# Tenants

A **tenant** is one positioning of the BDR pipeline: a product, an ICP, three
outreach angles, brand strings, and a sender identity. Each tenant lives in
its own folder under `tenants/` and is loaded by name at runtime — either via
the sidebar dropdown in the Streamlit UI, or pinned via the `BDR_TENANT`
environment variable.

This repo ships with one example tenant: **`demo/`** (Acme Analytics, a
fictional B2B SaaS revenue intelligence product). Use it as a template.

---

## Quickstart: add a new tenant

You have two options.

### Option A — Interactive wizard (recommended)

```bash
python scripts/onboard_tenant.py
```

The wizard asks you 8 questions (company name, sender name, business
description, target persona, three angle ideas), then uses Claude to draft a
complete tenant skeleton — `config.yaml`, `icp.txt`, `angles.json`, and
`copy.json` with three variants per copy field. You review and edit; the
wizard writes the folder.

This takes ~3 minutes for a working draft.

### Option B — Copy and edit by hand

```bash
cp -r tenants/demo tenants/my-tenant
```

Then edit each file (see the schema below). Run `python scripts/check_tenant.py
my-tenant` to validate.

---

## Folder layout

```
tenants/<slug>/
  config.yaml          # required — brand, business, persona, sender, CRM
  icp.txt              # required — freeform ICP definition (loaded into UI)
  angles.json          # required — 3 outreach angles (strategist menu)
  copy.json            # required — humanizer copy banks (3 variants per field)
  data/
    prospects.csv      # optional — pre-researched target companies
  logo.png             # optional — sidebar logo
```

The slug `<slug>` must match `^[a-z0-9_-]+$` and is used as the tenant ID.

---

## File reference

### `config.yaml`

```yaml
brand:
  name: Your Brand Name              # required, displayed in UI and prompts
  short_name: Your Brand             # optional, for compact UI
  icon: "🚀"                         # emoji or single character
  tagline: "What you do, in 8 words" # optional
  primary_color: "#000000"           # hex color for accent UI elements

business:
  description: >                     # required — one paragraph, third person
    What the company does, who it's for, what makes it specific. This text
    appears in every system prompt — keep it sharp.
  headline_metric: "30% faster X"    # optional — referenced in copy if present
  reference_customers:                # 2-5 named customers used in proof points
    - Globex Industries
    - Initech

persona:
  title: VP of Sales                  # required — primary target title
  title_alternates:                   # acceptable variants
    - Chief Revenue Officer
    - Head of RevOps
  seniority_filter:                   # Hunter.io filter (defaults shown)
    - c_suite
    - executive
    - director
    - vp

icp:
  tier1_label: "Tier 1 — Strategic"   # human-readable tier labels
  tier2_label: "Tier 2 — Mid-Fit"
  tier3_label: "Tier 3 — Below Threshold"
  tier_criteria: >                    # one-line summary used in ICP prompt
    Tier 1 = $50M+ ARR. Tier 2 = $10–50M. Tier 3 = below.

sender:
  name: Your Name                     # required — used in email signature
  title: GTM Lead                     # optional — appears in sender block
  email_signature: |                  # optional — full multi-line signature
    Your Name
    GTM Lead, Acme Analytics
  dm_signoff: Your Name               # optional — defaults to first name

crm:
  enabled: false                      # show Notion sync UI for this tenant?
  notion_database_id: null            # optional override of NOTION_DATABASE_ID
```

The schema is enforced (`extra="forbid"` — unknown keys raise validation errors).

### `icp.txt`

Plain text. Anything goes. This loads into the UI's ICP editor and into the
ICP classification system prompt verbatim. Treat it like a brief: give the LLM
specific revenue/headcount/technographic criteria, name disqualifiers, list
buying triggers.

### `angles.json`

Array of exactly 3 objects with these keys:

```json
[
  {
    "key": "angle1",
    "name": "Forecast Confidence Gap",
    "tab_label": "Forecast Gap",
    "description": "When this angle fits — one line.",
    "core_insight": "Why it works — 1-3 sentences.",
    "avoid": "What NOT to say with this angle."
  },
  { "key": "angle2", ... },
  { "key": "angle3", ... }
]
```

The strategist sees `name + description + core_insight + avoid` for each angle
and picks one based on enrichment signals.

### `copy.json`

The humanizer's copy banks. Array of 3 objects matching the angle keys. Each
needs **11 fields × 3 variants** (= 33 strings) — the assembler picks one
variant deterministically per (tenant + company) hash, so the same prospect
always gets the same draft.

Required fields per angle:

| Field | Count | Notes |
|---|---|---|
| `proof_points` | 3 | Named customer proof, ~20 words each |
| `email_offers` | 3 | Email closing CTA. May use `{company}`, `{industry}` |
| `dm_offers` | 3 | LinkedIn DM CTA, ~9 words each |
| `subject_templates` | 3 | Email subjects, may use `{company}` |
| `followup_bodies` | 3 | Day-3 email body |
| `followup_subjects` | 3 | Day-3 email subject |
| `social_proof_bodies` | 3 | Day-7 email body, second customer reference |
| `social_proof_subjects` | 3 | Day-7 email subject |
| `breakup_bodies` | 3 | Day-21 breakup email body |
| `breakup_subjects` | 3 | Day-21 breakup email subject |
| `linkedin_connect_notes` | 3 | Day-0 LinkedIn connect, ~200 chars |
| `email_filler_p1/p2/p3` | 1 each | Sentences appended if email is too short |

See `tenants/demo/copy.json` for a complete worked example.

### `data/prospects.csv`

Optional. Header: `company,industry,domain,notes`. Loaded into the sidebar
prospects table. Use it for pre-researched target lists.

---

## Validate your tenant

After editing any file, run:

```bash
python scripts/check_tenant.py <slug>
# or check all tenants:
python scripts/check_tenant.py
```

The script loads the tenant through the Pydantic schema — it'll surface
missing required fields, malformed angles, count mismatches in copy banks,
or invalid hex colors.

---

## How tenant config flows through the pipeline

```
load_tenant("acme") ──► TenantConfig
                            │
                            ▼
            BDRState["tenant"] = TenantConfig
                            │
              ┌─────────────┼──────────────┐
              ▼             ▼              ▼
        Enrichment    Strategist      Humanizer ──► Critic
                                       │
                                       └─► tenant.humanizer_copy.by_key("angle1")
```

Every agent reads `state["tenant"]` — they never touch tenant files directly.
This means you can run different tenants in the same Python process by
passing different `TenantConfig` objects through different state dicts.

---

## Switching tenants at runtime

The Streamlit UI sidebar shows a dropdown with every available tenant. Pick
one and the page reruns with the selected config.

For scripted/CI runs, pin a tenant via env:

```bash
BDR_TENANT=acme streamlit run app/main.py
```

When `BDR_TENANT` is set and matches an existing slug, the dropdown is
disabled.

---

## What the schema does NOT include (yet)

- Multiple sequence variants per tenant (founder track vs. enterprise track)
- Per-tenant LLM model overrides
- Per-tenant Exa query templates
- Localization (all copy is English-only)

These are deliberate cuts for v1. The fastest way to add them is to extend
`app/tenants/schema.py` and propagate the new fields through the agents.
