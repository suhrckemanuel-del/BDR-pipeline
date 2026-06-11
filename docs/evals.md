# Demo Eval Results

These are internal demo workflow metrics for the BDR Pipeline portfolio artifact.
They do not claim reply rates, meeting rates, revenue, deliverability, or campaign lift.

## What Was Evaluated

- Date: 2026-06-11
- Tenant: `demo`
- Mode: `sample`
- Demo accounts used: Globex Industries, Initech, Hooli
- Completed runs: 3/3

Detailed CSV: `docs/eval-results.csv`

## Metrics

| account | industry_context | run_completed | runtime_seconds | evidence_card_count | high_confidence_evidence_count | contact_count | account_score | priority_label | quality_gate_verdict | risk_flag_count | unsupported_claim_count | report_generated | notes_errors |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| Globex Industries | B2B SaaS \| Series D revenue intelligence customer; 120 AEs across 3 segments | yes | 0.00 | 3 | 1 | 0 | 80 | high_priority | needs_edit | 1 | 1 | yes |  |
| Initech | B2B SaaS \| Series C; recent CRO hire focused on forecast accuracy | yes | 0.00 | 2 | 1 | 0 | 74 | review | approved | 0 | 0 | yes |  |
| Hooli | B2B SaaS \| Late-stage; multi-product motion (PLG + sales-led) | yes | 0.00 | 3 | 1 | 1 | 72 | review | needs_edit | 1 | 1 | yes |  |

## Limitations

- Demo prospects are anonymized/synthetic examples.
- Sample mode uses deterministic fixture states and does not verify live API reliability.
- Live mode depends on available API keys, network access, and third-party enrichment services.
- Evidence confidence is a workflow quality signal, not proof that outreach should be sent.
- Human review is required before any outreach leaves the system.

## What These Results Prove

- The workflow can produce measurable internal artifacts: evidence counts, account score, quality gate verdict, risks, and report generation status.
- The eval harness records runtime and failures without treating missing APIs as a successful run.

## What These Results Do Not Prove

- They do not prove reply-rate lift.
- They do not prove meetings booked.
- They do not prove revenue impact.
- They do not prove production reliability across real prospect data.
