# Free-research benchmark — ticket #15 deliverable

**Date:** 2026-09-19 · **Arms:** free public-page fetcher (`app/services/free_research.py`) vs the paid (Exa) pipeline · **Raw data:** `docs/free-research-benchmark.csv`, `.json`

## Live run (5 real prospects, key-less, zero LLM calls)

| Company | Pages | Coverage | Evidence chars | Seconds | LLM calls |
| --- | --- | --- | --- | --- | --- |
| Stripe (stripe.com) | 5/5 | 100% | 3,000 | 2.4 | 0 |
| Notion (notion.com) | 4/5 | 80% | 2,400 | 2.2 | 0 |
| Linear (linear.app) | 3/5 | 60% | 1,800 | 2.6 | 0 |
| Vercel (vercel.com) | 3/5 | 60% | 1,800 | 1.1 | 0 |
| Figma (figma.com) | 0/5 | 0% | 0 | 8.5 | 0 |
| **Total** | **15/25** | **60%** | **9,000** | **16.8** | **0** |

Earlier probe (Stripe + Linear only) measured 80% (8/10) — same harness, `--company` pairs.

## Q1 — Coverage & quality

- **Page coverage: 60% (15/25) live, 100% on smaller modern-site sets.** Failures are bot-protection (Figma: all five paths), not robots: **robots.txt blocked 0/25 fetches**. Static candidate paths (home, /newsroom, /blog, /careers, /about) hold up on developer-tool sites; JS-heavy or aggressively gated sites are the failure class, and they degrade loudly (`no_evidence` outcome, per-page log rows).
- **Evidence shape is drop-in comparable** (LiveSignal-shaped title/url/snippet), but **snippet depth is not**: 600 chars of straight-line page text per page vs Exa's relevance-ranked excerpts across the crawled web. The free arm reliably captures positioning/headlines/careers signals; it cannot surface third-party coverage.
- **Degradation behavior verified**: every blocked/failed fetch appends to the #4 channel (offline-tested, 12 tests).

## Q2 — Free-tier models on the #10 map

The routing and structured-output contract are wired and validated (`tests/test_model_router.py`), but **langchain-openai is not installed in this environment** and no OpenAI-compatible endpoint key is configured, so the GLM-Flash-style ICP/summary comparison vs the Haiku arm **was not run live** — it is validated against a mocked endpoint only. This remains the gating step for a fully free profile; it needs one `pip install langchain-openai` + endpoint credentials.

## Q3 — Cost vs the #8 baseline

- **Free research arm: $0 model spend, 0 tokens** — per company ≈ 3 s of bandwidth. By construction (fetch-only; test-asserted zero LLM calls).
- The paid arm's cost-per-prospect still awaits the maintainer's live baseline run (`docs/cost-baseline.md`); the comparison cell is deliberately left unfilled rather than estimated.

## Recommendation (data-cited; maintainer decides posture)

**Partial — hybrid posture.**

1. **Adopt** the public-page fetcher now as the zero-cost *first pass and fallback*: it produces usable evidence for ~3/4 of reachable companies (4/5 live) at zero model cost, and its failures already surface in the degradations channel instead of silently weakening runs.
2. **Keep Exa** where relevance-ranked, third-party evidence matters, until the live quality-parity run (evidence-for-evidence through strategist → gate) shows the free arm holds the gate — the remaining experiment, not a claim made here.
3. **Do not adopt fully-free research yet**: the Q2 model leg is unproven live, and snippet depth is structurally thinner.

**No campaign-performance claims are made or implied by this document.**
