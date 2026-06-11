"""
components.py — Pure HTML render helpers for the Chalk UI.

Each function returns nothing and writes directly to Streamlit via st.markdown.
None of these touch session_state — composition is the layout module's job.
"""
from __future__ import annotations

from html import escape
from typing import Any, Iterable

import streamlit as st

from app.services import report_builder as R
from app.tenants.schema import TenantConfig

# ---------------------------------------------------------------------------
# Brand + sidebar bits
# ---------------------------------------------------------------------------
def brand_block(icon: str, name: str, tagline: str = "", show_demo_chip: bool = False) -> None:
    sub = f'<div class="brand-sub">{escape(tagline)}</div>' if tagline else ""
    st.markdown(
        f'<div class="brand-block">'
        f'<div class="brand-mark">{escape(icon or "•")}</div>'
        f'<div><div class="brand-name">{escape(name)}</div>{sub}</div>'
        f'</div>',
        unsafe_allow_html=True,
    )
    if show_demo_chip:
        st.markdown('<span class="demo-badge">Demo mode</span>', unsafe_allow_html=True)


def sidebar_section(label: str) -> None:
    st.markdown(f'<span class="sidebar-section">{escape(label)}</span>', unsafe_allow_html=True)


def prospect_list(rows: Iterable[dict], active_company: str = "") -> None:
    items: list[str] = ['<div class="prospect-list">']
    for row in rows:
        company = (row.get("company") or "").strip()
        if not company:
            continue
        industry = (row.get("industry") or row.get("notes") or "")[:48]
        priority = (row.get("priority") or "").upper()
        tier_n = {"P1": 1, "T1": 1, "P2": 2, "T2": 2, "P3": 3, "T3": 3}.get(priority, 0)
        tier_cls = f"t{tier_n}" if tier_n in (1, 2, 3) else "t3"
        badge = f"T{tier_n}" if tier_n else "T?"
        is_active = company == active_company
        cls = "prospect-row is-active" if is_active else "prospect-row"
        industry_html = (
            f'<div class="prospect-industry">{escape(industry)}</div>' if industry else ""
        )
        items.append(
            f'<div class="{cls}">'
            f'<div><div class="prospect-name">{escape(company)}</div>{industry_html}</div>'
            f'<span class="prospect-tier {tier_cls}">{escape(badge)}</span>'
            f'</div>'
        )
    items.append("</div>")
    st.markdown("".join(items), unsafe_allow_html=True)


# ---------------------------------------------------------------------------
# Top header + stage nav
# ---------------------------------------------------------------------------
STAGE_LABELS: list[tuple[str, str]] = [
    ("enrichment", "Enrichment"),
    ("strategist", "Strategist"),
    ("humanizer",  "Humanizer"),
    ("critic",     "Critic"),
    ("crm_sync",   "CRM Sync"),
]


def stage_nav(active_key: str = "", done_keys: tuple[str, ...] = ()) -> None:
    pills: list[str] = ['<div class="stage-nav">']
    for idx, (key, label) in enumerate(STAGE_LABELS, start=1):
        cls = "stage-pill"
        if key == active_key:
            cls += " is-active"
        elif key in done_keys:
            cls += " is-done"
        pills.append(
            f'<div class="{cls}">'
            f'<span class="stage-num">{idx}</span>'
            f'<span>{escape(label)}</span>'
            f'</div>'
        )
    pills.append("</div>")
    st.markdown("".join(pills), unsafe_allow_html=True)


def header_block(company: str, industry: str = "", tier_label: str = "") -> None:
    industry_html = (
        f'<span class="head-industry">{escape(industry)}</span>' if industry else ""
    )
    tier_html = ""
    if tier_label:
        tier_n = 1 if "1" in tier_label else 2 if "2" in tier_label else 3
        tier_html = (
            f'<span class="prospect-tier t{tier_n}" style="font-size:.66rem;padding:3px 8px">'
            f'{escape(tier_label)}</span>'
        )
    st.markdown(
        f'<div class="head-block">'
        f'<span class="head-company">{escape(company)}</span>'
        f'{industry_html}{tier_html}'
        f'</div>',
        unsafe_allow_html=True,
    )


def section_title(text: str) -> None:
    st.markdown(f'<div class="section-title">{escape(text)}</div>', unsafe_allow_html=True)


# ---------------------------------------------------------------------------
# KPI strip
# ---------------------------------------------------------------------------
def kpi_strip(items: list[tuple[str, str, str]]) -> None:
    """items: list of (label, value, tone) where tone in {'', 'good', 'warn', 'bad'}."""
    tiles: list[str] = ['<div class="kpi-strip">']
    for label, value, tone in items:
        tone_cls = f" {tone}" if tone else ""
        tiles.append(
            f'<div class="kpi-tile">'
            f'<div class="kpi-value{tone_cls}">{escape(str(value))}</div>'
            f'<div class="kpi-label">{escape(label)}</div>'
            f'</div>'
        )
    tiles.append("</div>")
    st.markdown("".join(tiles), unsafe_allow_html=True)


def _readable_label(value: str) -> str:
    return (value or "").replace("_", " ").title()


def account_score_panel(score: Any) -> None:
    if not score:
        return

    priority = getattr(score, "priority_label", "") or ""
    tone = {
        "high_priority": "good",
        "review": "warn",
        "needs_more_research": "warn",
        "do_not_send_yet": "bad",
    }.get(priority, "warn")
    components = [
        getattr(score, "icp_fit", None),
        getattr(score, "pain_evidence", None),
        getattr(score, "trigger_strength", None),
        getattr(score, "contact_confidence", None),
        getattr(score, "evidence_quality", None),
    ]
    comp_html: list[str] = ['<div class="score-components">']
    for comp in [c for c in components if c is not None]:
        label = getattr(comp, "label", "") or ""
        value = getattr(comp, "score", 0) or 0
        rationale = getattr(comp, "rationale", "") or ""
        comp_html.append(
            '<div class="score-component">'
            f'<div class="score-component-head"><span>{escape(label)}</span><b>{escape(str(value))}/5</b></div>'
            f'<div class="score-component-bar"><span style="width:{max(0, min(100, int(value) * 20))}%"></span></div>'
            f'<div class="score-component-rationale">{escape(rationale)}</div>'
            '</div>'
        )
    comp_html.append("</div>")

    warnings = getattr(score, "warnings", []) or []
    warnings_html = ""
    if warnings:
        warnings_html = (
            '<div class="score-warnings">'
            '<div class="score-warnings-title">Warnings</div>'
            + "".join(f'<div class="score-warning">{escape(str(w))}</div>' for w in warnings)
            + "</div>"
        )

    st.markdown(
        f'<div class="account-score-card {tone}">'
        '<div class="score-top">'
        '<div class="score-number">'
        f'<span>{escape(str(getattr(score, "overall_score", 0)))}</span><small>/100</small>'
        '</div>'
        '<div class="score-summary">'
        '<div class="score-eyebrow">Transparent account-readiness score</div>'
        f'<div class="score-priority">{escape(_readable_label(priority))}</div>'
        f'<div class="score-action">{escape(getattr(score, "recommended_action", "") or "")}</div>'
        '</div>'
        '</div>'
        f'{"".join(comp_html)}'
        f'{warnings_html}'
        '</div>',
        unsafe_allow_html=True,
    )


def quality_gate_panel(critic_result: Any) -> None:
    gate = getattr(critic_result, "quality_gate", None) if critic_result else None
    if not gate:
        return

    verdict = getattr(gate, "verdict", "") or ""
    tone = {
        "approved": "good",
        "needs_edit": "warn",
        "needs_more_research": "warn",
        "do_not_send_yet": "bad",
    }.get(verdict, "warn")
    safe = bool(getattr(gate, "safe_to_send", False))
    confidence = getattr(gate, "confidence", "") or "low"
    overall_quality = getattr(critic_result, "overall_quality", 0.0) or 0.0
    risk_flags = getattr(gate, "risk_flags", []) or []
    required_edits = getattr(gate, "required_edits", []) or []
    summary = getattr(gate, "summary", "") or ""
    evidence_note = getattr(gate, "evidence_coverage_note", "") or ""

    edit_html = ""
    if required_edits:
        edit_html = (
            '<div class="gate-list"><div class="gate-list-title">Required edits</div>'
            + "".join(f'<div class="gate-list-item">{escape(str(edit))}</div>' for edit in required_edits)
            + "</div>"
        )

    flags_html = ""
    if risk_flags:
        flag_items: list[str] = ['<div class="gate-flags">']
        for flag in risk_flags:
            severity = getattr(flag, "severity", "") or "medium"
            risk_type = _readable_label(getattr(flag, "risk_type", "") or "risk")
            touch = getattr(flag, "touch_number", None)
            touch_label = f" · T{touch}" if touch is not None else ""
            excerpt = getattr(flag, "text_excerpt", "") or ""
            rationale = getattr(flag, "rationale", "") or ""
            fix = getattr(flag, "recommended_fix", "") or ""
            flag_items.append(
                f'<div class="gate-flag sev-{escape(severity)}">'
                f'<div class="gate-flag-head"><span>{escape(risk_type)}{escape(touch_label)}</span>'
                f'<b>{escape(_readable_label(severity))}</b></div>'
                + (f'<div class="gate-flag-excerpt">{escape(excerpt)}</div>' if excerpt else "")
                + (f'<div class="gate-flag-body">{escape(rationale)}</div>' if rationale else "")
                + (f'<div class="gate-flag-fix">{escape(fix)}</div>' if fix else "")
                + '</div>'
            )
        flag_items.append("</div>")
        flags_html = "".join(flag_items)

    st.markdown(
        f'<div class="quality-gate-card {tone}">'
        '<div class="gate-top">'
        '<div class="gate-status">'
        '<div class="gate-eyebrow">Quality + Risk Gate</div>'
        f'<div class="gate-verdict">{escape(_readable_label(verdict))}</div>'
        f'<div class="gate-summary">{escape(summary)}</div>'
        '</div>'
        '<div class="gate-metrics">'
        f'<div><span>Safe to send</span><b>{escape("Yes" if safe else "No")}</b></div>'
        f'<div><span>Confidence</span><b>{escape(_readable_label(confidence))}</b></div>'
        f'<div><span>Copy quality</span><b>{overall_quality:.1f}/5</b></div>'
        f'<div><span>Risk flags</span><b>{len(risk_flags)}</b></div>'
        '</div>'
        '</div>'
        f'{edit_html}'
        f'{flags_html}'
        + (f'<div class="gate-evidence-note">{escape(evidence_note)}</div>' if evidence_note else "")
        + '</div>',
        unsafe_allow_html=True,
    )


# ---------------------------------------------------------------------------
# Account report
# ---------------------------------------------------------------------------
def report_panel(state: dict, tenant: TenantConfig) -> None:
    enrichment = state.get("enrichment")
    strategy = state.get("strategy")
    card = state.get("card")
    critic_result = state.get("critic_result")
    gate = getattr(critic_result, "quality_gate", None) if critic_result else None
    account_score = getattr(enrichment, "account_score", None) if enrichment else None
    company = (
        getattr(enrichment, "company", "")
        or state.get("company")
        or "Unknown account"
    )
    priority = getattr(account_score, "priority_label", "") if account_score else ""
    verdict = getattr(gate, "verdict", "") if gate else ""
    safe = bool(getattr(gate, "safe_to_send", False)) if gate else False
    next_action = R.determine_next_action(account_score, gate, enrichment)
    evidence = R.select_top_evidence_cards(
        getattr(enrichment, "evidence_cards", []) if enrichment else [],
        limit=6,
    )
    contact = R.select_recommended_contact(
        getattr(enrichment, "contacts", []) if enrichment else [],
        tenant,
    )
    sequence = getattr(card, "sequence", None) if card else None

    st.markdown(
        '<div class="report-hero">'
        '<div>'
        '<div class="report-eyebrow">Founder-readable account report</div>'
        f'<div class="report-company">{escape(str(company))}</div>'
        f'<div class="report-next">{escape(next_action)}</div>'
        '</div>'
        '<div class="report-hero-badges">'
        f'<span>{escape(R.format_priority(priority))}</span>'
        f'<span>{escape(R.format_gate_verdict(verdict) if gate else "Manual Review Required")}</span>'
        '</div>'
        '</div>',
        unsafe_allow_html=True,
    )

    report_decision_grid(account_score, gate, priority, verdict, safe)
    report_reason_and_checklist(account_score, strategy)
    report_evidence_highlights(evidence)
    report_recommended_contact(contact)
    report_sequence_preview(sequence, strategy, tenant)
    report_risks_and_edits(critic_result)


def report_decision_grid(account_score: Any, gate: Any, priority: str, verdict: str, safe: bool) -> None:
    score = getattr(account_score, "overall_score", None) if account_score else None
    risk_count = len(getattr(gate, "risk_flags", []) or []) if gate else 0
    tiles = [
        ("Account score", f"{score}/100" if score is not None else "Unknown", _priority_tone(priority)),
        ("Priority", R.format_priority(priority), _priority_tone(priority)),
        ("Gate", R.format_gate_verdict(verdict) if gate else "Run Critic", _gate_tone(verdict)),
        ("Safe to send", "Yes" if safe else "No", "good" if safe else "bad"),
        ("Risks", str(risk_count), "bad" if risk_count >= 3 else "warn" if risk_count else "good"),
    ]
    cells = ['<div class="report-decision-grid">']
    for label, value, tone in tiles:
        tone_cls = f" {tone}" if tone else ""
        cells.append(
            '<div class="report-decision-cell">'
            f'<div class="report-decision-value{tone_cls}">{escape(value)}</div>'
            f'<div class="report-decision-label">{escape(label)}</div>'
            '</div>'
        )
    cells.append("</div>")
    st.markdown("".join(cells), unsafe_allow_html=True)


def report_reason_and_checklist(account_score: Any, strategy: Any) -> None:
    reasons = [
        ("ICP fit", _score_component_text(account_score, "icp_fit")),
        ("Pain evidence", _score_component_text(account_score, "pain_evidence")),
        ("Trigger strength", _score_component_text(account_score, "trigger_strength")),
        ("Recommended angle", getattr(strategy, "angle_name", "") or getattr(strategy, "recommended_angle", "") or "No strategy available."),
        ("Pain signal", getattr(strategy, "pain_signal", "") or "No strategy pain signal available."),
    ]
    reason_html = "".join(
        '<div class="report-list-row">'
        f'<b>{escape(label)}</b><span>{escape(str(value))}</span>'
        '</div>'
        for label, value in reasons
    )
    checklist_html = "".join(
        f'<div class="report-check-item">{escape(item)}</div>'
        for item in R.CHECKLIST
    )
    st.markdown(
        '<div class="report-two-col">'
        '<section class="report-section">'
        '<div class="report-section-title">Why this account</div>'
        f'{reason_html}'
        '</section>'
        '<section class="report-section">'
        '<div class="report-section-title">Human approval checklist</div>'
        f'{checklist_html}'
        '</section>'
        '</div>',
        unsafe_allow_html=True,
    )


def report_evidence_highlights(evidence: list[Any]) -> None:
    section_title("Evidence highlights")
    if not evidence:
        empty_state("?", "No evidence cards", "The report can still render, but outreach should wait for stronger source-backed evidence.")
        return

    cards: list[str] = ['<div class="report-evidence-list">']
    for card in evidence:
        source_url = getattr(card, "source_url", "") or ""
        source_title = getattr(card, "source_title", "") or "No source URL"
        source_html = (
            f'<a href="{escape(source_url)}" target="_blank" rel="noopener">{escape(source_title)}</a>'
            if source_url
            else f'<span>{escape(source_title)}</span>'
        )
        cards.append(
            '<div class="report-evidence-row">'
            '<div class="report-evidence-top">'
            f'<span>{escape(getattr(card, "evidence_id", "") or "evidence")}</span>'
            f'<span>{escape(R.evidence_bucket_label(card))}</span>'
            f'<span>{escape(_readable_label(getattr(card, "confidence_label", "") or "low"))}</span>'
            '</div>'
            f'<div class="report-evidence-claim">{escape(getattr(card, "claim", "") or "Evidence claim unavailable.")}</div>'
            f'<div class="report-evidence-source">{source_html}</div>'
            '</div>'
        )
    cards.append("</div>")
    st.markdown("".join(cards), unsafe_allow_html=True)


def report_recommended_contact(contact: Any | None) -> None:
    section_title("Recommended contact")
    if not contact:
        st.markdown(
            '<div class="report-section">'
            '<div class="report-contact-name">No verified contact found</div>'
            '<div class="report-muted">Manual contact discovery is required before outreach.</div>'
            '</div>',
            unsafe_allow_html=True,
        )
        return

    confidence = int(getattr(contact, "confidence", 0) or 0)
    linkedin = getattr(contact, "linkedin_url", "") or ""
    linkedin_html = (
        f'<a href="{escape(linkedin)}" target="_blank" rel="noopener">LinkedIn</a>'
        if linkedin
        else '<span>LinkedIn not available</span>'
    )
    warning = ""
    if confidence < 70 or not getattr(contact, "email", ""):
        warning = '<div class="report-warning">Verify this contact manually before outreach.</div>'
    st.markdown(
        '<div class="report-contact-card">'
        '<div>'
        f'<div class="report-contact-name">{escape(getattr(contact, "name", "") or "Unknown contact")}</div>'
        f'<div class="report-muted">{escape(getattr(contact, "position", "") or "Unknown title")}</div>'
        '</div>'
        '<div class="report-contact-meta">'
        f'<span>{escape(getattr(contact, "email", "") or "Email not available")}</span>'
        f'<span>Confidence {confidence}</span>'
        f'{linkedin_html}'
        '</div>'
        f'{warning}'
        '</div>',
        unsafe_allow_html=True,
    )


def report_sequence_preview(sequence: Any, strategy: Any, tenant: TenantConfig) -> None:
    section_title("Outreach recommendation")
    angle = (
        getattr(sequence, "recommended_angle", "")
        or getattr(strategy, "angle_name", "")
        or "No angle available"
    )
    persona = (
        getattr(sequence, "entry_persona", "")
        or getattr(strategy, "cpo_hypothesis", "")
        or tenant.persona.title
    )
    touches = getattr(sequence, "touches", []) if sequence else []
    if not touches:
        st.markdown(
            '<div class="report-section">'
            f'<div class="report-section-title">{escape(str(angle))}</div>'
            '<div class="report-muted">No outreach sequence was drafted.</div>'
            '</div>',
            unsafe_allow_html=True,
        )
        return

    st.markdown(
        '<div class="report-sequence-head">'
        f'<span>Angle: {escape(str(angle))}</span>'
        f'<span>Persona: {escape(str(persona))}</span>'
        f'<span>{len(touches)} touches</span>'
        '</div>',
        unsafe_allow_html=True,
    )
    for touch in touches:
        touch_card(touch)


def report_risks_and_edits(critic_result: Any) -> None:
    section_title("Quality + risk")
    gate = getattr(critic_result, "quality_gate", None) if critic_result else None
    if not gate:
        st.markdown(
            '<div class="report-section">'
            '<div class="report-warning">No quality gate available. Run critic or manually review before sending.</div>'
            '</div>',
            unsafe_allow_html=True,
        )
        return

    required_edits = getattr(gate, "required_edits", []) or []
    risk_flags = getattr(gate, "risk_flags", []) or []
    edit_html = "".join(
        f'<div class="report-check-item">{escape(str(edit))}</div>'
        for edit in required_edits
    ) or '<div class="report-muted">No required edits were returned.</div>'

    risk_html = ""
    if risk_flags:
        for flag in risk_flags:
            severity = getattr(flag, "severity", "") or "medium"
            risk_html += (
                f'<div class="report-risk sev-{escape(severity)}">'
                f'<div><b>{escape(_readable_label(getattr(flag, "risk_type", "") or "Risk"))}</b>'
                f'<span>{escape(_readable_label(severity))}</span></div>'
                f'<p>{escape(getattr(flag, "rationale", "") or "No rationale provided.")}</p>'
                f'<p><b>Fix:</b> {escape(getattr(flag, "recommended_fix", "") or "No fix provided.")}</p>'
                '</div>'
            )
    else:
        risk_html = '<div class="report-muted">No risk flags were returned.</div>'

    st.markdown(
        '<div class="report-two-col">'
        '<section class="report-section">'
        '<div class="report-section-title">Required edits</div>'
        f'{edit_html}'
        '</section>'
        '<section class="report-section">'
        '<div class="report-section-title">Risk flags</div>'
        f'{risk_html}'
        '</section>'
        '</div>',
        unsafe_allow_html=True,
    )


def _score_component_text(account_score: Any, component_name: str) -> str:
    component = getattr(account_score, component_name, None) if account_score else None
    if not component:
        return "No score component available."
    score = getattr(component, "score", None)
    rationale = getattr(component, "rationale", "") or "No rationale available."
    return f"{score}/5 - {rationale}" if score is not None else rationale


def _gate_tone(verdict: str) -> str:
    return {
        "approved": "good",
        "needs_edit": "warn",
        "needs_more_research": "warn",
        "do_not_send_yet": "bad",
    }.get(verdict or "", "")


def _priority_tone(priority: str) -> str:
    return {
        "high_priority": "good",
        "review": "warn",
        "needs_more_research": "warn",
        "do_not_send_yet": "bad",
    }.get(priority or "", "")


# ---------------------------------------------------------------------------
# Sequence
# ---------------------------------------------------------------------------
def _channel_li(channel: str) -> bool:
    return "linkedin" in (channel or "").lower()


def sequence_timeline(touches: list[Any]) -> None:
    nodes: list[str] = ['<div class="seq-timeline">']
    for t in touches:
        ch = getattr(t, "channel", "") or ""
        dot_cls = "seq-dot li" if _channel_li(ch) else "seq-dot"
        n = getattr(t, "touch_number", "") or ""
        day = getattr(t, "day", "") or 0
        ch_label = "LinkedIn" if _channel_li(ch) else "Email"
        nodes.append(
            f'<div class="seq-node">'
            f'<div class="{dot_cls}">{escape(str(n))}</div>'
            f'<div class="seq-meta">'
            f'<div class="seq-day">Day {escape(str(day))}</div>'
            f'<div class="seq-ch">{escape(ch_label)}</div>'
            f'</div></div>'
        )
    nodes.append("</div>")
    st.markdown("".join(nodes), unsafe_allow_html=True)


def touch_card(touch: Any) -> None:
    n = getattr(touch, "touch_number", "")
    day = getattr(touch, "day", "")
    channel = getattr(touch, "channel", "") or ""
    is_li = _channel_li(channel)
    ch_label = "LinkedIn" if is_li else "Email"
    subject = getattr(touch, "subject", "") or ""
    body = getattr(touch, "body", "") or ""
    wc = getattr(touch, "word_count", 0) or len(body.split())
    num_cls = "touch-num li" if is_li else "touch-num"

    title = f"Touch {n} · Day {day} · {ch_label}"
    subject_html = (
        f'<div class="touch-subject">Subject<span class="subject-text">{escape(subject)}</span></div>'
        if subject
        else ""
    )
    st.markdown(
        f'<div class="touch-card">'
        f'<div class="touch-header">'
        f'<div class="{num_cls}">{escape(str(n))}</div>'
        f'<div class="touch-title">{escape(title)}</div>'
        f'<div class="touch-meta">{wc} words</div>'
        f'</div>'
        f'{subject_html}'
        f'<div class="touch-body">{escape(body)}</div>'
        f'</div>',
        unsafe_allow_html=True,
    )


# ---------------------------------------------------------------------------
# Signals + research
# ---------------------------------------------------------------------------
def signals_grid(signals: list[Any]) -> None:
    if not signals:
        st.caption("No signals returned.")
        return
    cards: list[str] = ['<div class="signals-grid">']
    for s in signals:
        src = getattr(s, "signal_source", "") or getattr(s, "signal_type", "") or "Signal"
        title = getattr(s, "title", "") or ""
        snippet = getattr(s, "snippet", "") or ""
        url = getattr(s, "url", "") or ""
        body = (
            f'<div class="signal-title">{escape(title)}</div>'
            + (f'<div class="signal-snip">{escape(snippet)}</div>' if snippet else "")
        )
        if url:
            body = f'<a href="{escape(url)}" target="_blank" rel="noopener">{body}</a>'
        cards.append(
            f'<div class="signal-card">'
            f'<div class="signal-src">{escape(src)}</div>'
            f'{body}'
            f'</div>'
        )
    cards.append("</div>")
    st.markdown("".join(cards), unsafe_allow_html=True)


def evidence_cards_grid(cards_data: list[Any]) -> None:
    if not cards_data:
        st.caption("No evidence cards available for this run.")
        return

    def label(value: str) -> str:
        return (value or "").replace("_", " ").title()

    cards: list[str] = ['<div class="evidence-grid">']
    for card in cards_data:
        source_type = getattr(card, "source_type", "") or "source"
        support_type = getattr(card, "support_type", "") or "support"
        confidence = getattr(card, "confidence_label", "") or "low"
        claim = getattr(card, "claim", "") or ""
        excerpt = getattr(card, "excerpt", "") or ""
        source_title = getattr(card, "source_title", "") or ""
        source_url = getattr(card, "source_url", "") or ""
        safe = bool(getattr(card, "safe_to_use", False))
        safe_cls = "safe" if safe else "caution"
        safe_label = "Safe to use" if safe else "Verify first"
        source_html = (
            f'<a class="evidence-source" href="{escape(source_url)}" target="_blank" rel="noopener">'
            f'{escape(source_title or "Open source")}</a>'
            if source_url
            else f'<span class="evidence-source muted">{escape(source_title or "No source URL")}</span>'
        )
        cards.append(
            '<div class="evidence-card">'
            '<div class="evidence-meta">'
            f'<span class="evidence-pill">{escape(label(source_type))}</span>'
            f'<span class="evidence-pill conf-{escape(confidence)}">{escape(label(confidence))}</span>'
            f'<span class="evidence-pill">{escape(label(support_type))}</span>'
            f'<span class="evidence-pill {safe_cls}">{escape(safe_label)}</span>'
            '</div>'
            f'<div class="evidence-claim">{escape(claim or "Evidence claim unavailable.")}</div>'
            f'<div class="evidence-excerpt">{escape(excerpt or "No excerpt available.")}</div>'
            f'{source_html}'
            '</div>'
        )
    cards.append("</div>")
    st.markdown("".join(cards), unsafe_allow_html=True)


# ---------------------------------------------------------------------------
# Contacts table
# ---------------------------------------------------------------------------
def contacts_table(contacts: list[Any]) -> None:
    if not contacts:
        st.caption("No contacts returned by Hunter.io.")
        return
    rows: list[str] = []
    for c in contacts:
        name = getattr(c, "name", "") or "—"
        position = getattr(c, "position", "") or ""
        email = getattr(c, "email", "") or ""
        seniority = (getattr(c, "seniority", "") or "").replace("_", " ").title()
        confidence = getattr(c, "confidence", 0) or 0
        rows.append(
            f"<tr>"
            f'<td class="name">{escape(name)}</td>'
            f"<td>{escape(position)}</td>"
            f"<td>{escape(seniority)}</td>"
            f'<td class="email">{escape(email)}</td>'
            f'<td><span class="conf">{confidence}</span></td>'
            f"</tr>"
        )
    st.markdown(
        '<table class="contacts-table">'
        "<thead><tr>"
        "<th>Name</th><th>Title</th><th>Seniority</th><th>Email</th><th>Conf.</th>"
        "</tr></thead>"
        f"<tbody>{''.join(rows)}</tbody>"
        "</table>",
        unsafe_allow_html=True,
    )


# ---------------------------------------------------------------------------
# Strategy + before/after
# ---------------------------------------------------------------------------
def strategy_rationale_card(strategy: Any) -> None:
    pain = getattr(strategy, "pain_signal", "") or ""
    rationale = getattr(strategy, "rationale", "") or ""
    angle_name = getattr(strategy, "angle_name", "") or ""
    label = f"Recommended angle · {angle_name}" if angle_name else "Recommended angle"
    st.markdown(
        f'<div class="rationale-card">'
        f'<div class="rationale-label">{escape(label)}</div>'
        + (f'<div class="rationale-pain">{escape(pain)}</div>' if pain else "")
        + (f'<div class="rationale-body">{escape(rationale)}</div>' if rationale else "")
        + "</div>",
        unsafe_allow_html=True,
    )


def before_after_block(before: str, after: str) -> None:
    if not (before or after):
        return
    st.markdown(
        '<div class="ba-grid">'
        f'<div class="ba-col"><div class="ba-label">Before</div>'
        f'<div class="ba-text">{escape(before or "—")}</div></div>'
        f'<div class="ba-col"><div class="ba-label">After</div>'
        f'<div class="ba-text">{escape(after or "—")}</div></div>'
        "</div>",
        unsafe_allow_html=True,
    )


# ---------------------------------------------------------------------------
# Draft card (for the Drafts tab sub-views)
# ---------------------------------------------------------------------------
def draft_card(label: str, subject: str, body: str) -> None:
    subject_html = (
        f'<div class="draft-subject">{escape(subject)}</div>' if subject else ""
    )
    st.markdown(
        f'<div class="draft-card">'
        f'<div class="draft-label">{escape(label)}</div>'
        f"{subject_html}"
        f'<div class="draft-body">{escape(body)}</div>'
        "</div>",
        unsafe_allow_html=True,
    )


# ---------------------------------------------------------------------------
# Empty state
# ---------------------------------------------------------------------------
def empty_state(icon: str, headline: str, sub: str) -> None:
    st.markdown(
        '<div class="empty-wrap">'
        f'<div class="empty-icon">{escape(icon)}</div>'
        f'<div class="empty-headline">{escape(headline)}</div>'
        f'<div class="empty-sub">{escape(sub)}</div>'
        "</div>",
        unsafe_allow_html=True,
    )
