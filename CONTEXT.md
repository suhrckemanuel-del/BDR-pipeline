# CONTEXT.md

Shared language for the BDR pipeline. Skills and agents should use these terms exactly; gaps or disputes get resolved through domain modeling and recorded here.

## What this is

A white-label BDR (business development representative) pipeline. For each prospect it: researches the company, builds evidence cards, classifies ICP fit, picks an outreach angle, drafts a sequence of cold email touches, humanizes them, gates them through a critique council, and assembles a portfolio artifact for a human reviewer. Humans approve everything; nothing auto-sends.

## Glossary

| Term | Meaning |
|---|---|
| **Tenant** | A white-label configuration — branding, ICP definition, models, critic council settings — under `tenants/<id>/config.yaml`. All behavior is tenant-driven; no hardcoded branding or copy. |
| **Prospect** | A target company being researched for outreach. |
| **Touch** | One email draft in a prospect's sequence. A sequence has several touches. |
| **Evidence card** | A falsifiable, sourced observation about the prospect that copy may cite. The unit of honesty: every claim in a touch traces to a card. |
| **ICP classification** | Structured LLM call deciding how well a prospect fits the tenant's ideal customer profile. |
| **Account score** | Deterministic weighted score of prospect fit and priority. No LLM involved. |
| **Angle** | The strategist-selected outreach angle the sequence argues from. |
| **Humanizer** | Pass that strips AI-sounding patterns using deterministic regex rules (`humanizer_rules`), not model judgment. |
| **Critic** | Scorer rating each touch on craft dimensions, returning structured critiques. |
| **Council** | Multiple independent critic raters (tenant-configurable `council_size`), aggregated by per-dimension median, with disagreement flags when raters span ≥2 points. |
| **Rewriter** | Consolidated rewrite pass: one call per touch merging all failing dimensions. |
| **Quality gate** | Final verdict on the **final post-rewrite** sequence, fed the council's panel context. |
| **Portfolio artifact** | The assembled report a human reviews (`report_builder`). |
| **Demo eval** | Offline eval against fixture data (`run_demo_eval`) — no network. |
| **Council eval** | Live comparison scoring identical copy under `council_size` 1 vs N to isolate rater behavior (`run_council_eval`). |

## Invariants

- Generation stays on the deterministic spine (copy banks, evidence cards); agents never free-form copy.
- The quality gate always judges the copy the human will actually see.
- Human-in-the-loop: the UI surfaces gate verdicts and council agreement; no auto-send.
- Everything is tenant-config-driven; adding a customer means adding a `tenants/<id>/` directory.
