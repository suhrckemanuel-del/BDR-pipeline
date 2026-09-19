# Cost baseline — B0 reference (ticket #8)

**Status: measurement infrastructure landed; the numbers below are the reference
every Phase B optimization measures against.**

This document is the committed baseline for:

- **#9 (B1)** — strip the craft prompt from the strategist
- **#10 (B2)** — per-node model tiering
- **#12 (B3)** — prompt caching
- **#13 (B4)** — cascade council
- **#14** — Phase B exit criterion: **≥50% cost reduction at equal gate quality, measured
  against these baselines** (relative-only gate; no absolute $ ceiling until the C2 engine
  decision).

## How to reproduce

```bash
# Offline structural checks (no API keys needed)
.venv/Scripts/python.exe -m pytest tests/test_token_accounting.py -q

# Live baseline run (spends API credits — maintainer's "press start" step)
.venv/Scripts/python.exe scripts/run_demo_eval.py --tenant demo --max-accounts 5 --live
# → docs/evals/demo-evals.md + CSV/JSON carry llm_calls / input_tokens /
#   output_tokens / cost_usd columns and a Cost per Prospect section

# Council comparison baseline (also spends credits)
.venv/Scripts/python.exe scripts/run_council_eval.py --tenant demo --max-accounts 5
```

The committed CSV/JSON artifacts are the ground truth; the summary numbers below
must be recomputable from them (`mean(cost_usd)` per prospect row).

## Price table (code: `app/services/token_accounting.py`, dated 2026-09-19)

| Model | Input $/MTok | Output $/MTok |
|---|---|---|
| claude-sonnet-4-6 | 3.00 | 15.00 |
| claude-haiku-4-5 | 1.00 | 5.00 |

Caching economics (added by B3, ticket #12): cache **reads** are priced at
10% of the input rate; cache **writes** carry a 25% premium (5-minute TTL).
The ledger splits `cache_read_tokens` / `cache_creation_tokens` and reports
`cache_hit_rate` per node and per run.

**B3 spike finding (2026-09-19):** on the pinned `langchain-anthropic==1.4.1`,
`cache_control` only reaches the request payload in the content-block form.
The `additional_kwargs` form used since the original build was silently
dropped from the payload — **no caching was ever active before B3**, which is
why no cache savings ever appeared in any measurement. All call sites now use
`model_router.cached_system_message` (content-block form, provider-gated so
the marker never leaks to non-Anthropic providers).

Live cache-hit evidence lands in the eval CSV's `cache_hit_rate` column and
`state.token_usage.totals` once a live run seals the baseline.

## Instrumented call sites (complete audit)

| # | Site | Node ledger | Model |
|---|---|---|---|
| 1 | `enrichment._summarise` — research summary | `enrichment` | Haiku 4.5 |
| 2 | `enrichment._classify_icp` — ICP tier classification | `enrichment` | Haiku 4.5 |
| 3 | `strategist.run_strategist` — angle selection | `strategist` | Sonnet 4.6 |
| 4 | `humanizer._generate_observations` — observations | `humanizer` | Sonnet 4.6 |
| 5 | `critic._score_with_council` — every council scorer | `critic` | per-scorer map |
| 6 | `critic._rewrite_failing_touches` — rewrite calls (incl. retries) | `critic` | Sonnet 4.6 |
| 7 | `critic` quality-gate call | `critic` | Sonnet 4.6 |

Capture mechanism: a `UsageTracker` callback handler attached at client
construction on every `ChatAnthropic(...)`; it rides through
`with_structured_output` wrappers, thread pools, and retries. Zero-LLM runs
(no API key) merge nothing — the pipeline behaves byte-identically to pre-B0.

## Baseline numbers — MEASURED 2026-09-20 (live, 3 demo prospects per arm)

First live baseline (and A/B) complete. Artifacts: `docs/evals/after-optimized.*`
and `docs/evals/before-baseline.*` (CSV/JSON/MD per arm). Both arms ran on the
same code (post-B1/B2/B3) with the critic profile as the only difference — the
true pre-optimization code no longer exists on main, so this A/B isolates **B4
(cascade) only**.

| Arm | Calls/prospect | Cost/prospect | Gate verdicts |
| --- | --- | --- | --- |
| **Optimized** (cascade on, Haiku first rater) | 10 | **$0.0754** | do_not_send_yet ×3 |
| **Before** (full council, Sonnet-first) | 10 | **$0.0705** | needs_edit, do_not_send_yet ×2 |
| Totals (3 prospects each) | 30 | $0.2263 vs $0.2115 | |

**Honest verdict for #14: the ≥50% exit criterion is NOT met by this measurement.**

Why, from the data:
- **Every prospect escalated** in both arms (10 calls each): the fixtures are
  borderline-by-design (synthetic, thin evidence), so the cascade never hits its
  saving case (a clean pass = 1 call). On escalating workloads the cascade is
  cost-neutral by construction — it buys *scrutiny*, not savings.
- **The dominant cost driver is ~11k OUTPUT tokens per prospect at Sonnet
  prices** — the craft-heavy humanizer/rewriter path, not the critic call count.
- Cache hit-rate was ~0 on these runs: each prospect is a fresh conversation
  (no shared prefix across prospects), so B3 savings are per-run prefix reuse
  only (retries/rewrites within one prospect).

**What the real lever is:** cutting output tokens on the craft path (tighter
humanizer prompts, cheaper writer tier where quality holds — the B2 route map
already supports per-node tier changes with zero code). That is the follow-up
work #14's target should attach to.

Quality leg: verdicts are not comparable at n=3 (one verdict differed between
arms; model nondeterminism on borderline fixtures). No quality claim is made
in either direction.

## Free-research arm (ticket #15)

The free public-page prototype (`app/services/free_research.py`) costs **zero LLM calls**
by construction — its only running costs are bandwidth and seconds. Benchmarked offline
(real domains: 80% page coverage, ~2.4k chars/company, see `docs/free-research.md`); the
quality reconciliation against the Exa arm (does free evidence hold the gate at parity?)
is the live-guided step below.

## Notes for B1–B4

- B1 (prompt strip) shows up as an `input_tokens` drop on the `strategist` ledger entry.
- B2 (model tiering) moves per-node `model` values; the ledger records the mix.
- B3 (caching) needs cache-read/cache-write price rows added here when wired.
- B4 (cascade) cuts `critic` calls; `llm_calls` per prospect is its headline metric.
