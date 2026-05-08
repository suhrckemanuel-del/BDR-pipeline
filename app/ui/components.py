"""
components.py — Pure HTML render helpers for the Chalk UI.

Each function returns nothing and writes directly to Streamlit via st.markdown.
None of these touch session_state — composition is the layout module's job.
"""
from __future__ import annotations

from html import escape
from typing import Any, Iterable

import streamlit as st

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
