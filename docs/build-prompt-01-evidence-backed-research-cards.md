# Build Prompt 01: Evidence-Backed Research Cards

Paste this into a fresh Codex build pass for the BDR Pipeline repo.

```text
You are Codex working in:
C:\Users\User\Documents\New project\BDR-pipeline

Goal:
Improve the BDR Pipeline's credibility and product value by adding evidence-backed research cards. This is the first trust-layer feature. The product should show what the system actually found, what source supports it, how confident the system is, and whether a claim is safe to use in outreach.

Product positioning:
This is not a mass cold-email generator. It is a founder-safe AI BDR workflow that researches a target account, finds contacts, drafts outreach, critiques quality, and keeps a human in the approval loop. Do not overclaim reply rates, meetings, revenue, or campaign performance.

Important repo context:
- Streamlit entrypoint: app/main.py
- UI layout: app/ui/layout.py
- UI components: app/ui/components.py
- CSS/theme: app/ui/theme.py
- Shared workflow state and schemas: app/agents/state.py
- Enrichment agent: app/agents/enrichment.py
- Strategist agent: app/agents/strategist.py
- Humanizer agent: app/agents/humanizer.py
- Critic agent: app/agents/critic.py
- Current workflow: enrichment -> strategist -> humanizer -> critic -> crm_sync
- Existing enrichment already has LiveSignal(title, url, snippet, signal_source, signal_type), job_signals, Hunter contacts, ICP classification, and research_summary.

Feature boundary:
Implement evidence-backed research cards only. Do not build the full review queue, full eval harness, reply analytics, LLM council, CRM rewrite, or automated sending in this pass.

Implementation requirements:
1. Add an EvidenceCard schema in app/agents/state.py.
   Suggested fields:
   - evidence_id: str
   - claim: str
   - source_title: str
   - source_url: str
   - source_type: Literal["live_signal", "job_signal", "contact", "icp_score", "manual_trigger"]
   - support_type: Literal["observed", "derived", "inferred"]
   - confidence_label: Literal["high", "medium", "low"]
   - confidence_score: int, 0-100
   - excerpt: str
   - safe_to_use: bool
   - used_in_outreach: bool = False
   - notes: str = ""

2. Add evidence_cards: List[EvidenceCard] to EnrichmentResult.

3. In app/agents/enrichment.py, build evidence cards deterministically from existing data:
   - live_signals become source_type="live_signal", support_type="observed"
   - job_signals become source_type="job_signal", support_type="observed"
   - Hunter contacts become source_type="contact", support_type="observed" if email/name/title exists
   - ICP score becomes source_type="icp_score", support_type="derived"
   - optional trigger headline becomes source_type="manual_trigger" if provided
   Never invent a source URL. If a URL is missing, keep source_url="" and lower confidence.

4. Confidence rules should be transparent and simple:
   - high: source has URL plus meaningful title/snippet, or Hunter confidence >= 80
   - medium: source has partial evidence, or Hunter confidence 50-79
   - low: weak/missing URL, thin snippet, low contact confidence, or derived/inferred claim

5. UI:
   - In the Research tab, render an "Evidence-backed research" section above the research summary.
   - Add a compact evidence card/grid component in app/ui/components.py.
   - Update app/ui/theme.py with minimal CSS that matches the existing Chalk design system.
   - Each card should show source type, confidence, support type, claim, excerpt, and a source link when available.
   - Add KPI strip items for evidence count and high-confidence evidence count if available.

6. Downstream prompt/context:
   - Update strategist enrichment formatting so the Strategy Agent sees the top evidence cards.
   - Instruct the strategist to ground rationale and pain_signal in evidence cards where possible and to say evidence is thin when it is thin.
   - If low-risk, pass a short evidence context into the Humanizer observation prompt. Keep this small and avoid a broad rewrite.

7. Safety:
   - Do not expose API keys, private env values, or secrets.
   - Do not imply evidence is verified beyond what the source supports.
   - Separate observed facts from derived scores and inferred sales angles.
   - If no evidence is available, the UI should say so plainly and the app should not crash.

Acceptance criteria:
- Running the Streamlit app shows evidence cards in the Research tab after a pipeline run.
- Evidence cards include source, confidence, support type, and excerpt.
- The app still works when Exa/Hunter/API keys return no data.
- Strategy output is nudged toward evidence-backed rationale without breaking the workflow.
- No unrelated feature expansion.
- No overclaimed performance metrics.

Verification:
1. Run:
   python -m compileall app
2. If the app can run locally, start Streamlit and run one anonymized/demo company:
   streamlit run app/main.py --server.port 8503 --server.headless true
3. Visually verify:
   - Research tab has Evidence-backed research above Research summary.
   - Cards are readable on desktop width.
   - Empty evidence state is clean.
4. Final response should list changed files, tests run, and any caveats.
```
