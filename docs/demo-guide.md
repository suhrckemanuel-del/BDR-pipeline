# Demo Guide

Use this guide to demo the BDR Pipeline clearly and conservatively.

Core framing:

> This is a founder-safe AI BDR workflow for sourced, scored, human-approved outreach.

## Before You Demo

- Use the `demo` tenant unless you have explicit permission to show another tenant.
- Use demo prospects from [tenants/demo/data/prospects.csv](../tenants/demo/data/prospects.csv) or a synthetic custom account.
- Do not show `.env`, API keys, private CRM data, or real personal contact data.
- Keep claims about workflow quality, not campaign performance.
- Have [docs/evals.md](evals.md) available if someone asks what is measured.

## Start Streamlit

```bash
streamlit run app/main.py --server.port 8503 --server.headless true
```

Open:

```text
http://localhost:8503
```

If `streamlit` is not on PATH, use the virtualenv:

```powershell
.\.venv\Scripts\python.exe -m streamlit run app/main.py --server.port 8503 --server.headless true
```

## Demo Steps

1. **Select the demo tenant**

   Show that the tenant controls positioning, persona, angles, and copy banks.

2. **Select a demo prospect**

   Pick a preset such as `Globex Industries` or `Initech`. Point out that the demo examples are anonymized/synthetic and no reply or meeting metrics are claimed.

3. **Run the pipeline**

   Explain that the workflow moves from research to scoring to strategy to sequence to critique to report.

4. **Show the Report tab first**

   This is the founder-readable artifact. It summarizes score, priority, next action, evidence, recommended contact, sequence, risk, and manual approval checklist.

5. **Explain evidence cards**

   Show the Research tab. Call out that the system separates observed evidence, derived evidence, and inference. Evidence confidence is a review signal, not a guarantee.

6. **Explain the account score**

   Show fit, pain, trigger, contact confidence, and evidence quality. Make clear that this score is account-readiness for review, not predicted reply rate.

7. **Explain the quality gate**

   Show the quality/risk panel. The gate can say `approved`, `needs_edit`, `needs_more_research`, or `do_not_send_yet`. The important product behavior is that the system can block weak outreach.

8. **Show the sequence**

   Show the multi-touch sequence. Explain that the humanizer uses LLM observations plus tenant copy banks so outputs stay closer to reviewed positioning.

9. **Download the report**

   Click `Download Markdown report`. The completed result should remain visible after the Streamlit rerun. The report is for human review and approval.

10. **Explain limitations**

   Close by saying this is a workflow proof artifact. It does not claim reply rates, meetings, revenue, deliverability, or production reliability.

## Short Talk Track

> This is not autonomous spam.
>
> It is a founder-safe workflow. The system researches an account, separates evidence from inference, scores whether the account is ready for outreach, drafts a sequence, and runs a quality/risk gate before anything is approved.
>
> The gate can say do not send yet. That is intentional.
>
> The output is a report and draft package for human review, not a send button.
>
> No reply or meeting metrics are claimed yet. The evals measure internal workflow quality and reliability: completion, evidence count, score, risk flags, unsupported claims, and report generation.

## What To Say When Asked About Metrics

Use [docs/evals.md](evals.md).

Safe answer:

> Right now I measure workflow reliability and review quality, not market outcomes. The eval checks whether runs complete, whether evidence cards are produced, whether the quality gate identifies risks, and whether the account report is generated. Reply rates, meetings, and revenue would require separate real campaign data and permission to report.

## What To Avoid Saying

Avoid:

- "This improves reply rates."
- "This booked meetings."
- "This generated revenue."
- "This replaces SDRs."
- "This is safe to auto-send."
- "These demo contacts are real verified contacts."
- "This is production-ready for sending at scale."

## Backup Demo Path

If live APIs are unavailable:

1. Show the screenshots in [docs/screenshots/](screenshots/).
2. Show the polished video [docs/bdr-pipeline-demo-v4-polished.mp4](bdr-pipeline-demo-v4-polished.mp4).
3. Run the sample eval:

   ```bash
   python scripts/run_demo_eval.py
   ```

4. Open [docs/evals.md](evals.md) and explain that sample mode is deterministic and offline.

## Demo Checklist

- Streamlit starts.
- Demo prospect presets are visible.
- Selecting a preset fills company, industry, and context.
- Custom account still works.
- Pipeline run produces Report, Sequence, Research, Contacts, and Drafts tabs.
- Markdown download is visible.
- Completed result persists after a harmless rerun.
- Clear last result works.
- Evals are linked and conservative.
