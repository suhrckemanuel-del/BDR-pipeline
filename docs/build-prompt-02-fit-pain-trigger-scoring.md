# Build Prompt 02: Fit + Pain + Trigger Scoring

Paste this into a fresh Codex build pass after Build 01.

```text
You are Codex working in:
C:\Users\User\Documents\New project\BDR-pipeline

Context:
Build 01 added evidence-backed research cards:
- app/agents/state.py now has EvidenceCard and EnrichmentResult.evidence_cards.
- app/agents/enrichment.py builds deterministic evidence cards from live signals, job signals, Hunter contacts, ICP score, and manual trigger.
- app/ui/layout.py renders Evidence-backed research above Research summary.
- app/ui/components.py has an evidence card grid.
- app/ui/theme.py has matching CSS.

Goal:
Add a transparent Fit + Pain + Trigger scoring layer that helps a founder decide whether an account is worth reviewing before outreach. This should make the pipeline feel like a sales judgment workflow, not just an email generator.

Product positioning:
This is a founder-safe AI BDR workflow. It researches accounts, shows evidence, scores fit/timing/contact confidence, drafts outreach, critiques quality, and keeps a human in control. Do not claim reply rates, revenue, meetings, or campaign lift.

Feature boundary:
Implement account scoring only. Do not build the full human review queue, auto-send, reply analytics, LLM council, CRM rewrite, or eval harness in this pass.

Important design principle:
Use transparent deterministic scoring rules first. Do not make the score a black-box LLM judgment. The point is credibility: a viewer should be able to understand why an account is high priority, needs review, or needs more research.

Repo map:
- Shared schemas: app/agents/state.py
- Enrichment/scoring location: app/agents/enrichment.py
- Strategy context: app/agents/strategist.py
- Optional humanizer context: app/agents/humanizer.py
- Streamlit result layout: app/ui/layout.py
- UI components: app/ui/components.py
- CSS: app/ui/theme.py

Implementation requirements:

1. Add account scoring schemas in app/agents/state.py.

Suggested models:

ScoreComponent:
- label: str
- score: int, 0-5
- rationale: str
- evidence_ids: List[str] = []

AccountScoringResult:
- overall_score: int, 0-100
- priority_label: Literal["high_priority", "review", "needs_more_research", "do_not_send_yet"]
- icp_fit: ScoreComponent
- pain_evidence: ScoreComponent
- trigger_strength: ScoreComponent
- contact_confidence: ScoreComponent
- evidence_quality: ScoreComponent
- recommended_action: str
- warnings: List[str] = []

Add account_score: Optional[AccountScoringResult] to EnrichmentResult.

2. Implement deterministic scoring in app/agents/enrichment.py.

Add a helper such as _compute_account_score(...). It should use:
- existing ICPClassification score/breakdown
- EvidenceCard list from Build 01
- live_signals and job_signals
- contacts and Hunter confidence
- manual trigger if present
- tenant persona keywords where useful

Suggested component logic:

ICP fit, 0-5:
- 5: ICP score >= 80
- 4: 65-79
- 3: 50-64
- 2: 35-49
- 1: below 35 but some industry/company data exists
- 0: no usable ICP signal

Pain evidence, 0-5:
- Based on observed evidence cards that suggest a relevant business/workflow pain.
- Give more weight to high/medium confidence live_signal or job_signal cards.
- Useful keyword families can include: hiring, expansion, transformation, migration, efficiency, cost, manual, operations, workflow, coverage, support, sales, onboarding, compliance, integration, data, reporting.
- Do not overfit to these words; keep the rule simple and explain the rationale.

Trigger strength, 0-5:
- Based on manual trigger, live signals, job signals, and common buying-moment keywords.
- High score requires source-backed or explicit trigger evidence.
- Manual trigger without URL should not score above medium unless supported by live evidence.

Contact confidence, 0-5:
- Based on number of contacts, Hunter confidence, role/persona match, seniority, and email presence.
- Wrong-person risk should lower this score.

Evidence quality, 0-5:
- Based on number of evidence cards, high-confidence cards, observed vs derived/inferred mix, source URLs, and safe_to_use.
- Strong score requires multiple observed/source-backed cards.

Overall score:
- Use a transparent weighted formula.
- Suggested weights:
  - ICP fit: 25%
  - Pain evidence: 25%
  - Trigger strength: 20%
  - Contact confidence: 20%
  - Evidence quality: 10%
- Convert 0-5 component scores to 0-100.

Priority labels:
- high_priority: overall >= 75 and no critical warnings
- review: overall >= 55
- needs_more_research: overall >= 35 or evidence is thin
- do_not_send_yet: overall < 35 or no usable evidence/contact context

Warnings should be plain and useful, e.g.:
- "No high-confidence source-backed evidence found."
- "No contacts found; contact discovery needs manual review."
- "Manual trigger supplied without a source URL."
- "Pain evidence is inferred rather than observed."
- "Contact confidence is low; verify before sending."

3. Store and trace scoring.

In run_enrichment:
- Build evidence_cards as already implemented.
- Compute account_score after evidence_cards and ICP classification exist.
- Store account_score on EnrichmentResult.
- Add trace line like:
  "Enrichment: account score 72/100 · review"

4. UI rendering.

In app/ui/components.py:
- Add an account score component that shows:
  - overall score
  - priority label
  - component scores for ICP fit, pain evidence, trigger strength, contact confidence, evidence quality
  - recommended action
  - warnings
- Keep it compact and founder-friendly. Avoid a huge dashboard.

In app/ui/layout.py:
- Render the account scoring panel near the top of the result page, after KPI strip and before strategy rationale.
- Add KPI strip item:
  - "Account Score" = overall_score
  - "Priority" = readable priority label
- If no account_score exists, the UI should not crash.

In app/ui/theme.py:
- Add minimal Chalk-style CSS for the score panel.
- Use existing color tokens: green for high, orange for review/needs research, red for do_not_send_yet.
- Keep styling consistent with existing cards and 8px radius.

5. Strategy context.

Update app/agents/strategist.py:
- Include account_score in the enrichment formatting.
- Tell the Strategy Agent to treat the score as a prioritization aid, not a perfect truth.
- If the account is "needs_more_research" or "do_not_send_yet", strategy should be cautious and avoid over-specific claims.

6. Safety and honesty.

- Do not say the account score predicts reply rate.
- Do not present the score as a machine-learning model.
- Label it as a transparent account-readiness score.
- Keep observed evidence separate from inferred pain.
- The system should be willing to say "needs more research" rather than forcing a high score.

Acceptance criteria:
- After running the pipeline, the UI shows an account score panel with overall score, priority, component scores, recommended action, and warnings.
- Scores are deterministic and explainable from evidence/contacts/ICP data.
- Research tab still shows evidence cards from Build 01.
- Strategist context includes the score and behaves cautiously when evidence is thin.
- App works with no API keys or no Exa/Hunter results.
- No unrelated expansion into review queue, sending, reply analytics, or LLM council.

Verification:
1. Run:
   python -m compileall app

2. Run a no-API smoke test or direct enrichment call if practical. Confirm it returns no error and account_score exists with a cautious priority.

3. Start Streamlit:
   streamlit run app/main.py --server.port 8503 --server.headless true

4. Run one demo/anonymized company and visually verify:
   - Account score panel appears near the top.
   - KPI strip includes Account Score / Priority.
   - Component rationales are readable.
   - Evidence cards still render in Research tab.

Final response should include:
- Changed files
- Verification commands run
- One or two caveats
- Whether any behavior was intentionally deferred
```
