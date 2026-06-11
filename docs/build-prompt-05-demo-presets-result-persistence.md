# Build Prompt 05: Demo Presets + Result Persistence

Paste this into a fresh Codex build pass after Build 04.

```text
You are Codex working in:
C:\Users\User\Documents\New project\BDR-pipeline

Context:
Build 01 added evidence-backed research cards.
Build 02 added transparent Fit + Pain + Trigger account scoring.
Build 03 added a visible Quality + Risk Gate.
Build 04 added a demo-ready account report:
- app/services/report_builder.py builds deterministic Markdown reports.
- app/ui/layout.py adds a first "Report" tab and Markdown download button.
- app/ui/components.py renders the founder-readable report panel.

Current issue from Build 04:
After clicking the Markdown download button, Streamlit reruns and resets to the empty route because completed results are only held during the run-triggered render. This hurts demo polish.

Existing demo data:
tenants/demo/data/prospects.csv contains anonymized/demo prospects such as:
- Globex Industries
- Initech
- Hooli
- Pied Piper
- Stark Industries

Goal:
Make the demo flow reliable and founder-friendly by adding:
1. Loadable demo prospect presets from the tenant's prospects.csv.
2. Session persistence for the most recent completed run, so reports remain visible after download/rerun.
3. A small, honest demo-mode cue that these are anonymized/synthetic demo targets, not real campaign data.

Product positioning:
This is still a founder-safe AI BDR workflow. We are improving demo reliability and usability, not claiming real campaign performance.

Feature boundary:
Implement demo presets and result persistence only. Do not build the full review queue, auto-send, reply analytics, LLM council, CRM rewrite, eval harness, or new outbound channels in this pass.

Repo map:
- Streamlit entrypoint/routing: app/main.py
- Sidebar/results layout: app/ui/layout.py
- UI components: app/ui/components.py
- CSS: app/ui/theme.py
- Tenant demo data: tenants/demo/data/prospects.csv
- Tenant schema/loader if needed: app/tenants/*
- Report builder: app/services/report_builder.py

Implementation requirements:

1. Demo prospect presets.

In app/ui/layout.py:
- Update _load_prospects or add a helper to load prospects.csv as structured rows.
- In render_sidebar, when prospects exist, add a "Demo prospect" selectbox or similar control above/manual input fields.
- Include a blank/custom option, e.g. "Custom account".
- Selecting a demo prospect should prefill:
  - company
  - industry
  - trigger_headline or notes if a suitable column exists
- If the CSV has notes but no trigger_headline, use notes as the optional trigger/context input.
- Keep manual editing possible after loading a preset.
- Keep existing tenant switching intact.

Be careful with Streamlit session_state:
- Do not mutate a widget key after the widget has already been instantiated.
- Prefer a small on_change callback or pre-widget defaults.
- Avoid an infinite rerun loop.
- The app should still work if prospects.csv is missing, malformed, or missing columns.

2. Make the prospect list useful.

The current sidebar prospect list is display-only. Either:
- replace it with the selectbox preset loader, or
- keep the display list but add the selectbox above it.

Do not spend time making the HTML list clickable unless it is simple and robust. A normal Streamlit selectbox is acceptable and more reliable.

3. Session persistence for completed result.

In app/main.py:
- After a workflow completes successfully, store the final state in st.session_state, for example:
  - "last_final_state"
  - "last_tenant_id"
  - "last_company"
  - "last_industry"
  - "last_run_completed_at" if easy

Routing change:
- If the user has not clicked Run on this rerun, but a last_final_state exists for the active tenant, render_main(tenant, last_final_state) instead of render_empty.
- This should preserve the Report tab after:
  - clicking Markdown download
  - harmless Streamlit reruns
  - changing nothing in the sidebar

Add a "Clear last result" button in the sidebar or near the report if simple:
- It should remove last_final_state and related metadata.
- It should then rerun to the empty state.

Do not persist results to disk in this pass.

4. Demo-mode cue.

For tenant_id == "demo":
- Add a small caption or info strip in the empty state or sidebar:
  "Demo prospects are anonymized/synthetic examples for portfolio demonstration. No reply or meeting metrics are claimed."
- Do not overdo it; keep it concise.

5. Optional saved state label.

When rendering a persisted result after a rerun:
- Show a subtle caption like:
  "Showing last completed run for <Company>. Run again to refresh."
- This can be in app/main.py above render_main or as a small component.
- Keep it unobtrusive.

6. Safety and honesty.

- Do not imply demo presets are real client data.
- Do not claim reply rate or meeting rate.
- Do not store secrets or API outputs to disk.
- Do not expose .env values.
- Do not change tenant configs beyond demo data handling unless necessary.

Acceptance criteria:
- Demo tenant sidebar can load a demo prospect from prospects.csv.
- Selecting a preset fills company/industry/context enough to run the pipeline.
- Manual custom entry still works.
- After a completed run, clicking the Markdown download no longer drops the user into the empty route; the report remains visible.
- A clear last-result action exists or the persisted run can be replaced by running another account.
- App behaves gracefully if prospects.csv is missing or malformed.
- No unrelated feature expansion.

Verification:
1. Run:
   python -m compileall app

2. Start Streamlit:
   streamlit run app/main.py --server.port 8503 --server.headless true

3. In the demo tenant:
   - Select a demo prospect preset.
   - Confirm the company/industry/context fields populate.
   - Run the pipeline.
   - Confirm Report tab appears.
   - Click Markdown download.
   - Confirm the completed report remains visible after the rerun.
   - Clear last result if that control was added.

4. Test custom input still works:
   - Choose custom account or manually edit fields.
   - Confirm Run button works.

5. If easy, temporarily simulate missing prospects.csv or malformed rows:
   - App should show no presets or fall back gracefully.

Final response should include:
- Changed files
- Verification commands run
- Whether download-rerun persistence worked
- Caveats
- What was intentionally deferred
```
