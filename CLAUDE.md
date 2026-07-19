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
    crm_sync.py                 # CRM dispatch node (tenant.crm.provider) + Notion push (schema-agnostic)
    hubspot_sync.py             # HubSpot push: default properties + ProspectCard as an associated Note
    sequence_export.py          # Instantly/Smartlead CSV export of queued, eval-passed sequences
    gmail_sender.py             # Per-tenant queue at tenants/<id>/data/queued/
    humanizer_rules.py          # 29-rule anti-AI regex filter
    store.py                    # SQLite persistence: runs + prospect tracker + events (pipeline/bdr.db)
    pipeline_evals.py           # Deterministic quality gates run on EVERY pipeline execution
    batch_runner.py             # Batch mode: pipeline over a prospect list, eval-gated + persisted
    reply_tracker.py            # Gmail IMAP reply detection → tracker status + per-angle reply rates
  ui/
    layout.py                   # Sidebar + single-run result panel
    ops.py                      # Batch runs / Pipeline tracker / Run history views
    components.py, theme.py     # Chalk design system
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
  onboard_tenant.py             # Tenant wizard: interactive Q&A or --url <site> (drafts from web content)
  check_tenant.py               # Pydantic-based tenant validator
  send_via_gmail.py             # Send queued sequences for a tenant
  run_batch.py                  # CLI batch runner (--mode sample|live), persists to SQLite
  run_eval.py                   # CI-able eval-gate regression check (exit 1 on blocking failure)
  check_replies.py              # IMAP reply detection for the tracker (--dry-run supported)
  export_sequences.py           # Export queued+eval-passed sequences to Instantly/Smartlead CSVs
  check_crm_sync.py             # Offline CRM connector checks (mocked HTTP, exit 1 on failure)
  check_exports.py              # Offline export checks (scratch BDR_DB_PATH, exit 1 on failure)
  check_onboarding.py           # Offline --url onboarding checks (mocked fetch + LLM, exit 1 on failure)

Dockerfile / .dockerignore      # Deployable image — see docs/deploy.md (BDR_DB_PATH on a /data volume)
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
    - Dispatches on tenant.crm.provider: notion (default) | hubspot | none
    - Notion: tenant.crm.notion_database_id overrides NOTION_DATABASE_ID env var
    - HubSpot: app/services/hubspot_sync.py — token from HUBSPOT_ACCESS_TOKEN
      (or the env var named by tenant.crm.hubspot_token_env); dry-run via
      BDR_CRM_DRY_RUN=1; missing creds → skip with message, never crash
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

**Schema-agnostic CRM sync, provider-dispatched.** `run_crm_sync` dispatches on `tenant.crm.provider` (`notion` default for backward compat, `hubspot`, `none`). The Notion connector discovers the title property dynamically (works with any DB shape; per-tenant override via `tenant.crm.notion_database_id`). The HubSpot connector touches only default portal properties (company name/domain, contact email/name/title) and pushes the full ProspectCard as an associated Note — no custom properties to provision. Tokens never live in config files: `tenant.crm.hubspot_token_env` names an env var, falling back to `HUBSPOT_ACCESS_TOKEN`.

**Sending-tool export, eval-gated.** `sequence_export.py` exports queued prospects to Instantly/Smartlead lead CSVs (per-touch copy as `subject_N`/`body_N`/`day_N` custom columns) — but only when the *latest* run passed the eval gates and a contact email exists; every skip carries a reason. One `exported` event logs per prospect. Fully offline: reads only the SQLite store.

**Live push behind per-tool choke points.** `sequence_push.py` pushes the same eligible prospects (eligibility reused verbatim from `sequence_export.collect_export_rows` — never forked) as leads into an Instantly (v2) or Smartlead campaign. Campaign ids live in tenant config (`outreach.instantly_campaign_id` / `outreach.smartlead_campaign_id`); keys via env-var indirection (`outreach.*_api_key_env` → fallback `INSTANTLY_API_KEY`/`SMARTLEAD_API_KEY`). Missing key/campaign id → skip with message; `BDR_PUSH_DRY_RUN=1` builds payloads with zero HTTP; one `pushed` event per prospect (detail = tool). All HTTP goes through `_instantly_api`/`_smartlead_api` so `check_push.py` can mock the wire.

**Website-driven onboarding.** `onboard_tenant.py --url <site>` fetches the homepage (fail-loud) plus common secondary pages (best-effort), extracts text via stdlib HTML parsing, and has Claude infer the brief + draft the full tenant, printing a diff-style summary before loader validation. The interactive and `--no-llm` paths are unchanged.

**Per-tenant Gmail queue.** Outbound emails queue under `tenants/<tenant_id>/data/queued/`, never in a shared directory.

**Streaming UI.** `run_workflow_stream()` yields `(latest_trace_line, full_state)` after each node so the dashboard renders progress live.

**Critic is non-looping.** Rewrites in place and continues. No max-iter retry loop — kept simple to ship.

**Every run is eval-gated and persisted.** `pipeline_evals.evaluate_state()` (no LLM, no network) runs after every pipeline execution — structural gates (5 touches, valid channels, ordered days), copy-quality gates (placeholder leaks, personalization, anti-AI filter idempotency), and critic thresholds (overall ≥ 3.0, no `do_not_send_yet` verdict, no high-severity risks). Blocking failures set `eval_passed=False` — those prospects should not be queued. Runs, tracker rows, and events persist to `pipeline/bdr.db` via `services/store.py` (stdlib sqlite3, WAL). `scripts/run_eval.py` is the CI regression loop: exit 1 on any blocking gate failure.

**Tenant-weighted account scoring.** `services/account_scoring.py` owns the composite 0–100 account-readiness score: five deterministic 0–5 components (ICP fit, pain evidence, trigger strength, contact confidence, evidence quality) combined by `expected_overall()` using per-tenant weights (`icp.scoring_weights`, defaults reproduce the historical 25/25/20/20/10 split — omitting the field is fully backward compatible). The LLM may inform individual signals upstream, but the composite arithmetic is pure and offline. `pipeline_evals` re-runs the arithmetic as the `score_components_sum` gate on every run; the run panel and tracker surface the breakdown (`score_chip` + expandable component table).

**Prospect tracker, not a CRM.** `store.py` keeps one row per (tenant, company) with a status funnel: researched → queued → sent → replied → meeting (+ not_a_fit). Reruns refresh scores without resetting funnel progress. Reply detection (`reply_tracker.py`, read-only Gmail IMAP with the existing App Password) moves prospects to `replied` and logs the outreach angle — powering per-angle reply-rate stats. Real CRM integration stays external (Notion sync; more connectors welcome).

**Config-only model + query flexibility.** Optional tenant `models` block (`models.enrichment/strategist/humanizer/critic`) overrides each agent's model; `app/agents/model_config.resolve_model()` is the single resolver, and every agent keeps one hardcoded default constant, so unset overrides change nothing. `icp.exa_query_templates` replaces the built-in Exa query pair with tenant templates (`{company}`/`{industry}` placeholders, literal-replace rendering so stray braces can't raise; first template = news slot with 5 results, rest = jobs slot with 3 each). `check_config_overrides.py` proves byte-identical behavior when both are absent.

**Batch mode has an offline path.** `batch_runner.run_batch(mode="sample")` uses the deterministic fixture states from `demo_eval.py`, so batch + tracker + history can be demoed and tested with zero API keys. Live mode runs the full LangGraph workflow per row; one failing prospect never aborts the batch.

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
python scripts/run_eval.py                  # offline eval gates — exit 1 on blocking failure
python scripts/run_batch.py --limit 3       # offline batch smoke (persists to pipeline/bdr.db)
python scripts/check_crm_sync.py            # CRM connector checks — HTTP mocked, no keys
python scripts/check_exports.py             # export checks — scratch DB via BDR_DB_PATH
python scripts/check_onboarding.py          # --url onboarding checks — fetch + LLM mocked
python scripts/check_scoring.py             # composite-score checks — weights, bounds, eval gate
python scripts/check_push.py                # live-push checks — HTTP mocked, scratch DB
python scripts/check_config_overrides.py    # model/Exa-template overrides — LLM mocked
```

Note: `streamlit.testing.v1.AppTest` segfaults on any *second* `at.run()` in this
dependency set (pandas 3.0 + pyarrow, even on a minimal one-dataframe app) — verify
UI flows against a real `streamlit run` server (e.g. Playwright) instead.

---

## Roadmap

1. ~~Signal-weighted ICP scoring (0–100 composite vs. 3-tier)~~ — done (`services/account_scoring.py`, tenant `icp.scoring_weights` + `score_components_sum` eval gate)
2. ~~Per-tenant model overrides (Sonnet vs. Opus per agent)~~ — done (tenant `models` block + `agents/model_config.py`)
3. ~~SQLite persistence~~ — done (`services/store.py`, runs + tracker + events)
4. Multiple sequence variants per tenant (founder track vs. enterprise track)
5. ~~Per-tenant Exa query templates~~ — done (`icp.exa_query_templates`)
6. ~~HubSpot connector alongside Notion~~ — done (`services/hubspot_sync.py`, `crm.provider` dispatch)
7. ~~Export queued sequences to sending tools (Instantly/Smartlead)~~ — done (`services/sequence_export.py` + tracker Export action)
8. ~~Live API push to Instantly/Smartlead~~ — done (`services/sequence_push.py`, tenant `outreach.*` config + tracker Push action)
9. Salesforce/Pipedrive connectors behind the same `crm.provider` dispatch
