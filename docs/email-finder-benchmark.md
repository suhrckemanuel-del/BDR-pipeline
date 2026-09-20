# Email finder benchmark — free recon vs Hunter (C1, ticket #17)

**Run date: 2026-09-20 · live network · free arm cost: $0 (no keys, no LLM calls) · Hunter arm: production `domain-search` with demo-tenant persona filter.**

Raw data: `email-finder-benchmark.csv` / `.json` (set 1) and `email-finder-benchmark-smb.csv` / `.json` (set 2).

## Set 1 — big-SaaS segment (same five companies as the #15 research benchmark)

| Company | Free usable | Free best | Hunter contacts | Hunter best |
|---|---|---|---|---|
| Stripe | 0 | — | 5 | vinay@stripe.com (85) |
| Notion | 0 | — | 5 | matthew@notion.online (99) |
| Linear | 1 | hello@linear.app (73) | 2 | cristina@linear.app (82) |
| Vercel | 0 | — | 5 | grace.roehl@vercel.com (85) |
| Figma | 0 | — (0/8 pages fetchable — bot protection) | 1 | therb@figma.com (99) |

**Free 1/5 · Hunter 5/5.** Big SaaS doesn't publish personal addresses in public HTML — Hunter's corpus is the only source. Figma blocks all fetching (same as #15).

## Set 2 — SMB / indie segment (the realistic outreach target)

| Company | Free usable | Free best | Hunter contacts | Hunter best |
|---|---|---|---|---|
| 37signals | 1 | jason@basecamp.com | 2 | scott@37signals.com |
| Buffer | 0 | — (JS-rendered pages) | 1 | jenny@buffer.com |
| Ghost | 1 | support@ghost.org (55) | 1 | hannah@ghost.org |
| Plausible | 1 | **hello@plausible.io** | **0** | — |
| Fathom | 0 | — | 4 | chelsea.shane@fathom.video |

**Free 3/5 · Hunter 4/5.** Where contact info is published, the free path finds it — including one domain where Hunter had nothing.

## Combined: free 4/10 usable at $0 · Hunter 9/10 at paid credits

## Findings

1. **The arms are complementary, not substitutes.** Free = what the company publishes (role/generic addresses, plus person addresses that appear on team/imprint pages). Hunter = a person-level corpus accumulated outside public pages.
2. **Segment matters more than tool.** On SMB targets (the actual ICP for affordable outreach) the gap is 3/5 vs 4/5 — at $0 vs paid credits. On big-SaaS targets the corpus wins outright.
3. **Pattern inference is the free lever for person-level addresses** — it needs one name (team page, news mention, manual LinkedIn look). With a harvested real address it learns the domain's convention and scores the inferred address 65. Exercised in the offline suite; not in this benchmark because no names were supplied live (supplying them would have contaminated the free-vs-Hunter comparison).
4. **Bot protection is the free path's hard failure mode** (Figma, 0/8 pages) — it degrades loudly into the #4 channel rather than pretending.

## Recommendation

**Free-first, Hunter-fallback.** Run the finder on every prospect; spend Hunter credits only when the free path returns nothing usable. At these hit rates roughly half the Hunter spend disappears on SMB segments, while corpus coverage remains for the hard cases. Wiring this into enrichment behind a `research.source: auto|free|hunter` tenant key is the natural C-phase ticket.
