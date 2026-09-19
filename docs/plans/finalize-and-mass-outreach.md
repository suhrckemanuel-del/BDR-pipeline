# Plan: Finalize v1 → Cost-Optimized → CLI-First → Mass Outreach

*Drafted 2026-09-19. Companion to `docs/roadmap.md`; the ordered backlog below is the source for ticket creation.*

## Guiding stance (read before building anything)

1. **Finalize before expanding.** The council shipped but has never been validated live (`docs/council-evals.md` is empty), known dead weight from the May review is still in the tree, and the quality-gate UI was never eyeballed. Building mass outreach on an unvalidated core multiplies rework.
2. **Measure cost before cutting it.** There is zero token/cost accounting today. Without per-node measurement, every "optimization" is faith.
3. **Simplify by subtraction, not addition.** "All-in-one" done wrong means five bolted-on subsystems. Done right it means: a dumb deterministic spine, providers behind adapters, runs persisted, and Claude Code / the CLI as the orchestrator. The pipeline already stopped free-form generation — keep it that way.
4. **CLI-first, Streamlit for review only.** The UI becomes the human review surface; everything runs headless.
5. **Mass outreach must not erase the founder-safe thesis.** Scraping + bulk sending carries legal/ToS and deliverability exposure (CAN-SPAM/GDPR, mailbox limits, sender reputation). Pacing, review-gating and the degradations channel are features, not friction.

---

## Phase A — Finalize what exists (gate to everything else)

| # | Ticket | Notes |
|---|--------|-------|
| A1 | Run the council comparison eval live; set council defaults from data | `scripts/run_council_eval.py` exists; needs ANTHROPIC_API_KEY + minutes. Decide default `council_size` from verdict-shift data. |
| A2 | Degradations channel | `state.degradations: list[str]` — enrichment currently swallows Exa/Hunter/LLM failures silently; surface in UI banner + eval CSV. Fixes the misleading Critic 0.0 KPI. Small, on-thesis. |
| A3 | Dead-weight cleanup | `intent_score`/`intent_top_trigger`/`intent_breakdown` never computed in live runs; `prospect_notes` accepted but unread; `TargetProfile` unpopulated; dead checkpointer path. Implement or delete each. |
| A4 | Repo hygiene | Decide fate of untracked `start-bdr.bat` / `.freebuff/`; pin dependencies; add GitHub Actions CI running `pytest` + `compileall` + `check_tenant.py` on PRs. |
| A5 | Durable run store | SQLite or JSON under `runs/`; strip the `tenant` object from persisted state (keep `tenant_id`); sidebar "Recent runs" + review queue. **Foundation for Phases C and D** — bulk outreach without persistence is not buildable. |

## Phase B — Token & cost optimization (strictly ordered)

| # | Ticket | Expected effect |
|---|--------|-----------------|
| B0 | Per-node token/cost accounting in the eval harness | Makes every later ticket measurable. Do first. |
| B1 | Strip `load_prompt("cold_email")` from the strategist's angle-selection call | Craft rules are unactionable for angle picking; pure input-token waste on every run. |
| B2 | Model tiering per node + per-tenant model overrides (roadmap #2) | Haiku for research summary + ICP classification; Sonnet for observations, rewrites, gate. ~50–70% off non-craft calls. |
| B3 | Anthropic prompt caching on repeated system/tenant/craft blocks | Tenant config + craft prompt repeat in all ~7–10 calls/prospect; caching is the big scale win. |
| B4 | Cascade council | Single rater first; full council only when the score is borderline or the gate flags. Cuts council cost ~60% while scrutinizing exactly the risky cases. |
| B5 | Batch API for bulk runs | 50% discount on non-interactive prospect batches; depends on A5. |

**Exit criterion:** measured cost-per-prospect down ≥50% at equal gate quality (per B0's baseline).

## Phase C — CLI-first headless

- **C1:** `python -m app.cli run --tenant demo --input prospects.csv --out runs/` — full pipeline with zero Streamlit dependency; JSON run output for orchestration.
- **C2:** Engine-agnostic LLM adapter behind the existing `with_structured_output` contract (direct API now; agentic-CLI engines later — research done 2026-09: Gemini CLI free tier is the viable fallback).
- **C3:** Streamlit becomes the review queue over the run store.

## Phase D — Mass outreach (all-in-one)

- **D1:** Enrichment adapter layer — Exa/Hunter plus a scraping provider, with degradations visible (depends A2).
- **D2:** Bulk queue — prospects CSV → run store → per-prospect state, resume, review queue (depends A5, C1).
- **D3:** Follow-up intelligence — follow-ups conditioned on prior touch + engagement signals (static context first; send/reply tracking later).
- **D4:** Sending at scale — throttling, per-tenant sending windows, bounce/unsubscribe handling, warmup guidance. Review-gated by default; build on the existing per-tenant Gmail queue.
- **D5 — explicit non-goal until D4 is proven:** auto-send. Human approval stays in the loop.

## Suggested sequencing

A1–A4 → A5 → B0–B2 → C1 → D1–D2 → B3–B5 → D3–D4.

## Open decisions (grill before ticketing)

1. Council default size — decide from A1 data, not vibes.
2. Run store: SQLite vs JSON files (volume, query needs).
3. Scraping scope: which sources, and the ToS posture for each.
4. Sending stack: Gmail app-password limits vs SMTP relay vs provider API.
5. Cost target: what cost-per-prospect is acceptable for the first paying tenant?
