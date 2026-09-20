# Free email finder (C1)

**Status: implemented and benchmarked live (see `email-finder-benchmark.md`). Free path: 4/10 usable contacts at $0; Hunter: 9/10 at paid credits. Complementary, not equivalent.**

## What it is

`app/services/email_finder.py` — contact discovery with zero API keys, built on the same posture as the #15 free-research fetcher:

- **Scope:** the company's own public pages only (`/contact`, `/team`, `/about`, `/people`, `/imprint` + homepage). One fetch per page, robots.txt respected (robots-unreachable fails *closed*), honest User-Agent, every fetch logged.
- **Zero LinkedIn / job-board URLs** by construction (same forbidden list as #15).
- **Extraction:** `mailto:` links plus plain-text addresses from script/style-stripped HTML.
- **Classification:** junk and placeholders dropped; noreply-class (cap 10), legal/privacy (30), support/recruiting (55), generic `info@`/`hello@` (70), freemail (45) all *kept but confidence-capped*, never silently dropped.
- **Pattern inference:** `first.last@domain`-style candidates. When a name is supplied, the domain's own harvested emails teach its local-part convention (`dot_two`, `dot_initial`, `plain`, `underscore_*`) and the inferred address scores 65; unlearned fallbacks 30–45.
- **MX verification:** guarded `dnspython` import (works without it; MX treated as unknown). A domain with no MX/A record caps every candidate at 15.

## Confidence model (0–100)

| Signal | Score |
|---|---|
| mailto on own site | 88 (+4 on contact-context pages) |
| plain text on own site | 78 (+4 on contact-context pages) |
| person-name match (supplied name) | +8, max 97 |
| pattern, learned convention | 65 |
| pattern, unlearned fallback | 30–45 |
| role caps | noreply 10 · legal 30 · support/recruiting 55 · generic 70 |
| freemail | 45 |
| MX ok | +3 (max 99) |
| MX fail | capped at 15 |

## Output

`find_emails(company, domain, first_name=None, last_name=None)` returns
`EmailFinderResult`: `contacts` (ContactLead-shaped dicts — enrichment-consumable — with `source`/`verification` extras), `emails` (provenance records), `degradations` (#4 channel), `fetch_log`, `conventions`.

## Test coverage

`tests/test_email_finder.py` — 20 offline tests, no network: extraction, junk filters, the full cap table, convention learning, pattern ranking, robots fail-closed posture, MX monkeypatched paths, ContactLead contract, dedup-keeps-best.

## Where the free path fits (from the live benchmark)

- **Publishes contact info → free wins.** SMB/indie segment: role addresses (`hello@plausible.io`, `jason@basecamp.com`) are on public pages; one domain (Plausible) had *zero* Hunter contacts but a public hello@.
- **Doesn't publish → Hunter's corpus wins.** Big SaaS keeps person-level addresses (`vinay@stripe.com`) out of public HTML; only a corpus has them.
- **The free lever for person-level addresses is pattern inference** — it needs a name, which the operator supplies from the page, a news article, or a manual LinkedIn look. `dot_two` conventions cover most companies once one real address is harvested.

**Recommended wiring (C-phase follow-up): free-first, Hunter-fallback.** Run the finder for every prospect; only spend Hunter credits when the free path returns nothing usable. At the measured hit rates that cuts Hunter spend roughly in half while keeping its corpus for the hard cases.
