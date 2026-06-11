# Roadmap

This roadmap keeps the project honest: the current artifact is a founder-safe AI BDR workflow prototype, not an autonomous revenue engine.

## Next Immediate Polish

- Saved reports / review queue so completed account reports survive app restarts.
- Explicit manual approval state on each account: `draft`, `needs_edit`, `needs_more_research`, `approved`, `blocked`.
- Clearer empty/contact-not-found states for demo companies with thin enrichment.
- Small UI pass on persisted-result messaging and demo mode copy.
- Refresh screenshots after Build 05-07 so the README matches the latest interface.
- New demo video refresh showing presets, report download persistence, and eval metrics.

## Product Features

- Review queue / saved reports with local SQLite first.
- Manual approval workflow with reviewer notes.
- Founder voice layer based on approved examples, banned phrases, and tone constraints.
- Reply reason classifier for future inbound replies, only after real reply data exists.
- Touch-level outcome tracking for manual campaign review.
- Deliverability checks: domain, length, spammy wording, link count, and risky claims.
- Account comparison view for prioritizing several prospects before drafting.
- Redaction/export mode for portfolio-safe demos.

## Evaluation And Metrics

- Expand [docs/evals.md](evals.md) with periodic sample and live eval snapshots.
- Add regression fixtures for report generation and risk-gate extraction.
- Track evidence quality over time: observed vs. derived vs. inferred.
- Track quality-gate verdict distribution across demo accounts.
- Add eval checks for unsupported claims and overclaim language in generated reports.
- Keep campaign-performance metrics separate until real consented campaign data exists.
- Add touch-level outcome tracking when there is real reply data, without implying causality too early.

## Integrations

- CRM export/sync that writes approved reports and states, not raw unreviewed drafts.
- Notion sync hardening with clearer status and schema checks.
- CSV export for founder review workflows.
- Optional Gmail draft creation only after manual approval.
- HubSpot/Salesforce export as a later integration, not a core rewrite.
- Tenant-specific data sources and Exa query templates.

## Long-Term Vision

- A reusable outbound workbench for founder-led GTM teams.
- Evidence-first account research with a clear line between sourced facts and inference.
- Human-approved outreach packages instead of automated spam.
- A lightweight operating system for account selection, messaging, critique, and review.
- Optional LLM council for critique only, not for unchecked generation or auto-send decisions.
- Production-grade tenant management, audit logs, permissions, and durable storage if the project moves beyond portfolio proof.

## Non-Goals For Now

- Auto-send.
- Reply-rate, meeting-rate, or revenue claims without real consented campaign data.
- Replacing SDRs.
- Safe-to-send-at-scale claims.
- A full CRM rebuild.
- New outbound channels before the core review loop is durable.
