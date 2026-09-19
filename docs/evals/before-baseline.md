# Demo Eval Results

These are internal demo workflow metrics for the BDR Pipeline portfolio artifact.
They do not claim reply rates, meeting rates, revenue, deliverability, or campaign lift.

## What Was Evaluated

- Date: 2026-09-20
- Tenant: `demo`
- Mode: `live`
- Demo accounts used: Globex Industries, Initech, Hooli
- Completed runs: 3/3

Detailed CSV: `docs/evals/before-baseline.csv`

## Metrics

| account | industry_context | run_completed | runtime_seconds | evidence_card_count | high_confidence_evidence_count | contact_count | account_score | priority_label | quality_gate_verdict | risk_flag_count | unsupported_claim_count | llm_calls | input_tokens | output_tokens | cost_usd | cache_hit_rate | report_generated | notes_errors |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| Globex Industries | B2B SaaS \| Series D revenue intelligence customer; 120 AEs across 3 segments | yes | 132.38 | 5 | 3 | 3 | 46 | needs_more_research | needs_edit | 8 | 4 | 10 | 13618 | 11052 | 0.072333 | 0.4570 | yes |  |
| Initech | B2B SaaS \| Series C; recent CRO hire focused on forecast accuracy | yes | 130.01 | 2 | 0 | 0 | 22 | do_not_send_yet | do_not_send_yet | 10 | 6 | 10 | 11453 | 10849 | 0.068640 | 0.5434 | yes |  |
| Hooli | B2B SaaS \| Late-stage; multi-product motion (PLG + sales-led) | yes | 131.32 | 2 | 0 | 0 | 22 | do_not_send_yet | do_not_send_yet | 9 | 6 | 10 | 12164 | 10919 | 0.070548 | 0.5117 | yes |  |

## Cost per Prospect

- Accounts with usage data: 3
- Total input tokens: 37,235 · output: 32,820
- Mean cost per prospect: $0.0705
- Median cost per prospect: $0.0705

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
