# BDR Pipeline — Claude Code Context

## Project Overview

White-label, tenant-driven BDR (Business Development Representative) automation pipeline. Takes a company name → runs a 5-step LangGraph workflow → produces a full 5-touch outreach sequence with company-specific pain signals, proof points, and humanized copy.

All positioning (brand, ICP, angles, copy banks, sender identity) lives in `tenants/<slug>/` config files. The pipeline code is brand-agnostic — adding a new tenant is a config-only operation.

The repo ships with one example tenant: **`demo/`** (Acme Analytics, a fictional B2B SaaS revenue intelligence product).

**Owner:** Manuel Suhrcke

---

## Tech Stack

- **Python 3.x**
- **Streamlit** — single-page dashboard UI (`app/main.py`)
- **LangGraph** — agentic workflow orchestration (5 linear nodes)
- **Anthropic Claude API** — every agent uses structured outputs via `llm.with_structured_output()`
- **Pydantic v2** — schema validation everywhere; `extra="forbid"` on tenant config so unknown keys fail loud
- **Exa.ai** — live web signals (news, job postings)
- **Hunter.io** — domain resolution + contact discovery
- **Notion** — optional CRM sync (per-tenant)
- **Gmail** — optional outbound (App Password auth)
- **pandas** — CSV handling
- No database — file-based state (CSV + JSON)

---

## Running the App

```bash
python -m venv .venv
.venv\Scripts\activate          # Windows
pip install -r requirements.txt

cp .env.example .env             # then fill in ANTHROPIC_API_KEY, EXA_API_KEY, HUNTER_API_KEY

streamlit run app/main.py
```

The UI opens at `http://localhost:8501`. Sidebar dropdown selects the tenant; default is whatever `list_tenants()` returns first (`demo` in a fresh clone).

To pin a tenant: `BDR_TENANT=demo streamlit run app/main.py`.

---

## Project Structure

```
app/
  main.py                       # Streamlit entry point
  agents/
    state.py                    # BDRState TypedDict + all Pydantic schemas
    workflow_engine.py          # LangGraph DAG: enrichment → strategist → humanizer → critic → crm_sync
    enrichment.py               # Exa + Hunter + ICP classification
    strategist.py               # Picks 1 of 3 tenant angles
    humanizer.py                # LLM observations + deterministic copy assembly + 5-touch sequence
    critic.py                   # 4-dim LLM scoring + per-touch first-paragraph rewrites
  services/
    crm_sync.py                 # Notion push (schema-agnostic, per-tenant DB override)
    gmail_sender.py             # Per-tenant queue at tenants/<id>/data/queued/
    humanizer_rules.py          # 29-rule anti-AI regex filter
  tenants/
    schema.py                   # Pydantic v2 TenantConfig
    loader.py                   # load_tenant() — cached, validates on read
    __init__.py                 # exports load_tenant, list_tenants

tenants/
  README.md                     # Tenant author guide (full schema reference)
  demo/
    config.yaml                 # Brand, business, persona, sender, ICP labels, CRM
    icp.txt                     # Freeform ICP definition
    angles.json                 # 3 outreach angles
    copy.json                   # Humanizer copy banks (3 angles × 11 fields × 3 variants)
    data/prospects.csv          # Pre-researched targets

scripts/
  onboard_tenant.py             # Interactive Claude-assisted tenant wizard
  check_tenant.py               # Pydantic-based tenant validator
  send_via_gmail.py             # Send queued sequences for a tenant
```

---

## 5-Step Pipeline Workflow

```
Input: company name + industry (+ optional trigger_headline) + active TenantConfig
  ↓
[1. Enrichment]  app/agents/enrichment.py
    - Exa: 5 live signals (news, press, job postings)
    - Hunter.io: resolve domain + senior exec contacts (filtered by tenant.persona.seniority_filter)
    - Claude: research summary through tenant.persona.title lens
    - Claude: ICP Tier 1/2/3 classification (structured output, uses tenant.icp.*)
  ↓
[2. Strategist]  app/agents/strategist.py
    - Claude picks 1 of 3 tenant.angles via StrategyDecision schema
    - Outputs: recommended_angle, pain_signal, cpo_hypothesis, rationale
  ↓
[3. Humanizer]  app/agents/humanizer.py
    - Claude: 3 observations (one per angle) + before/after narrative — observations only
    - Deterministic assembly: observation + tenant.humanizer_copy.by_key(...) banks + filler
    - Builds 5-touch sequence (LinkedIn connect → email → follow-up → social proof → breakup)
    - Applies humanizer_rules.py 29-rule regex filter
  ↓
[4. Critic]  app/agents/critic.py
    - Claude scores each touch on 4 dims: pain specificity, proof relevance, CTA clarity, human voice
    - Rewrites the first paragraph of any email touch with a failing dimension
    - No retry loop — in-place rewrites, then forward
  ↓
[5. CRM Sync]  app/services/crm_sync.py (optional)
    - If sync_to_notion AND tenant.crm.enabled → push ProspectCard to Notion
    - tenant.crm.notion_database_id overrides NOTION_DATABASE_ID env var
  ↓
Output: ProspectCard — 3 angle drafts + 5-touch sequence + critic score
```

---

## Key Data Models (`app/agents/state.py`)

- `BDRState` — shared TypedDict passed between LangGraph nodes. Holds `tenant: TenantConfig`, company, industry, and accumulated outputs.
- `EnrichmentResult` — Exa signals + Hunter contacts + ICP classification
- `StrategyDecision` — chosen angle + rationale
- `HumanizerObservations` — LLM-only observations (3 per run)
- `AngleDraft` — assembled draft for one angle (DM + email subject + email body)
- `SequenceTouch` — one touch in the 5-touch sequence
- `OutreachSequence` — full sequence
- `ProspectCard` — final output card
- `CriticResult` — per-dim scores + rewrites_applied count
- `CRMSyncResult` — Notion push result

## Tenant Schema (`app/tenants/schema.py`)

`TenantConfig` is the root. Sub-models: `BrandConfig`, `BusinessConfig`, `PersonaConfig`, `ICPConfig`, `SenderConfig`, `CRMConfig`, `OutreachAngle` (×3), `HumanizerCopy` (with `AngleCopy` ×3). All use `extra="forbid"`.

`copy.json` requires exactly 3 angles, each with 11 fields × 3 variants = 33 strings, plus 3 single-string fillers. Schema enforces counts.

---

## Architecture Decisions

**Single agent path, tenant-driven.** No `if tenant_id == "X"` branching anywhere. All positioning is config. Adding a tenant means writing 4 files (`config.yaml`, `icp.txt`, `angles.json`, `copy.json`); no Python changes.

**Structured outputs everywhere.** All Claude calls go through `llm.with_structured_output(SchemaClass)`. No regex parsing of LLM text.

**Anti-AI copy pattern.** LLM generates *observations only*. Proof points, CTAs, subject lines, follow-ups, breakups all come from per-tenant fixed string banks (`copy.json`). 29-rule regex filter (`humanizer_rules.py`) strips residual LLM patterns as a final pass.

**Deterministic variants.** `_variant_index()` hashes (tenant_id + company) to always pick the same proof/CTA variant across reruns. Same prospect → same draft.

**Schema-agnostic Notion sync.** Connector discovers the title property dynamically; works with any Notion DB shape. Per-tenant DB override via `tenant.crm.notion_database_id`.

**Per-tenant Gmail queue.** Outbound emails queue under `tenants/<tenant_id>/data/queued/`, never in a shared directory.

**Streaming UI.** `run_workflow_stream()` yields `(latest_trace_line, full_state)` after each node so the dashboard renders progress live.

**Critic is non-looping.** Rewrites in place and continues. No max-iter retry loop — kept simple to ship.

---

## Tenant Loading

```python
from app.tenants import load_tenant, list_tenants

list_tenants()                    # ['demo', ...]  — scans tenants/ for valid configs
tenant = load_tenant("demo")      # cached, validates via Pydantic on read
```

`load_tenant()` reads `config.yaml`, `icp.txt`, `angles.json`, and `copy.json`, then constructs the `TenantConfig`. Validation errors raise immediately with the offending field path.

Every agent reads `state["tenant"]`. Agents never touch tenant files directly.

---

## Onboarding a New Tenant

Two paths, both documented in `tenants/README.md`:

1. **Wizard:** `python scripts/onboard_tenant.py` — interactive Q&A, calls Claude Sonnet to draft full tenant skeleton, validates via loader. Supports `--no-llm` (skeleton only) and `--force` (overwrite existing).
2. **Manual:** `cp -r tenants/demo tenants/<slug>/`, edit, then `python scripts/check_tenant.py <slug>` to validate.

---

## No Test Suite

Testing is manual: Streamlit UI tested live, batch scripts run with `--dry-run` first, validators (`check_tenant.py`) run on tenant edits. Smoke tests:

```bash
python scripts/check_tenant.py
python -c "from app.agents.workflow_engine import build_workflow; build_workflow(use_checkpointer=False)"
```

---

## Roadmap

1. Signal-weighted ICP scoring (0–100 composite vs. 3-tier)
2. Per-tenant model overrides (Sonnet vs. Opus per agent)
3. SQLite persistence (replace CSV)
4. Multiple sequence variants per tenant (founder track vs. enterprise track)
5. Per-tenant Exa query templates
