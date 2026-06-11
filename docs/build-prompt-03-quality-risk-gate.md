# Build Prompt 03: Quality + Risk Gate

Paste this into a fresh Codex build pass after Build 02.

```text
You are Codex working in:
C:\Users\User\Documents\New project\BDR-pipeline

Context:
Build 01 added evidence-backed research cards:
- EvidenceCard schema on EnrichmentResult.
- Deterministic evidence_cards from live signals, job signals, contacts, ICP score, and manual trigger.
- Streamlit Research tab shows evidence cards.

Build 02 added account-readiness scoring:
- ScoreComponent and AccountScoringResult in app/agents/state.py.
- EnrichmentResult.account_score.
- Deterministic Fit + Pain + Trigger scoring in app/agents/enrichment.py.
- UI account score panel and Account Score / Priority KPI tiles.

Current critic:
- app/agents/critic.py already scores sequence touches on:
  - pain_specificity
  - proof_relevance
  - cta_clarity
  - human_voice
- It rewrites the first paragraph of email touches when dimensions fail.
- The current CriticResult has touch_scores, overall_quality, rewrites_applied, critique_summary.

Goal:
Upgrade the critic into a visible Quality + Risk Gate. It should not only ask “is the copy good?” but also “is this safe and evidence-backed enough for a founder to approve?”

Product positioning:
This is a founder-safe AI BDR workflow. The product should show judgment before automation. The system should be willing to say “needs edit,” “needs more research,” or “do not send yet” instead of forcing every output through.

Feature boundary:
Implement the quality/risk gate only. Do not build the full review queue, auto-send, reply analytics, LLM council, CRM rewrite, or eval harness in this pass.

Important principle:
Do not make the critic invent evidence. If a specific claim is not supported by the provided evidence cards, the critic should flag it or make the copy more cautious. It should not “fix” unsupported claims by hallucinating replacement facts.

Repo map:
- Critic implementation: app/agents/critic.py
- Shared schemas: app/agents/state.py
- Enrichment/evidence/account score inputs: app/agents/enrichment.py and state objects
- UI layout: app/ui/layout.py
- UI components: app/ui/components.py
- CSS: app/ui/theme.py

Implementation requirements:

1. Extend critic schemas in app/agents/critic.py.

Keep the existing TouchScore / SequenceCritique flow, but add a risk layer.

Suggested models:

RiskFlag:
- risk_type: Literal[
    "unsupported_claim",
    "overclaiming",
    "generic_copy",
    "weak_personalization",
    "wrong_person_risk",
    "thin_evidence",
    "contact_confidence",
    "deliverability_language",
    "unclear_cta",
    "tone_issue"
  ]
- severity: Literal["low", "medium", "high"]
- touch_number: int | None = None
- text_excerpt: str = ""
- rationale: str = ""
- recommended_fix: str = ""
- evidence_ids: list[str] = []

QualityGate:
- verdict: Literal["approved", "needs_edit", "needs_more_research", "do_not_send_yet"]
- safe_to_send: bool
- confidence: Literal["high", "medium", "low"]
- summary: str
- required_edits: list[str] = []
- risk_flags: list[RiskFlag] = []
- unsupported_claim_count: int = 0
- evidence_coverage_note: str = ""

Extend CriticResult:
- quality_gate: QualityGate | None = None

If you prefer to keep all critic models local to critic.py, that is fine. Do not move unrelated schemas unless there is a clear reason.

2. Pass evidence/account score into the critic.

Update _build_critic_human_message or add a new helper so the critic sees:
- account_score overall_score and priority_label
- account_score warnings
- top evidence cards:
  - evidence_id
  - claim
  - source_type
  - support_type
  - confidence_label
  - safe_to_use
  - excerpt
- contacts with confidence, title, email presence, and role

Keep the prompt compact. Limit evidence cards to the top 6-8 most useful cards, prioritizing:
- safe_to_use=True
- observed support_type
- high/medium confidence
- live_signal/job_signal before contact/derived ICP cards

3. Update the critic system prompt.

The critic should now evaluate both:

Copy quality:
- pain_specificity
- proof_relevance
- cta_clarity
- human_voice

Risk:
- Is each specific company claim supported by the evidence cards?
- Is the copy overclaiming outcomes, reply rates, revenue, or certainty?
- Is personalization too specific for the available evidence?
- Is the recipient/contact confidence weak?
- Is the account score low enough that the output should require more research?
- Is the CTA clear and low-friction?
- Is the language likely to feel spammy or AI-generated?

Tell the critic:
- Use evidence cards as the only allowed support set.
- If evidence is thin, mark the verdict as needs_more_research or needs_edit.
- If account_score.priority_label is do_not_send_yet, the gate should normally be do_not_send_yet unless there is a strong reason otherwise.
- If a claim is plausible but not sourced, flag it as unsupported or inferred.
- Do not penalize the system for being honest about weak evidence.

4. Produce the quality gate result.

There are two acceptable approaches:

Approach A:
- Expand the existing structured LLM output to include quality_gate.

Approach B:
- Keep SequenceCritique as-is, then make a second compact structured LLM call for QualityGate using the sequence, evidence context, account score, and touch scores.

Choose the approach that is least brittle. If the current structured critic call is already long, Approach B may be safer.

5. Rewriting behavior.

Keep the current rewrite behavior, but make it evidence-safe:
- Rewrites should not add new facts not present in evidence cards.
- If the failing issue is unsupported_claim or thin_evidence, prefer making the copy more cautious/generic over inventing a new “specific” claim.
- Do not rewrite fixed tenant proof points unless the existing code already does so.
- If the critic verdict is do_not_send_yet, do not attempt to “rewrite into approved”; preserve the gate warning.

6. UI rendering.

In app/ui/components.py:
- Add a quality gate panel component.
- It should show:
  - verdict
  - safe_to_send
  - confidence
  - overall_quality
  - summary
  - required edits
  - risk flags with severity and recommended fix
  - evidence coverage note

In app/ui/layout.py:
- Render the Quality + Risk Gate panel near the top of the result page, after the account score panel and before strategy rationale.
- Add KPI tile:
  - "Gate" = readable verdict
  - optional "Risks" = count of risk_flags
- The UI must not crash if critic_result exists without quality_gate, or if the critic is skipped due to missing API key.

In app/ui/theme.py:
- Add minimal Chalk-style CSS for the quality gate panel.
- Use existing tokens:
  - green for approved
  - orange for needs_edit / needs_more_research
  - red for do_not_send_yet
- Keep radius 8px or less.

7. Trace output.

Add trace lines like:
- "Critic: quality gate verdict needs_edit · 3 risk flags"
- "Critic: unsupported claims flagged: 2"

8. Safety and honesty.

- Do not claim the gate guarantees deliverability or reply quality.
- Do not claim evidence has been externally verified beyond source presence.
- Do not claim the system is safe for auto-send.
- The output should still require human review before sending.

Acceptance criteria:
- Pipeline still runs end-to-end.
- Existing touch scoring and rewrite behavior still works.
- CriticResult includes a quality_gate when the critic runs.
- UI shows a clear Quality + Risk Gate panel.
- The gate can output approved, needs_edit, needs_more_research, or do_not_send_yet.
- The gate flags unsupported claims or thin evidence when evidence is weak.
- Missing API key / failed critic call does not crash the app.
- No unrelated expansion into review queue, sending, analytics, LLM council, or CRM rewrite.

Verification:
1. Run:
   python -m compileall app

2. Run no-API smoke test if practical:
   - The app should not crash.
   - If the critic is skipped, UI should degrade gracefully.

3. Start Streamlit:
   streamlit run app/main.py --server.port 8503 --server.headless true

4. Run one demo/anonymized company and visually verify:
   - Account score panel still appears.
   - Evidence cards still appear.
   - Quality + Risk Gate panel appears after the critic.
   - Verdict, summary, risk flags, and required edits are readable.

5. If possible, test a low-evidence company or no-API path:
   - Gate should be absent gracefully or cautious.
   - No crash.

Final response should include:
- Changed files
- Verification commands run
- Whether the critic produced risk flags in the demo
- Caveats
- What was intentionally deferred
```
