"""
pipeline_evals.py — Deterministic quality gates for every pipeline run.

This is the eval loop that keeps output at industry-product quality: every run
(single, batch, or CLI) is scored against three families of checks, and the
result is persisted with the run so quality is trackable over time.

  Structural gates  — the sequence is complete and well-formed: expected touch
                      count, valid channels, non-decreasing day offsets,
                      subjects on email touches, drafts for all 3 angles.
  Copy-quality      — no template placeholders leaked, company personalization
                      present, body lengths in range, and the anti-AI filter is
                      idempotent (if humanize() still changes the text, an LLM
                      pattern leaked through the pipeline).
  Score thresholds  — critic overall_quality and quality-gate verdict must
                      clear minimum bars; high-severity risk flags fail.

Severity model: "fail" checks gate the run (eval_passed=False → a prospect
should not be queued for sending); "warn" checks surface in reports but don't
block. Sample/offline states are evaluated non-strictly since fixtures are
intentionally thin.

No LLM calls and no network — this module must stay cheap enough to run on
every single pipeline execution.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from app.agents.state import ANGLE_KEYS
from app.services.account_scoring import effective_weights, expected_overall
from app.services.humanizer_rules import humanize

EXPECTED_TOUCHES = 5
VALID_CHANNELS = {"email", "linkedin", "linkedin_connect"}
MAX_EMAIL_WORDS = 200
MAX_DM_WORDS = 90
MIN_CRITIC_FAIL = 3.0
MIN_CRITIC_WARN = 3.5

# Placeholder / template leaks that must never reach a prospect.
_LEAK_PATTERNS = (
    re.compile(r"\{[a-z_]+\}", re.IGNORECASE),   # unrendered {company} etc.
    re.compile(r"\[(?:insert|placeholder|todo)[^\]]*\]", re.IGNORECASE),
    re.compile(r"\bTODO\b"),
    re.compile(r"\bXXX\b"),
    re.compile(r"lorem ipsum", re.IGNORECASE),
)


@dataclass
class EvalCheck:
    name: str
    passed: bool
    severity: str = "fail"  # "fail" | "warn"
    detail: str = ""


@dataclass
class EvalReport:
    checks: list[EvalCheck] = field(default_factory=list)

    @property
    def failures(self) -> list[EvalCheck]:
        return [c for c in self.checks if not c.passed and c.severity == "fail"]

    @property
    def warnings(self) -> list[EvalCheck]:
        return [c for c in self.checks if not c.passed and c.severity == "warn"]

    @property
    def passed(self) -> bool:
        return not self.failures

    def summary_line(self) -> str:
        total = len(self.checks)
        ok = sum(1 for c in self.checks if c.passed)
        status = "PASS" if self.passed else "FAIL"
        extra = f", {len(self.warnings)} warning(s)" if self.warnings else ""
        return f"{status} — {ok}/{total} checks passed{extra}"

    def failure_names(self) -> str:
        return "; ".join(f"{c.name}: {c.detail}" if c.detail else c.name for c in self.failures)


def _get(obj: Any, attr: str, default: Any = None) -> Any:
    if obj is None:
        return default
    if isinstance(obj, dict):
        return obj.get(attr, default)
    return getattr(obj, attr, default)


def _word_count(text: str) -> int:
    return len((text or "").split())


def _touch_texts(sequence: Any) -> list[tuple[int, str, str]]:
    """(touch_number, channel, combined subject+body) per touch."""
    out = []
    for touch in _get(sequence, "touches") or []:
        combined = " ".join(
            part for part in (_get(touch, "subject", ""), _get(touch, "body", "")) if part
        )
        out.append((_get(touch, "touch_number", 0), _get(touch, "channel", ""), combined))
    return out


def evaluate_state(state: dict, *, strict: bool = True) -> EvalReport:
    """
    Run every quality gate against a completed pipeline state.

    strict=True is for live runs (full 5-touch expectations). strict=False
    relaxes completeness gates to warnings for offline sample fixtures.
    """
    report = EvalReport()
    add = report.checks.append
    completeness = "fail" if strict else "warn"

    # ----- run-level -------------------------------------------------------
    error = state.get("error")
    add(EvalCheck("run_completed", not error, "fail", str(error or "")))
    if error:
        return report  # nothing downstream is meaningful

    enrichment = state.get("enrichment")
    strategy = state.get("strategy")
    card = state.get("card")
    critic = state.get("critic_result")

    add(EvalCheck("enrichment_present", enrichment is not None, "fail"))
    add(EvalCheck("icp_classified", _get(enrichment, "icp") is not None, completeness))
    add(EvalCheck("strategy_present", strategy is not None, "fail"))

    angle = _get(strategy, "recommended_angle", "")
    add(EvalCheck("angle_valid", angle in ANGLE_KEYS, "fail", f"got {angle!r}"))

    # ----- structural: drafts + sequence -----------------------------------
    angles = _get(card, "angles") or []
    add(
        EvalCheck(
            "drafts_all_angles",
            len(angles) == len(ANGLE_KEYS),
            completeness,
            f"{len(angles)}/{len(ANGLE_KEYS)} angle drafts",
        )
    )
    for draft in angles:
        key = _get(draft, "angle_key", "?")
        add(
            EvalCheck(
                f"draft_{key}_nonempty",
                bool(_get(draft, "email_subject", "").strip())
                and bool(_get(draft, "email_body", "").strip())
                and bool(_get(draft, "dm", "").strip()),
                "fail",
            )
        )

    sequence = _get(card, "sequence")
    touches = _get(sequence, "touches") or []
    add(EvalCheck("sequence_present", bool(touches), "fail"))
    if touches:
        # Tenants with sequence variants define their own touch count; the
        # expected count is the active track's plan length.
        variants = getattr(state.get("tenant"), "sequence_variants", None)
        if variants is not None:
            expected_touches = len(variants.resolve(state.get("sequence_variant") or "").touches)
        else:
            expected_touches = EXPECTED_TOUCHES
        add(
            EvalCheck(
                "sequence_touch_count",
                len(touches) == expected_touches,
                completeness,
                f"{len(touches)}/{expected_touches} touches",
            )
        )
        days = [_get(t, "day", 0) for t in touches]
        add(EvalCheck("sequence_days_ordered", days == sorted(days), "fail", f"days={days}"))
        channels = [_get(t, "channel", "") for t in touches]
        bad = [c for c in channels if c not in VALID_CHANNELS]
        add(EvalCheck("sequence_channels_valid", not bad, "fail", f"invalid={bad}"))
        missing_subject = [
            _get(t, "touch_number", 0)
            for t in touches
            if _get(t, "channel") == "email" and not _get(t, "subject", "").strip()
        ]
        add(
            EvalCheck(
                "email_subjects_present",
                not missing_subject,
                "fail",
                f"touches missing subject: {missing_subject}",
            )
        )
        long_touches = []
        for t in touches:
            limit = MAX_EMAIL_WORDS if _get(t, "channel") == "email" else MAX_DM_WORDS
            if _word_count(_get(t, "body", "")) > limit:
                long_touches.append(_get(t, "touch_number", 0))
        add(EvalCheck("body_length_in_range", not long_touches, "warn", f"too long: {long_touches}"))

    # ----- copy quality ----------------------------------------------------
    company = (state.get("company") or "").strip()
    all_texts: list[tuple[str, str]] = []  # (label, text)
    for draft in angles:
        key = _get(draft, "angle_key", "?")
        all_texts.append((f"draft:{key}:email", f"{_get(draft, 'email_subject', '')} {_get(draft, 'email_body', '')}"))
        all_texts.append((f"draft:{key}:dm", _get(draft, "dm", "")))
    for number, channel, text in _touch_texts(sequence):
        all_texts.append((f"touch:{number}:{channel}", text))

    leaks = []
    for label, text in all_texts:
        for pattern in _LEAK_PATTERNS:
            if pattern.search(text or ""):
                leaks.append(f"{label} ({pattern.pattern})")
                break
    add(EvalCheck("no_placeholder_leaks", not leaks, "fail", "; ".join(leaks[:5])))

    if company and all_texts:
        mentioning = sum(1 for _, text in all_texts if company.lower() in (text or "").lower())
        add(
            EvalCheck(
                "personalized_to_company",
                mentioning > 0,
                "fail",
                f"company name appears in {mentioning}/{len(all_texts)} pieces",
            )
        )

    # Anti-AI idempotency: the pipeline already ran humanize(); if a second
    # pass still changes the text, an LLM pattern leaked through assembly.
    residue = []
    for label, text in all_texts:
        if text and humanize(text) != text:
            residue.append(label)
    add(
        EvalCheck(
            "anti_ai_filter_idempotent",
            not residue,
            "warn",
            f"residual AI patterns in: {', '.join(residue[:5])}",
        )
    )

    # ----- critic thresholds -----------------------------------------------
    add(EvalCheck("critic_present", critic is not None, completeness))
    quality = _get(critic, "overall_quality")
    if quality is not None:
        add(
            EvalCheck(
                "critic_score_min",
                float(quality) >= MIN_CRITIC_FAIL,
                "fail",
                f"{quality} < {MIN_CRITIC_FAIL}",
            )
        )
        add(
            EvalCheck(
                "critic_score_target",
                float(quality) >= MIN_CRITIC_WARN,
                "warn",
                f"{quality} < {MIN_CRITIC_WARN}",
            )
        )
    gate = _get(critic, "quality_gate")
    if gate is not None:
        verdict = _get(gate, "verdict", "")
        add(
            EvalCheck(
                "gate_not_blocked",
                verdict != "do_not_send_yet",
                "fail",
                f"verdict={verdict}",
            )
        )
        high_risks = [
            _get(r, "risk_type", "?")
            for r in (_get(gate, "risk_flags") or [])
            if _get(r, "severity", "") == "high"
        ]
        add(EvalCheck("no_high_severity_risks", not high_risks, "fail", ", ".join(high_risks)))
        unsupported = _get(gate, "unsupported_claim_count", 0) or 0
        add(EvalCheck("no_unsupported_claims", unsupported == 0, "warn", f"{unsupported} unsupported"))

    # ----- account-score integrity ------------------------------------------
    account_score = _get(enrichment, "account_score")
    if account_score is not None:
        components = {
            name: _get(_get(account_score, name), "score", 0) or 0
            for name in (
                "icp_fit",
                "pain_evidence",
                "trigger_strength",
                "contact_confidence",
                "evidence_quality",
            )
        }
        overall = _get(account_score, "overall_score", 0) or 0
        expected = expected_overall(components, effective_weights(state.get("tenant")))
        add(
            EvalCheck(
                "score_components_sum",
                abs(overall - expected) <= 1,
                completeness,
                f"overall={overall}, expected={expected} from tenant weights",
            )
        )

    return report


def report_to_rows(report: EvalReport) -> list[dict]:
    return [
        {
            "check": c.name,
            "result": "pass" if c.passed else c.severity,
            "detail": c.detail,
        }
        for c in report.checks
    ]


def build_eval_markdown(results: list[tuple[str, EvalReport]], title: str = "Pipeline Eval Report") -> str:
    """Render per-account eval reports as a Markdown document (CLI + CI artifact)."""
    lines = [f"# {title}", ""]
    passed = sum(1 for _, r in results if r.passed)
    lines.append(f"**{passed}/{len(results)} accounts passed all blocking gates.**")
    lines.append("")
    for account, report in results:
        lines.append(f"## {account} — {report.summary_line()}")
        lines.append("")
        lines.append("| Check | Result | Detail |")
        lines.append("| --- | --- | --- |")
        for row in report_to_rows(report):
            detail = row["detail"].replace("|", "\\|").replace("\n", " ")
            lines.append(f"| {row['check']} | {row['result']} | {detail} |")
        lines.append("")
    return "\n".join(lines)
