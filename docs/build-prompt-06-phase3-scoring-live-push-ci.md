# Build Prompt 06 — Phase 3: Composite Scoring, Live Sending-Tool Push, Config Flexibility, CI

Paste everything below the line into a fresh Claude Code session on the
`suhrckemanuel-del/BDR-pipeline` repo.

---

# BDR Pipeline — Phase 3: Composite Scoring, Live Push, Config Flexibility, CI

## Context (read first)

Repo: `suhrckemanuel-del/BDR-pipeline` — a white-label, tenant-driven BDR outreach
pipeline (Streamlit + LangGraph + Claude structured outputs). Read `CLAUDE.md` for
full architecture before writing any code.

Phase 1 (SQLite store, batch mode, eval gates, reply tracking, ops workspaces) and
Phase 2 are done. Phase 2 (branch `claude/session-443hem`; merge it or branch from
it if unmerged — check first) added:
- **HubSpot connector** (`app/services/hubspot_sync.py`) behind a
  `tenant.crm.provider: notion|hubspot|none` dispatch in `crm_sync.py`. Tokens via
  env-var indirection (`crm.hubspot_token_env` → fallback `HUBSPOT_ACCESS_TOKEN`),
  dry-run via `BDR_CRM_DRY_RUN=1`.
- **Sending-tool CSV export** (`app/services/sequence_export.py`,
  `scripts/export_sequences.py`, tracker Export action): Instantly/Smartlead lead
  CSVs, only for queued + latest-run-eval-passed prospects, `exported` event per
  prospect.
- **Website-driven onboarding**: `scripts/onboard_tenant.py --url <site>` (fetch →
  Claude drafts full tenant → diff-style summary → loader validation; fail-loud on
  unreachable homepage).
- **Deploy-readiness**: `Dockerfile` + `.dockerignore` + `docs/deploy.md`
  (`BDR_DB_PATH` on a volume; boots with zero API keys).
- **Offline check scripts** (all exit 1 on failure, no keys needed):
  `scripts/check_crm_sync.py`, `scripts/check_exports.py`,
  `scripts/check_onboarding.py`, plus `scripts/run_eval.py` and
  `scripts/run_batch.py --limit 3`.

## Hard constraints (unchanged from Phase 2 — do not relax)

- Do NOT build CRM features (contacts DB, deals, tasks) — we integrate, never replace.
- All positioning and behavior stays tenant-config-driven; no `if tenant_id == ...`
  branching anywhere.
- New integrations must degrade gracefully when credentials are missing (skip with a
  clear message, never crash) — follow `crm_sync.py` / `hubspot_sync.py`.
- Keep every new feature runnable offline where possible (sample/dry-run mode) so
  demos never depend on API keys.
- Every workstream extends the eval/verification story: new code paths get
  deterministic checks in a `scripts/check_*.py` sibling (mock HTTP/LLM; scratch DB
  via `BDR_DB_PATH`), never by weakening existing gates.
- Tenant schema changes must be backward compatible: only add fields with defaults;
  `extra="forbid"` stays; `tenants/demo/` keeps validating without edits (it is the
  compat proof — extend it only when a workstream explicitly says to).
- Known env quirk: `streamlit.testing.v1.AppTest` segfaults on any second
  `at.run()` (pandas 3.0 + pyarrow). Verify UI against a real `streamlit run`
  server with Playwright (Chromium at `/opt/pw-browsers/chromium`).
- Don't commit `*.db` or `exports/`. Follow existing style (module docstrings, pure
  services, thin UI).

## Workstreams (priority order)

### A. Signal-weighted composite ICP scoring (roadmap #1)
Replace the flat Tier 1/2/3-only view with a 0–100 composite account score that is
**transparent and deterministic where possible**:
- New `app/services/account_scoring.py` (or extend the existing
  `AccountScoringResult` path in `app/agents/state.py` — inspect first): weighted
  components (ICP tier fit, live-signal strength, job-posting intent, contact
  seniority/confidence) with per-component rationale. Weights live in tenant config
  (`icp.scoring_weights`, optional with sensible defaults — backward compatible).
- The LLM may classify individual signals; the composite arithmetic must be
  deterministic and unit-checkable offline.
- Surface the breakdown in the run panel and tracker (score chip + expandable
  component table). Persist components into the run record (`state_json` already
  flows through `store.record_run`).
- Eval gate: add a `score_components_sum` check (components must sum to the
  composite within rounding) to `pipeline_evals.py`.
- Offline check: `scripts/check_scoring.py` — fixture states through the scorer,
  assert weights, bounds, determinism, and backward compat (tenant without
  `scoring_weights` gets defaults).

### B. Live API push to Instantly/Smartlead (roadmap #8)
Extend Phase 2's CSV export with direct API push, same eligibility rules:
- `app/services/sequence_push.py`: push eligible prospects as leads into an
  Instantly campaign (v2 API, `INSTANTLY_API_KEY`) and/or Smartlead
  (`SMARTLEAD_API_KEY`); campaign id per tenant in config
  (`outreach.instantly_campaign_id` etc. — new optional `OutreachToolsConfig`
  sub-model, all-defaults).
- Reuse `sequence_export.collect_export_rows` for eligibility — one source of
  truth; do not fork the filtering logic.
- Missing key/campaign id → skip with message. `BDR_PUSH_DRY_RUN=1` → build
  payloads, zero HTTP. Log a `pushed` event per prospect (detail = tool name).
- Tracker UI: a "Push to tool" action next to Export, only enabled when the tenant
  has a campaign id configured.
- Offline check: `scripts/check_push.py` — mock the HTTP layer exactly like
  `check_crm_sync.py` does (single `_api` choke point, recorder function): payload
  shape, call order, dry-run zero-HTTP, skip paths, event logging.

### C. Per-tenant model overrides + Exa query templates (roadmap #2 + #5)
Small, config-only flexibility pass:
- `TenantConfig` gains optional `models` (e.g. `models.enrichment`,
  `models.strategist`, `models.humanizer`, `models.critic` — default to current
  hardcoded model) and `icp.exa_query_templates` (list of template strings with
  `{company}` / `{industry}` placeholders; default preserves current queries).
- Thread through the agents without any behavioral change when unset. Grep for the
  current model-id constants and Exa query construction first; keep one default
  constant per agent, overridden per tenant.
- Extend `scripts/check_tenant.py` output to show overrides when present.
- Offline check: assertions inside `scripts/check_tenant.py` or a tiny
  `scripts/check_config_overrides.py`: demo tenant unchanged → defaults; a synthetic
  tenant dict with overrides → agents receive the overridden values (inject/inspect
  without calling the LLM).

### D. CI workflow (after A–C merge)
`.github/workflows/checks.yml`: on push/PR run the full offline suite —
`check_tenant.py`, workflow-build smoke, `run_eval.py`, `run_batch.py --limit 3`,
`check_crm_sync.py`, `check_exports.py`, `check_onboarding.py`, plus the new
`check_scoring.py` / `check_push.py` / config-override checks. Python 3.12, pip
cache, no secrets required (everything is offline by design — that's the point).
Also add a `docker build` job (build only, no push). Update `CLAUDE.md`'s "No Test
Suite" section to point at the workflow.

## Subagent delegation plan

Use subagents to parallelize; keep integration and final verification in the main
session:
1. **Explore agent** (read-only, "medium"): map the current scoring path
   (`AccountScoringResult`, enrichment → score), the Exa query construction, the
   model-id constants per agent, and `sequence_export` internals, before any code.
2. **Plan agent**: one implementation plan per workstream A–C with file-level
   changes and acceptance checks; review the plans yourself before building.
3. **general-purpose agents**, one per workstream A–C, in `isolation: "worktree"`;
   A, B, C can run in parallel (disjoint files except possibly `ops.py` — assign
   different functions explicitly, as Phase 2 did). D last, in the main session.
4. Main session: merge worktrees, run the full verification suite, fix conflicts.
5. Lesson from Phase 2: subagents can die mid-flight on session usage limits. Their
   worktrees survive under `.claude/worktrees/` — salvage partial work from there
   and finish inline (or resume the agent) rather than restarting from scratch.

## Acceptance (all must pass before push)

- `python scripts/check_tenant.py` and workflow build smoke test.
- `python scripts/run_eval.py` exits 0 (offline) — including the new scoring gate.
- `python scripts/run_batch.py --limit 3` persists runs.
- All existing check scripts still exit 0; new checks for A/B/C exit 0; B works in
  dry-run without any API key.
- `tenants/demo/` validates without edits (backward compat).
- Playwright drive of all Streamlit views against a live keyless server: zero
  exceptions, including the new score breakdown and Push action states.
- Docker image builds; the CI workflow YAML is syntactically valid (`actionlint` or
  a dry parse) and lists every offline check.
- Update `CLAUDE.md` (structure, decisions, roadmap: mark #1, #2, #5, #8 done) and
  `tenants/README.md` (new config fields).

Develop on the session's designated branch, based on the latest default branch —
or on `claude/session-443hem` if Phase 2 is still unmerged (check first). Commit
per workstream with clear messages; push when acceptance passes.
