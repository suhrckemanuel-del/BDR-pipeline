"""
theme.py — Chalk design system CSS injection.

The palette and typography are a fixed design language (light mode, off-white
surfaces, soft borders). The single value driven by the active tenant is the
accent color: tenant.brand.primary_color overrides --accent and derived alpha
variants. All other tokens are constants.
"""
from __future__ import annotations

import streamlit as st

# Fixed Chalk tokens — the design system, not the brand.
_TOKENS: dict[str, str] = {
    "bg":           "#f7f7f5",
    "surface":      "#ffffff",
    "surface-2":    "#f0f0ee",
    "border":       "#e5e5e3",
    "border-light": "#ebebea",
    "border-hover": "#cacac8",
    "sidebar-bg":   "#f0f0ee",
    "text-1":       "#0a0a0a",
    "text-2":       "#555555",
    "text-3":       "#aaaaaa",
    "text-4":       "#bbbbbb",
    "green":        "#16a34a",
    "green-sub":    "#f0fdf4",
    "red":          "#dc2626",
    "red-sub":      "#fef2f2",
    "orange":       "#d97706",
    "orange-sub":   "#fffbeb",
    "li-blue":      "#0a66c2",
}


def _hex_to_rgb(hex_color: str) -> tuple[int, int, int]:
    h = hex_color.lstrip("#")
    if len(h) == 3:
        h = "".join(c * 2 for c in h)
    return int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)


def _accent_variants(primary: str) -> dict[str, str]:
    r, g, b = _hex_to_rgb(primary)
    return {
        "accent":     primary,
        "accent-sub": f"rgba({r},{g},{b},0.10)",
        "accent-bg":  f"rgba({r},{g},{b},0.06)",
    }


def inject_css(primary_color: str = "#2563EB") -> None:
    """Render the Chalk stylesheet, with --accent overridden per tenant."""
    tokens = {**_TOKENS, **_accent_variants(primary_color)}
    var_block = "\n".join(f"    --{k}: {v};" for k, v in tokens.items())

    st.markdown(
        '<link rel="preconnect" href="https://fonts.googleapis.com">'
        '<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>'
        '<link href="https://fonts.googleapis.com/css2?family=Inter:opsz,wght@14..32,300;14..32,400;14..32,500;14..32,600;14..32,700&display=swap" rel="stylesheet">',
        unsafe_allow_html=True,
    )

    st.markdown(
        f"""<style>
:root {{
{var_block}
    --radius: 8px;
    --radius-sm: 6px;
    --font: "Inter","SF Pro Text",-apple-system,"Helvetica Neue",sans-serif;
}}

/* Hide Streamlit native chrome */
header[data-testid="stHeader"] {{ display: none !important; }}
[data-testid="stToolbar"] {{ display: none !important; }}
#MainMenu {{ display: none !important; }}

/* Page */
.stApp {{ background: var(--bg) !important; }}
[data-testid="stMain"] {{ padding-top: 0 !important; }}
.main > .block-container {{ padding-top: 0.75rem !important; }}
.block-container {{ padding-top: 0.75rem !important; max-width: 1280px; }}
* {{ font-family: var(--font) !important; box-sizing: border-box; }}
hr {{ border-color: var(--border) !important; opacity: 1 !important; }}

/* Body text */
[data-testid="stMarkdownContainer"] p {{ color: var(--text-2); font-size: .875rem; line-height: 1.65; }}
[data-testid="stMarkdownContainer"] li {{ color: var(--text-2); font-size: .875rem; line-height: 1.65; }}
[data-testid="stMarkdownContainer"] strong {{ color: var(--text-1); }}
[data-testid="stMarkdownContainer"] a {{ color: var(--accent) !important; text-decoration: none; }}
[data-testid="stMarkdownContainer"] a:hover {{ text-decoration: underline; }}
.stCaption > div {{ color: var(--text-3) !important; font-size: .75rem !important; line-height: 1.5 !important; }}
code {{ background: var(--surface-2) !important; color: var(--text-2) !important; border-radius: 4px !important; padding: 1px 5px !important; font-size: .8rem !important; }}

/* Sidebar */
[data-testid="stSidebar"] {{ background: var(--sidebar-bg) !important; border-right: 1px solid var(--border) !important; }}
[data-testid="stSidebar"] [data-testid="stVerticalBlock"] {{ gap: 0.4rem; }}
[data-testid="stSidebar"] hr {{ border-color: var(--border-light) !important; margin: .35rem 0 !important; }}
[data-testid="stSidebar"] input {{
    background: #fff !important;
    border: 1px solid #ddd !important;
    color: var(--text-1) !important;
    border-radius: var(--radius-sm) !important;
    font-size: .8rem !important;
}}
[data-testid="stSidebar"] input:focus {{ border-color: var(--accent) !important; box-shadow: 0 0 0 3px var(--accent-sub) !important; }}
[data-testid="stSidebar"] label {{ color: var(--text-4) !important; font-size: .65rem !important; font-weight: 600 !important; letter-spacing: .04em !important; text-transform: uppercase !important; }}

/* Sidebar primary button */
[data-testid="stSidebar"] [data-testid="stButton"] > button[kind="primary"] {{
    background: var(--accent) !important;
    color: #fff !important;
    border: none !important;
    font-weight: 600 !important;
    font-size: .82rem !important;
    padding: .65rem .75rem !important;
    border-radius: 7px !important;
    width: 100% !important;
}}
[data-testid="stSidebar"] [data-testid="stButton"] > button[kind="primary"]:hover {{ opacity: .88 !important; }}
[data-testid="stSidebar"] [data-testid="stButton"] > button[disabled] {{
    background: #ebebea !important;
    color: var(--text-4) !important;
    border-radius: 7px !important;
}}

/* Brand block */
.brand-block {{
    display: flex;
    align-items: center;
    gap: 10px;
    padding: 6px 0 8px;
}}
.brand-mark {{
    width: 30px; height: 30px;
    background: var(--accent);
    border-radius: 7px;
    display: flex; align-items: center; justify-content: center;
    flex-shrink: 0;
    color: #fff;
    font-size: 1rem;
}}
.brand-name {{ font-size: .92rem; font-weight: 650; color: var(--text-1); letter-spacing: -.01em; line-height: 1.2; }}
.brand-sub  {{ font-size: .68rem; color: var(--text-4); font-weight: 400; margin-top: 1px; }}

.demo-badge {{
    display: inline-flex;
    align-items: center;
    width: fit-content;
    background: var(--accent-bg);
    color: var(--accent) !important;
    border: 1px solid var(--accent-sub);
    border-radius: 5px;
    padding: 3px 7px;
    font-size: .62rem !important;
    font-weight: 700;
    letter-spacing: .06em;
    text-transform: uppercase;
    margin: 4px 0 8px;
}}

.sidebar-section {{
    font-size: .6rem;
    font-weight: 700;
    letter-spacing: .09em;
    text-transform: uppercase;
    color: var(--text-4);
    padding: .85rem 0 .25rem;
    display: block;
}}

/* Prospect rows */
.prospect-list {{ display: flex; flex-direction: column; gap: 2px; margin-top: 4px; }}
.prospect-row {{
    display: grid;
    grid-template-columns: minmax(0, 1fr) auto;
    align-items: center;
    gap: 8px;
    min-height: 44px;
    padding: 7px 9px;
    border: 1px solid transparent;
    border-radius: 7px;
    color: var(--text-1) !important;
    text-decoration: none !important;
    transition: background .08s, border-color .08s;
}}
.prospect-row:hover {{ background: var(--border-light); }}
.prospect-row.is-active {{ background: var(--surface); border-color: var(--border); box-shadow: 0 1px 3px rgba(0,0,0,.06); }}
.prospect-name {{
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
    font-size: .82rem;
    font-weight: 600;
    color: var(--text-1);
    line-height: 1.2;
}}
.prospect-industry {{ font-size: .67rem; color: var(--text-4); margin-top: 2px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }}
.prospect-tier {{
    font-size: .56rem;
    font-weight: 700;
    letter-spacing: .04em;
    padding: 2px 6px;
    border-radius: 3px;
    flex-shrink: 0;
}}
.prospect-tier.t1 {{ background: var(--accent-bg); color: var(--accent); }}
.prospect-tier.t2 {{ background: var(--orange-sub); color: var(--orange); }}
.prospect-tier.t3 {{ background: var(--surface-2); color: var(--text-3); }}

/* Stage nav (top) */
.stage-nav {{
    display: flex;
    gap: 6px;
    margin: .3rem 0 1.4rem;
    padding: 4px;
    background: var(--surface);
    border: 1px solid var(--border);
    border-radius: var(--radius);
    overflow-x: auto;
}}
.stage-pill {{
    display: flex;
    align-items: center;
    gap: 8px;
    flex: 1;
    min-width: 0;
    padding: 7px 12px;
    border-radius: 6px;
    color: var(--text-3);
    font-size: .76rem;
    font-weight: 500;
    white-space: nowrap;
    transition: background .12s, color .12s;
}}
.stage-pill .stage-num {{
    width: 18px; height: 18px;
    border-radius: 50%;
    background: var(--surface-2);
    color: var(--text-3);
    font-size: .62rem;
    font-weight: 700;
    display: flex; align-items: center; justify-content: center;
    flex-shrink: 0;
}}
.stage-pill.is-active {{ background: var(--accent-bg); color: var(--accent); font-weight: 600; }}
.stage-pill.is-active .stage-num {{ background: var(--accent); color: #fff; }}
.stage-pill.is-done {{ color: var(--text-2); }}
.stage-pill.is-done .stage-num {{ background: var(--green); color: #fff; }}

/* Header block */
.head-block {{ display: flex; align-items: baseline; gap: 12px; margin: .5rem 0 .25rem; }}
.head-company {{ font-size: 1.5rem; font-weight: 700; color: var(--text-1); letter-spacing: -.02em; }}
.head-industry {{ font-size: .85rem; color: var(--text-3); }}

/* KPI strip */
.kpi-strip {{ display: flex; gap: 8px; flex-wrap: wrap; margin: .25rem 0 1.4rem; }}
.kpi-tile {{
    background: var(--surface);
    border: 1px solid var(--border);
    border-radius: var(--radius);
    padding: .8rem 1rem;
    flex: 1;
    min-width: 110px;
}}
.kpi-value {{
    font-size: 1.06rem;
    font-weight: 700;
    color: var(--text-1);
    letter-spacing: -.03em;
    font-variant-numeric: tabular-nums;
}}
.kpi-value.good {{ color: var(--green); }}
.kpi-value.warn {{ color: var(--orange); }}
.kpi-value.bad  {{ color: var(--red); }}
.kpi-label {{
    font-size: .6rem;
    color: var(--text-4);
    margin-top: .2rem;
    text-transform: uppercase;
    letter-spacing: .09em;
    font-weight: 600;
}}

/* Account score */
.account-score-card {{
    background: var(--surface);
    border: 1px solid var(--border);
    border-radius: var(--radius);
    padding: 16px 18px;
    margin: -0.25rem 0 12px;
}}
.account-score-card.good {{ border-color: rgba(22,163,74,.32); }}
.account-score-card.warn {{ border-color: rgba(217,119,6,.32); }}
.account-score-card.bad {{ border-color: rgba(220,38,38,.28); }}
.score-top {{
    display: grid;
    grid-template-columns: auto minmax(0, 1fr);
    gap: 14px;
    align-items: center;
    margin-bottom: 14px;
}}
.score-number {{
    width: 76px;
    height: 76px;
    border-radius: var(--radius);
    background: var(--surface-2);
    display: flex;
    align-items: baseline;
    justify-content: center;
    padding-top: 17px;
    font-variant-numeric: tabular-nums;
}}
.account-score-card.good .score-number {{ background: var(--green-sub); color: var(--green); }}
.account-score-card.warn .score-number {{ background: var(--orange-sub); color: var(--orange); }}
.account-score-card.bad .score-number {{ background: var(--red-sub); color: var(--red); }}
.score-number span {{ font-size: 1.65rem; line-height: 1; font-weight: 750; }}
.score-number small {{ font-size: .68rem; font-weight: 700; margin-left: 2px; }}
.score-eyebrow {{
    font-size: .6rem;
    font-weight: 700;
    text-transform: uppercase;
    letter-spacing: .08em;
    color: var(--text-4);
    margin-bottom: 4px;
}}
.score-priority {{ font-size: 1.05rem; font-weight: 700; color: var(--text-1); line-height: 1.25; }}
.score-action {{ font-size: .82rem; color: var(--text-2); line-height: 1.55; margin-top: 5px; max-width: 820px; }}
.score-components {{ display: grid; grid-template-columns: repeat(5, minmax(0, 1fr)); gap: 8px; }}
.score-component {{
    border: 1px solid var(--border-light);
    border-radius: var(--radius-sm);
    padding: 10px;
    background: var(--bg);
}}
.score-component-head {{ display: flex; align-items: center; justify-content: space-between; gap: 8px; }}
.score-component-head span {{
    font-size: .68rem;
    color: var(--text-1);
    font-weight: 700;
    line-height: 1.25;
}}
.score-component-head b {{ font-size: .66rem; color: var(--text-3); font-variant-numeric: tabular-nums; }}
.score-component-bar {{
    height: 5px;
    background: var(--border-light);
    border-radius: 99px;
    overflow: hidden;
    margin: 8px 0;
}}
.score-component-bar span {{ display: block; height: 100%; background: var(--accent); }}
.score-component-rationale {{ font-size: .7rem; line-height: 1.45; color: var(--text-2); }}
.score-warnings {{
    border-top: 1px solid var(--border-light);
    margin-top: 12px;
    padding-top: 10px;
}}
.score-warnings-title {{
    font-size: .58rem;
    font-weight: 700;
    text-transform: uppercase;
    letter-spacing: .08em;
    color: var(--text-4);
    margin-bottom: 6px;
}}
.score-warning {{
    font-size: .76rem;
    color: var(--text-2);
    line-height: 1.5;
    margin-top: 3px;
}}

@media (max-width: 1000px) {{
    .score-components {{ grid-template-columns: 1fr 1fr; }}
}}
@media (max-width: 650px) {{
    .score-top {{ grid-template-columns: 1fr; }}
    .score-components {{ grid-template-columns: 1fr; }}
}}

/* Quality gate */
.quality-gate-card {{
    background: var(--surface);
    border: 1px solid var(--border);
    border-radius: var(--radius);
    padding: 16px 18px;
    margin-bottom: 12px;
}}
.quality-gate-card.good {{ border-color: rgba(22,163,74,.32); }}
.quality-gate-card.warn {{ border-color: rgba(217,119,6,.32); }}
.quality-gate-card.bad {{ border-color: rgba(220,38,38,.28); }}
.gate-top {{
    display: grid;
    grid-template-columns: minmax(0, 1fr) auto;
    gap: 16px;
    align-items: start;
}}
.gate-eyebrow {{
    font-size: .6rem;
    font-weight: 700;
    text-transform: uppercase;
    letter-spacing: .08em;
    color: var(--text-4);
    margin-bottom: 4px;
}}
.gate-verdict {{ font-size: 1.05rem; font-weight: 700; color: var(--text-1); line-height: 1.25; }}
.gate-summary {{ font-size: .82rem; color: var(--text-2); line-height: 1.55; margin-top: 5px; max-width: 820px; }}
.gate-metrics {{
    display: grid;
    grid-template-columns: repeat(2, minmax(90px, 1fr));
    gap: 8px;
    min-width: 280px;
}}
.gate-metrics div {{
    border: 1px solid var(--border-light);
    border-radius: var(--radius-sm);
    padding: 8px 10px;
    background: var(--bg);
}}
.gate-metrics span {{
    display: block;
    font-size: .56rem;
    color: var(--text-4);
    font-weight: 700;
    letter-spacing: .08em;
    text-transform: uppercase;
    margin-bottom: 3px;
}}
.gate-metrics b {{ font-size: .78rem; color: var(--text-1); font-variant-numeric: tabular-nums; }}
.gate-list {{ border-top: 1px solid var(--border-light); margin-top: 12px; padding-top: 10px; }}
.gate-list-title {{
    font-size: .58rem;
    font-weight: 700;
    text-transform: uppercase;
    letter-spacing: .08em;
    color: var(--text-4);
    margin-bottom: 6px;
}}
.gate-list-item {{ font-size: .76rem; color: var(--text-2); line-height: 1.5; margin-top: 3px; }}
.gate-flags {{
    display: grid;
    grid-template-columns: 1fr 1fr;
    gap: 8px;
    margin-top: 12px;
}}
.gate-flag {{
    border: 1px solid var(--border-light);
    border-radius: var(--radius-sm);
    padding: 10px;
    background: var(--bg);
}}
.gate-flag.sev-low {{ border-color: var(--border-light); }}
.gate-flag.sev-medium {{ border-color: rgba(217,119,6,.24); background: var(--orange-sub); }}
.gate-flag.sev-high {{ border-color: rgba(220,38,38,.22); background: var(--red-sub); }}
.gate-flag-head {{ display: flex; justify-content: space-between; gap: 8px; align-items: center; }}
.gate-flag-head span {{ font-size: .68rem; font-weight: 700; color: var(--text-1); }}
.gate-flag-head b {{
    font-size: .55rem;
    letter-spacing: .08em;
    text-transform: uppercase;
    color: var(--text-3);
}}
.gate-flag.sev-medium .gate-flag-head b {{ color: var(--orange); }}
.gate-flag.sev-high .gate-flag-head b {{ color: var(--red); }}
.gate-flag-excerpt {{
    font-size: .72rem;
    color: var(--text-1);
    line-height: 1.45;
    margin-top: 7px;
    font-style: italic;
}}
.gate-flag-body {{ font-size: .72rem; color: var(--text-2); line-height: 1.45; margin-top: 7px; }}
.gate-flag-fix {{ font-size: .72rem; color: var(--text-2); line-height: 1.45; margin-top: 6px; font-weight: 600; }}
.gate-evidence-note {{
    border-top: 1px solid var(--border-light);
    margin-top: 12px;
    padding-top: 10px;
    font-size: .76rem;
    color: var(--text-3);
    line-height: 1.5;
}}

@media (max-width: 900px) {{
    .gate-top {{ grid-template-columns: 1fr; }}
    .gate-metrics {{ min-width: 0; }}
    .gate-flags {{ grid-template-columns: 1fr; }}
}}

/* Section title */
.section-title {{
    font-size: .65rem;
    font-weight: 700;
    text-transform: uppercase;
    letter-spacing: .09em;
    color: var(--text-4);
    margin: 1.5rem 0 .8rem;
    display: flex;
    align-items: center;
    gap: 8px;
}}
.section-title::after {{ content: ''; flex: 1; height: 1px; background: var(--border-light); }}

/* Tabs */
.stTabs [data-baseweb="tab-list"] {{
    background: var(--surface);
    border: 1px solid var(--border);
    border-radius: var(--radius);
    padding: 3px;
    gap: 2px;
}}
.stTabs [data-baseweb="tab"] {{
    background: transparent;
    border: none;
    color: var(--text-3);
    font-size: .8rem;
    font-weight: 500;
    padding: .4rem .9rem;
    border-radius: 6px;
}}
.stTabs [aria-selected="true"] {{
    background: var(--surface-2) !important;
    color: var(--text-1) !important;
    font-weight: 600 !important;
}}
.stTabs [data-baseweb="tab"]:hover:not([aria-selected="true"]) {{ color: var(--text-2); background: var(--surface-2); }}
[data-testid="stTabPanel"] {{ padding-top: 1rem; }}

/* Account report */
.report-hero {{
    display: grid;
    grid-template-columns: minmax(0, 1fr) auto;
    gap: 16px;
    align-items: start;
    background: var(--surface);
    border: 1px solid var(--border);
    border-radius: var(--radius);
    padding: 18px 20px;
    margin-bottom: 10px;
}}
.report-eyebrow {{
    font-size: .6rem;
    font-weight: 700;
    text-transform: uppercase;
    letter-spacing: .08em;
    color: var(--accent);
    margin-bottom: 5px;
}}
.report-company {{
    font-size: 1.28rem;
    font-weight: 750;
    color: var(--text-1);
    line-height: 1.2;
}}
.report-next {{
    margin-top: 8px;
    color: var(--text-2);
    font-size: .86rem;
    line-height: 1.55;
    max-width: 820px;
}}
.report-hero-badges {{
    display: flex;
    gap: 6px;
    flex-wrap: wrap;
    justify-content: flex-end;
}}
.report-hero-badges span {{
    border: 1px solid var(--border-light);
    background: var(--surface-2);
    border-radius: 4px;
    padding: 4px 7px;
    color: var(--text-2);
    font-size: .62rem;
    font-weight: 750;
    text-transform: uppercase;
    letter-spacing: .06em;
    white-space: nowrap;
}}
.report-decision-grid {{
    display: grid;
    grid-template-columns: repeat(5, minmax(0, 1fr));
    gap: 8px;
    margin-bottom: 12px;
}}
.report-decision-cell {{
    background: var(--surface);
    border: 1px solid var(--border);
    border-radius: var(--radius);
    padding: 11px 13px;
    min-width: 0;
}}
.report-decision-value {{
    color: var(--text-1);
    font-size: .94rem;
    font-weight: 750;
    line-height: 1.25;
    overflow-wrap: anywhere;
}}
.report-decision-value.good {{ color: var(--green); }}
.report-decision-value.warn {{ color: var(--orange); }}
.report-decision-value.bad {{ color: var(--red); }}
.report-decision-label {{
    margin-top: 5px;
    color: var(--text-4);
    font-size: .58rem;
    font-weight: 700;
    text-transform: uppercase;
    letter-spacing: .08em;
}}
.report-two-col {{
    display: grid;
    grid-template-columns: minmax(0, 1fr) minmax(0, 1fr);
    gap: 10px;
    margin-bottom: 12px;
}}
.report-section {{
    background: var(--surface);
    border: 1px solid var(--border);
    border-radius: var(--radius);
    padding: 15px 17px;
    min-width: 0;
}}
.report-section-title {{
    color: var(--text-4);
    font-size: .6rem;
    font-weight: 750;
    letter-spacing: .08em;
    text-transform: uppercase;
    margin-bottom: 10px;
}}
.report-list-row {{
    display: grid;
    grid-template-columns: 130px minmax(0, 1fr);
    gap: 10px;
    padding: 8px 0;
    border-top: 1px solid var(--border-light);
}}
.report-list-row:first-of-type {{ border-top: none; padding-top: 0; }}
.report-list-row b {{
    color: var(--text-1);
    font-size: .74rem;
    line-height: 1.45;
}}
.report-list-row span {{
    color: var(--text-2);
    font-size: .78rem;
    line-height: 1.55;
    overflow-wrap: anywhere;
}}
.report-check-item {{
    color: var(--text-2);
    font-size: .78rem;
    line-height: 1.55;
    padding: 7px 0;
    border-top: 1px solid var(--border-light);
}}
.report-check-item:first-of-type {{ border-top: none; padding-top: 0; }}
.report-evidence-list {{
    display: grid;
    grid-template-columns: 1fr 1fr;
    gap: 9px;
}}
.report-evidence-row {{
    background: var(--surface);
    border: 1px solid var(--border);
    border-radius: var(--radius);
    padding: 13px 15px;
    min-width: 0;
}}
.report-evidence-top {{
    display: flex;
    gap: 5px;
    flex-wrap: wrap;
    margin-bottom: 8px;
}}
.report-evidence-top span {{
    background: var(--surface-2);
    border: 1px solid var(--border-light);
    border-radius: 4px;
    padding: 2px 6px;
    color: var(--text-3);
    font-size: .56rem;
    font-weight: 750;
    letter-spacing: .06em;
    text-transform: uppercase;
}}
.report-evidence-claim {{
    color: var(--text-1);
    font-size: .84rem;
    font-weight: 650;
    line-height: 1.45;
    overflow-wrap: anywhere;
}}
.report-evidence-source {{
    margin-top: 8px;
    color: var(--text-3);
    font-size: .72rem;
    font-weight: 600;
    overflow-wrap: anywhere;
}}
.report-contact-card {{
    display: grid;
    grid-template-columns: minmax(0, 1fr) minmax(230px, auto);
    gap: 12px;
    align-items: start;
    background: var(--surface);
    border: 1px solid var(--border);
    border-radius: var(--radius);
    padding: 15px 17px;
}}
.report-contact-name {{
    color: var(--text-1);
    font-size: .94rem;
    font-weight: 750;
    line-height: 1.3;
}}
.report-contact-meta {{
    display: flex;
    gap: 6px;
    flex-wrap: wrap;
    justify-content: flex-end;
}}
.report-contact-meta span,
.report-contact-meta a {{
    background: var(--surface-2);
    border: 1px solid var(--border-light);
    border-radius: 4px;
    padding: 3px 7px;
    color: var(--text-2) !important;
    font-size: .68rem;
    font-weight: 650;
    overflow-wrap: anywhere;
}}
.report-muted {{
    color: var(--text-3);
    font-size: .78rem;
    line-height: 1.55;
    margin-top: 4px;
}}
.report-warning {{
    grid-column: 1 / -1;
    color: var(--orange);
    background: var(--orange-sub);
    border: 1px solid rgba(217,119,6,.22);
    border-radius: var(--radius-sm);
    padding: 8px 10px;
    font-size: .76rem;
    line-height: 1.45;
}}
.report-sequence-head {{
    display: flex;
    gap: 6px;
    flex-wrap: wrap;
    margin-bottom: 10px;
}}
.report-sequence-head span {{
    background: var(--surface);
    border: 1px solid var(--border);
    border-radius: 4px;
    color: var(--text-2);
    font-size: .7rem;
    font-weight: 650;
    padding: 4px 8px;
}}
.report-risk {{
    border: 1px solid var(--border-light);
    border-radius: var(--radius-sm);
    padding: 10px;
    margin-top: 8px;
    background: var(--bg);
}}
.report-risk:first-of-type {{ margin-top: 0; }}
.report-risk.sev-medium {{ border-color: rgba(217,119,6,.24); background: var(--orange-sub); }}
.report-risk.sev-high {{ border-color: rgba(220,38,38,.22); background: var(--red-sub); }}
.report-risk div {{
    display: flex;
    justify-content: space-between;
    gap: 8px;
    align-items: center;
}}
.report-risk b {{ color: var(--text-1); font-size: .7rem; }}
.report-risk span {{
    color: var(--text-3);
    font-size: .55rem;
    font-weight: 750;
    letter-spacing: .08em;
    text-transform: uppercase;
}}
.report-risk p {{
    margin: 7px 0 0;
    color: var(--text-2);
    font-size: .74rem;
    line-height: 1.5;
}}

@media (max-width: 900px) {{
    .report-hero {{ grid-template-columns: 1fr; }}
    .report-hero-badges {{ justify-content: flex-start; }}
    .report-decision-grid {{ grid-template-columns: 1fr 1fr; }}
    .report-two-col {{ grid-template-columns: 1fr; }}
    .report-evidence-list {{ grid-template-columns: 1fr; }}
    .report-contact-card {{ grid-template-columns: 1fr; }}
    .report-contact-meta {{ justify-content: flex-start; }}
}}
@media (max-width: 560px) {{
    .report-decision-grid {{ grid-template-columns: 1fr; }}
    .report-list-row {{ grid-template-columns: 1fr; gap: 3px; }}
}}

/* Touch cards */
.touch-card {{
    background: var(--surface);
    border: 1px solid var(--border);
    border-radius: var(--radius);
    margin-bottom: 10px;
    overflow: hidden;
    transition: border-color .12s;
}}
.touch-card:hover {{ border-color: var(--border-hover); }}
.touch-header {{
    display: flex;
    align-items: center;
    gap: 10px;
    padding: 11px 14px;
}}
.touch-num {{
    width: 22px; height: 22px;
    border-radius: 50%;
    background: var(--accent);
    color: #fff;
    font-size: .65rem;
    font-weight: 700;
    display: flex; align-items: center; justify-content: center;
    flex-shrink: 0;
    font-variant-numeric: tabular-nums;
}}
.touch-num.li {{ background: var(--li-blue); }}
.touch-title {{ font-size: .82rem; font-weight: 500; color: var(--text-1); flex: 1; }}
.touch-meta  {{ font-size: .68rem; color: var(--text-4); font-variant-numeric: tabular-nums; }}
.touch-subject {{
    padding: 8px 14px 0 46px;
    font-size: .66rem;
    font-weight: 700;
    text-transform: uppercase;
    letter-spacing: .07em;
    color: var(--text-4);
    border-top: 1px solid var(--border-light);
}}
.touch-subject .subject-text {{ color: var(--text-2); text-transform: none; letter-spacing: 0; font-weight: 500; margin-left: 6px; }}
.touch-body {{
    padding: 6px 14px 14px 46px;
    font-size: .82rem;
    line-height: 1.72;
    color: var(--text-2);
    white-space: pre-wrap;
}}

/* Sequence timeline */
.seq-timeline {{
    display: flex;
    align-items: flex-start;
    padding: .6rem 0 1rem;
    overflow-x: auto;
    position: relative;
}}
.seq-timeline::before {{
    content: '';
    position: absolute;
    top: 13px; left: 14px; right: 14px;
    height: 1px;
    background: var(--border-light);
    z-index: 0;
}}
.seq-node {{
    display: flex;
    flex-direction: column;
    align-items: center;
    flex: 1;
    position: relative;
    min-width: 70px;
    z-index: 1;
}}
.seq-dot {{
    width: 26px; height: 26px;
    border-radius: 50%;
    background: var(--accent);
    border: 2px solid var(--bg);
    font-size: .65rem;
    color: #fff;
    font-weight: 700;
    display: flex; align-items: center; justify-content: center;
}}
.seq-dot.li {{ background: var(--li-blue); }}
.seq-meta {{ margin-top: .45rem; text-align: center; line-height: 1.4; }}
.seq-day {{ font-weight: 600; font-size: .67rem; color: var(--text-1); }}
.seq-ch  {{ font-size: .6rem; color: var(--text-4); margin-top: 1px; text-transform: capitalize; }}

/* Signals grid */
.signals-grid {{ display: grid; grid-template-columns: 1fr 1fr; gap: 8px; }}
.signal-card {{
    background: var(--surface);
    border: 1px solid var(--border);
    border-radius: var(--radius);
    padding: 11px 14px;
}}
.signal-src {{
    font-size: .6rem;
    font-weight: 700;
    text-transform: uppercase;
    letter-spacing: .08em;
    color: var(--accent);
    margin-bottom: 4px;
}}
.signal-title {{ font-size: .82rem; color: var(--text-1); font-weight: 500; line-height: 1.4; }}
.signal-snip {{ font-size: .76rem; color: var(--text-3); line-height: 1.55; margin-top: 4px; }}
.signal-card a {{ color: inherit !important; }}
.signal-card a:hover .signal-title {{ color: var(--accent); }}

@media (max-width: 800px) {{
    .signals-grid {{ grid-template-columns: 1fr; }}
}}

/* Evidence cards */
.evidence-grid {{ display: grid; grid-template-columns: 1fr 1fr; gap: 10px; }}
.evidence-card {{
    background: var(--surface);
    border: 1px solid var(--border);
    border-radius: var(--radius);
    padding: 13px 15px;
}}
.evidence-meta {{
    display: flex;
    align-items: center;
    gap: 5px;
    flex-wrap: wrap;
    margin-bottom: 8px;
}}
.evidence-pill {{
    display: inline-flex;
    align-items: center;
    border: 1px solid var(--border-light);
    border-radius: 4px;
    padding: 2px 6px;
    color: var(--text-3);
    background: var(--surface-2);
    font-size: .58rem;
    font-weight: 700;
    letter-spacing: .06em;
    text-transform: uppercase;
}}
.evidence-pill.conf-high {{ background: var(--green-sub); color: var(--green); border-color: rgba(22,163,74,.24); }}
.evidence-pill.conf-medium {{ background: var(--orange-sub); color: var(--orange); border-color: rgba(217,119,6,.24); }}
.evidence-pill.conf-low {{ background: var(--surface-2); color: var(--text-3); }}
.evidence-pill.safe {{ background: var(--accent-bg); color: var(--accent); border-color: var(--accent-sub); }}
.evidence-pill.caution {{ background: var(--red-sub); color: var(--red); border-color: rgba(220,38,38,.18); }}
.evidence-claim {{ font-size: .86rem; color: var(--text-1); font-weight: 600; line-height: 1.45; }}
.evidence-excerpt {{ font-size: .76rem; color: var(--text-2); line-height: 1.6; margin-top: 6px; }}
.evidence-source {{
    display: inline-block;
    margin-top: 9px;
    font-size: .72rem;
    font-weight: 600;
    color: var(--accent) !important;
}}
.evidence-source.muted {{ color: var(--text-3) !important; }}

@media (max-width: 800px) {{
    .evidence-grid {{ grid-template-columns: 1fr; }}
}}

/* Contacts table */
.contacts-table {{
    width: 100%;
    border-collapse: separate;
    border-spacing: 0;
    background: var(--surface);
    border: 1px solid var(--border);
    border-radius: var(--radius);
    overflow: hidden;
    font-size: .82rem;
}}
.contacts-table th {{
    background: var(--surface-2);
    color: var(--text-4);
    font-size: .6rem;
    font-weight: 700;
    text-transform: uppercase;
    letter-spacing: .08em;
    text-align: left;
    padding: 9px 14px;
    border-bottom: 1px solid var(--border);
}}
.contacts-table td {{
    padding: 11px 14px;
    border-bottom: 1px solid var(--border-light);
    color: var(--text-2);
    vertical-align: top;
}}
.contacts-table tr:last-child td {{ border-bottom: none; }}
.contacts-table tr:hover td {{ background: var(--surface-2); }}
.contacts-table .name {{ color: var(--text-1); font-weight: 600; }}
.contacts-table .email {{ font-family: "SF Mono", Menlo, Consolas, monospace; font-size: .75rem; color: var(--accent); }}
.contacts-table .conf {{
    display: inline-block;
    background: var(--surface-2);
    color: var(--text-2);
    border-radius: 4px;
    padding: 2px 7px;
    font-size: .68rem;
    font-weight: 600;
    font-variant-numeric: tabular-nums;
}}

/* Strategy / Before-After cards */
.rationale-card {{
    background: var(--surface);
    border: 1px solid var(--border);
    border-radius: var(--radius);
    padding: 16px 18px;
    margin-bottom: 12px;
}}
.rationale-label {{
    font-size: .6rem;
    font-weight: 700;
    text-transform: uppercase;
    letter-spacing: .08em;
    color: var(--accent);
    margin-bottom: 6px;
}}
.rationale-pain {{
    font-size: .92rem;
    color: var(--text-1);
    font-weight: 600;
    line-height: 1.5;
    margin-bottom: 10px;
}}
.rationale-body {{ font-size: .85rem; color: var(--text-2); line-height: 1.65; }}

.ba-grid {{ display: grid; grid-template-columns: 1fr 1fr; gap: 10px; margin-bottom: 16px; }}
.ba-col {{
    background: var(--surface);
    border: 1px solid var(--border);
    border-radius: var(--radius);
    padding: 14px 16px;
}}
.ba-label {{
    font-size: .58rem;
    font-weight: 700;
    text-transform: uppercase;
    letter-spacing: .08em;
    color: var(--text-4);
    margin-bottom: 8px;
}}
.ba-text {{ font-size: .85rem; color: var(--text-2); line-height: 1.65; white-space: pre-wrap; }}
@media (max-width: 800px) {{ .ba-grid {{ grid-template-columns: 1fr; }} }}

/* Draft card */
.draft-card {{
    background: var(--surface);
    border: 1px solid var(--border);
    border-radius: var(--radius);
    padding: 16px 18px;
    margin-bottom: 12px;
}}
.draft-label {{
    font-size: .58rem;
    font-weight: 700;
    text-transform: uppercase;
    letter-spacing: .08em;
    color: var(--text-4);
    margin-bottom: 6px;
}}
.draft-subject {{
    font-size: .9rem;
    color: var(--text-1);
    font-weight: 600;
    margin-bottom: 12px;
}}
.draft-body {{ font-size: .85rem; color: var(--text-2); line-height: 1.7; white-space: pre-wrap; }}

/* Empty state */
.empty-wrap {{
    display: flex;
    flex-direction: column;
    align-items: center;
    justify-content: center;
    padding: 5rem 2rem;
    text-align: center;
    gap: 14px;
}}
.empty-icon {{
    width: 60px; height: 60px;
    border-radius: 14px;
    background: var(--surface);
    border: 1px solid var(--border);
    display: flex; align-items: center; justify-content: center;
    color: var(--text-3);
    font-size: 1.6rem;
}}
.empty-headline {{ font-size: 1.1rem; font-weight: 650; color: var(--text-1); letter-spacing: -.02em; }}
.empty-sub {{ font-size: .88rem; color: var(--text-3); max-width: 460px; line-height: 1.6; }}

/* Streamlit alerts → soft tints */
[data-testid="stAlert"] {{ border-radius: var(--radius) !important; }}
div[class*="stSuccess"] {{ background: var(--green-sub) !important; border: 1px solid var(--green) !important; border-radius: var(--radius) !important; color: var(--text-1) !important; }}
div[class*="stError"]   {{ background: var(--red-sub)   !important; border: 1px solid var(--red)   !important; border-radius: var(--radius) !important; color: var(--text-1) !important; }}
div[class*="stWarning"] {{ background: var(--orange-sub)!important; border: 1px solid var(--orange)!important; border-radius: var(--radius) !important; color: var(--text-1) !important; }}
div[class*="stInfo"]    {{ background: var(--accent-bg) !important; border: 1px solid var(--accent)!important; border-radius: var(--radius) !important; color: var(--text-1) !important; }}

/* Form controls */
[data-testid="stTextArea"] textarea,
[data-testid="stTextInput"] input {{
    background: var(--surface) !important;
    border: 1px solid var(--border) !important;
    color: var(--text-1) !important;
    border-radius: var(--radius-sm) !important;
    font-size: .82rem !important;
    line-height: 1.6 !important;
    caret-color: var(--accent) !important;
}}
[data-testid="stTextArea"] textarea:focus,
[data-testid="stTextInput"] input:focus {{
    border-color: var(--accent) !important;
    outline: none !important;
    box-shadow: 0 0 0 3px var(--accent-sub) !important;
}}

/* Spinner */
[data-testid="stSpinner"] {{ color: var(--accent) !important; }}

/* Selectbox */
[data-testid="stSelectbox"] > div > div {{
    background: var(--surface) !important;
    border: 1px solid var(--border) !important;
    border-radius: var(--radius-sm) !important;
    color: var(--text-1) !important;
}}
</style>""",
        unsafe_allow_html=True,
    )
