# Free-research mode — prototype (ticket #15)

**Status: prototype shipped, benchmarked offline and on real domains. Not wired into the pipeline — this is the D1 exploration ticket, not a feature flip.**

## What it is

`app/services/free_research.py` fetches a company's **own public pages** (homepage, `/newsroom`, `/blog`, `/careers`, `/about`) and shapes them as `LiveSignal`-shaped evidence — the same shape the paid (Exa) pipeline produces — so downstream prompts and benchmarks can treat the arms like-for-like.

**Zero API keys, zero LLM calls.** A fetch-only pass over one company costs fractions of a cent (bandwidth only) versus an Exa search + LLM synthesis.

## Research posture (non-negotiable)

- **Own public pages only.** No crawling, no link discovery — five static candidate paths, one fetch each.
- **robots.txt respected on every fetch.** Network failure while checking robots fails **closed** (page not fetched); an unparseable robots.txt fails open, conservatively, and is logged. Disallowed = degradation entry, never a workaround.
- **Honest User-Agent** identifying the research purpose.
- **Hard URL-class exclusions:** LinkedIn, Indeed, Glassdoor, ZipRecruiter, Monster, Wellfound, RemoteOK, WeWorkRemotely — excluded by construction (`FORBIDDEN_URL_MARKERS`), tested.
- **Every fetch logged** (URL, UTC timestamp, outcome, bytes) and every failure flows into the #4 degradations channel.

## Benchmark harness

`scripts/benchmark_free_research.py`:

```bash
# tenant prospect list (offline fixture domains → honest zeros)
python scripts/benchmark_free_research.py --tenant demo

# real domains, live
python scripts/benchmark_free_research.py --company "Stripe=stripe.com" --company "Linear=linear.app"
```

Outputs per-company CSV (`coverage`, `evidence_chars`, `seconds`, `llm_calls_free_arm`) and a JSON summary. The **free arm makes zero LLM calls** — asserted in tests.

## Results snapshot (2026-09-19)

| Arm | Companies | Coverage | Evidence | LLM calls |
| --- | --- | --- | --- | --- |
| Free (real domains) | 2 (Stripe, Linear) | 80% (8/10 pages) | 4,800 chars | **0** |
| Free (demo fixtures) | 5 | 0% (reserved `.example.com`) | 0 | **0** |

Committed fixture run: `docs/free-research-benchmark.csv` + `.json`.

## What this does and does not prove

Proven: fetching works broadly (1MB cap, timeouts, robots posture), output is drop-in `LiveSignal`-shaped, cost of the free arm is zero model calls, failures degrade loudly.

Not proven (needs the live-guided step): that free-page snippets **replace** Exa snippets in strategist/critic quality at parity. That comparison requires your API key for the paid arm — it is the remaining Q3, recorded in `docs/cost-baseline.md`.

## Path to D1 (if adopted)

`research_company_public_pages(company, domain)` already matches the D1 adapter surface: evidence in, degradations out. Wiring it behind a tenant config key (e.g. `research.source: public | exa | both`) is a one-node change in `enrichment.py` — deliberately not done here.
