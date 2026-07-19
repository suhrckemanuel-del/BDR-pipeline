"""
ops.py — Operational views: Batch runs, Pipeline tracker, Run history.

These views sit next to the single-run flow and are backed by the SQLite store
(app/services/store.py). They keep the layout.py convention: pure rendering,
no workflow orchestration hidden in components.

  Batch    — run the pipeline over the tenant prospect list (or an uploaded
             CSV) with live progress; every run is eval-gated and persisted.
  Tracker  — the prospect funnel (researched → queued → sent → replied →
             meeting) with inline status editing and per-angle reply rates.
  History  — persisted past runs; any run can be re-opened and re-rendered
             with the full result panel.
"""
from __future__ import annotations

import io
from typing import Optional

import pandas as pd
import streamlit as st

from app.services import store
from app.services.batch_runner import (
    MANUAL_MINUTES_PER_PROSPECT,
    batch_summary,
    run_batch,
)
from app.services.demo_eval import load_prospects
from app.services.pipeline_evals import EvalReport, evaluate_state
from app.tenants.schema import TenantConfig
from app.ui import components as C

VIEWS = ("Run pipeline", "Batch runs", "Pipeline tracker", "Run history")


# ---------------------------------------------------------------------------
# Shared: workspace nav + eval panel
# ---------------------------------------------------------------------------
def render_workspace_nav() -> str:
    """Sidebar workspace switcher. Returns the selected view name."""
    with st.sidebar:
        view = st.radio(
            "Workspace",
            options=list(VIEWS),
            key="ui_workspace",
            label_visibility="collapsed",
        )
        st.divider()
    return str(view)


def render_ops_sidebar(tenant: TenantConfig, available_tenants: list[str], pin_locked: bool) -> str:
    """Minimal sidebar for ops views: brand + tenant switcher."""
    with st.sidebar:
        C.brand_block(
            icon=tenant.brand.icon,
            name=tenant.brand.name,
            tagline=tenant.brand.tagline or "Outreach Pipeline",
            show_demo_chip=(tenant.tenant_id == "demo"),
        )
        selection = st.selectbox(
            "Tenant",
            options=available_tenants,
            index=available_tenants.index(tenant.tenant_id),
            disabled=pin_locked,
            label_visibility="collapsed",
        )
    return str(selection)


def render_eval_panel(report: EvalReport, title: str = "Quality gates (eval loop)") -> None:
    """Compact pass/fail checklist for one run's eval report."""
    icon = "✅" if report.passed else "🛑"
    with st.expander(f"{icon} {title} — {report.summary_line()}", expanded=not report.passed):
        for check in report.checks:
            if check.passed:
                st.markdown(f"- ✅ `{check.name}`")
            elif check.severity == "fail":
                st.markdown(f"- 🛑 `{check.name}` — {check.detail or 'failed'}")
            else:
                st.markdown(f"- ⚠️ `{check.name}` — {check.detail or 'warning'}")
        if not report.passed:
            st.caption("Blocking gates failed — this prospect should not be queued for sending as-is.")


# ---------------------------------------------------------------------------
# Batch view
# ---------------------------------------------------------------------------
def render_batch_view(tenant: TenantConfig) -> None:
    C.header_block(f"{tenant.brand.name} — Batch runs", industry="", tier_label="")
    st.caption(
        "Run the full pipeline over a prospect list. Every run is scored by the eval gates "
        "and persisted — results land in the Pipeline tracker and Run history."
    )

    tenant_rows = load_prospects(tenant)
    source = st.radio(
        "Prospect source",
        options=["Tenant prospects.csv", "Upload CSV"],
        horizontal=True,
    )
    prospects: Optional[list[dict[str, str]]] = None
    if source == "Upload CSV":
        uploaded = st.file_uploader("CSV with at least a `company` column", type=["csv"])
        if uploaded is not None:
            try:
                df = pd.read_csv(io.BytesIO(uploaded.getvalue()), dtype=str).fillna("")
                prospects = [
                    {str(k): str(v).strip() for k, v in row.items()}
                    for row in df.to_dict(orient="records")
                    if str(row.get("company", "")).strip()
                ]
                st.caption(f"{len(prospects)} prospect rows loaded.")
            except Exception as exc:
                st.error(f"Could not parse CSV: {exc}")
    else:
        prospects = tenant_rows
        st.caption(f"{len(tenant_rows)} prospects in `tenants/{tenant.tenant_id}/data/prospects.csv`.")

    col_mode, col_limit, col_notion = st.columns([2, 2, 2])
    with col_mode:
        mode_label = st.selectbox(
            "Mode",
            options=["Offline sample (no API keys)", "Live APIs"],
            help="Offline sample builds deterministic fixture states — use it to demo batch mode without keys.",
        )
        mode = "live" if mode_label == "Live APIs" else "sample"
    with col_limit:
        max_rows = len(prospects or []) or 1
        limit = st.number_input("Max prospects", min_value=1, max_value=max(max_rows, 1), value=min(max_rows, 25))
    with col_notion:
        _provider_label = {"notion": "Notion", "hubspot": "HubSpot"}.get(tenant.crm.provider, "CRM")
        sync_to_notion = st.checkbox(
            f"Sync to {_provider_label}",
            value=False,
            disabled=not tenant.crm.enabled or tenant.crm.provider == "none" or mode != "live",
            help="Live mode only; crm.enabled must be true and crm.provider set in tenant config.",
        )

    if st.button("▶  Run batch", type="primary", disabled=not prospects):
        progress = st.progress(0.0, text="Starting batch…")

        def on_progress(index: int, total: int, company: str) -> None:
            progress.progress((index - 1) / total, text=f"[{index}/{total}] {company}")

        with st.spinner("Running batch…"):
            results = run_batch(
                tenant,
                prospects,
                mode=mode,
                limit=int(limit),
                sync_to_notion=sync_to_notion,
                on_progress=on_progress,
            )
        progress.progress(1.0, text="Batch complete")
        st.session_state["last_batch_results"] = results
        st.session_state["last_batch_tenant"] = tenant.tenant_id

    results = st.session_state.get("last_batch_results")
    if not results or st.session_state.get("last_batch_tenant") != tenant.tenant_id:
        _render_lifetime_stats(tenant)
        return

    summary = batch_summary(results)
    kpis = [
        ("Prospects", str(summary["total"]), ""),
        ("Completed", str(summary["completed"]), "good" if summary["completed"] == summary["total"] else "warn"),
        ("Eval passed", str(summary["eval_passed"]), "good" if summary["eval_passed"] else "warn"),
        ("Avg critic", f"{summary['avg_critic']}/5" if summary["avg_critic"] is not None else "—", ""),
        ("Pipeline time", f"{summary['pipeline_minutes']} min", ""),
        ("Manual est.", f"~{summary['est_manual_minutes']} min", "good"),
    ]
    C.kpi_strip(kpis)
    st.caption(
        f"Manual estimate assumes ~{MANUAL_MINUTES_PER_PROSPECT} min of research + drafting per prospect — "
        "a stated benchmark assumption, not a measured claim."
    )

    table = pd.DataFrame(
        [
            {
                "Company": r.company,
                "Industry": r.industry,
                "Score": r.account_score,
                "Priority": (r.priority_label or "").replace("_", " ").title(),
                "Critic": r.critic_score,
                "Angle": r.recommended_angle,
                "Eval": "✅ pass" if r.eval_report.passed else "🛑 fail",
                "Issues": r.error or r.eval_report.failure_names(),
                "Run ID": r.run_id,
            }
            for r in results
        ]
    )
    st.dataframe(table, use_container_width=True, hide_index=True)

    failing = [r for r in results if not r.eval_report.passed]
    if failing:
        st.warning(
            f"{len(failing)} run(s) failed blocking eval gates and should not be queued for sending. "
            "Open them in Run history for the full picture."
        )
    for r in failing:
        render_eval_panel(r.eval_report, title=f"{r.company} — failed gates")


def _render_lifetime_stats(tenant: TenantConfig) -> None:
    stats = store.run_stats(tenant.tenant_id)
    if not stats["total"]:
        return
    C.section_title("All-time (this tenant)")
    C.kpi_strip(
        [
            ("Runs", str(stats["total"]), ""),
            ("Completed", str(stats["completed"]), ""),
            ("Eval passed", str(stats["eval_passed"]), ""),
            ("Avg critic", f"{stats['avg_critic']}/5" if stats["avg_critic"] is not None else "—", ""),
            ("Compute time", f"{round(stats['total_runtime'] / 60, 1)} min", ""),
        ]
    )


def _render_tracker_score_inspector(tenant: TenantConfig, visible: list[dict]) -> None:
    """Score chip + expandable component breakdown for a tracked prospect."""
    with_runs = [p for p in visible if p.get("last_run_id")]
    if not with_runs:
        return
    C.section_title("Score details")
    choice = st.selectbox(
        "Prospect",
        options=["—"] + [p["company"] for p in with_runs],
        key="tracker_score_prospect",
        label_visibility="collapsed",
    )
    if choice == "—":
        return
    prospect = next(p for p in with_runs if p["company"] == choice)
    run = store.get_run(int(prospect["last_run_id"]))
    if not run:
        st.caption("Latest run for this prospect is no longer in the store.")
        return
    state = store.rehydrate_state(run)
    account_score = getattr(state.get("enrichment"), "account_score", None)
    if account_score is None:
        st.caption("No account score was recorded on the latest run.")
        return
    C.score_chip(account_score)
    C.account_score_panel(account_score, expandable=True)


# ---------------------------------------------------------------------------
# Tracker view
# ---------------------------------------------------------------------------
def render_tracker_view(tenant: TenantConfig) -> None:
    C.header_block(f"{tenant.brand.name} — Pipeline tracker", industry="", tier_label="")

    counts = store.funnel_counts(tenant.tenant_id)
    C.kpi_strip(
        [
            ("Researched", str(counts["researched"]), ""),
            ("Queued", str(counts["queued"]), ""),
            ("Sent", str(counts["sent"]), ""),
            ("Replied", str(counts["replied"]), "good" if counts["replied"] else ""),
            ("Meetings", str(counts["meeting"]), "good" if counts["meeting"] else ""),
            ("Not a fit", str(counts["not_a_fit"]), ""),
        ]
    )

    prospects = store.list_prospects(tenant.tenant_id)
    if not prospects:
        C.empty_state(
            "📋",
            "No tracked prospects yet",
            "Run the pipeline (single or batch) and completed prospects appear here with a funnel status.",
        )
        return

    status_filter = st.multiselect(
        "Filter by status",
        options=list(store.PROSPECT_STATUSES),
        default=[],
        format_func=lambda s: s.replace("_", " ").title(),
    )
    visible = [p for p in prospects if not status_filter or p["status"] in status_filter]

    df = pd.DataFrame(
        [
            {
                "Company": p["company"],
                "Status": p["status"],
                "Score": p["account_score"],
                "Priority": (p["priority_label"] or "").replace("_", " ").title(),
                "Angle": p["angle"],
                "Contact": p["contact_name"],
                "Email": p["contact_email"],
                "Eval": "✅" if p["eval_passed"] == 1 else ("🛑" if p["eval_passed"] == 0 else "—"),
                "Updated": (p["updated_at"] or "")[:16].replace("T", " "),
            }
            for p in visible
        ]
    )
    edited = st.data_editor(
        df,
        use_container_width=True,
        hide_index=True,
        disabled=[c for c in df.columns if c != "Status"],
        column_config={
            "Status": st.column_config.SelectboxColumn(
                "Status",
                options=list(store.PROSPECT_STATUSES),
                required=True,
                help="researched → queued → sent → replied → meeting",
            ),
        },
        key="tracker_editor",
    )

    # Persist any status edits.
    changed = 0
    for original, new_status in zip(visible, edited["Status"].tolist()):
        if new_status != original["status"]:
            if new_status == "queued" and original["eval_passed"] == 0:
                st.warning(
                    f"{original['company']}: last run failed blocking eval gates — "
                    "review it in Run history before queueing."
                )
            store.set_prospect_status(tenant.tenant_id, original["company"], new_status, detail="manual (tracker)")
            changed += 1
    if changed:
        st.toast(f"Updated {changed} prospect status(es).")
        st.rerun()

    _render_tracker_score_inspector(tenant, visible)

    # Export to sending tool
    C.section_title("Export to sending tool")
    col_fmt, col_go = st.columns([2, 2])
    with col_fmt:
        export_fmt = st.selectbox(
            "Format",
            options=["instantly", "smartlead"],
            format_func=str.title,
            key="tracker_export_fmt",
        )
    with col_go:
        # `exported` events log at prepare time — st.download_button has no
        # on-click callback to log against.
        if st.button("📤 Prepare export (queued + eval-passed)"):
            from app.services.sequence_export import export_sequences

            st.session_state["tracker_export"] = export_sequences(tenant.tenant_id, export_fmt)
    export_result = st.session_state.get("tracker_export")
    if export_result:
        if export_result.exported:
            st.success(f"{len(export_result.exported)} prospect(s) ready — {export_result.fmt.title()} CSV.")
            st.download_button(
                f"Download {export_result.fmt}.csv",
                data=export_result.csv_text,
                file_name=f"{tenant.tenant_id}_{export_result.fmt}.csv",
                mime="text/csv",
            )
        else:
            st.info("No eligible prospects (need status=queued and a passing latest eval run).")
        for company, reason in export_result.skipped:
            st.caption(f"Skipped {company}: {reason}")

    # Reply loop
    C.section_title("Reply loop")
    col_btn, col_stats = st.columns([1, 2])
    with col_btn:
        if st.button("📬 Check inbox for replies"):
            from app.services.reply_tracker import check_replies

            with st.spinner("Polling Gmail inbox over IMAP…"):
                result = check_replies(tenant.tenant_id)
            for error in result.errors:
                st.warning(error)
            if result.hits:
                for hit in result.hits:
                    st.success(f"Reply from {hit.company} ({hit.contact_email}): {hit.subject!r}")
                st.rerun()
            elif not result.errors:
                st.info(f"No replies found across {result.checked_addresses} contacted prospect(s).")
        st.caption("Or run `python scripts/check_replies.py` on a schedule.")
    with col_stats:
        stats = store.angle_reply_stats(tenant.tenant_id)
        if stats:
            st.dataframe(
                pd.DataFrame(
                    [
                        {
                            "Angle": row["angle"],
                            "Contacted": row["contacted"],
                            "Replied": row["replied"],
                            "Reply rate": f"{row['reply_rate'] * 100:.0f}%",
                        }
                        for row in stats
                    ]
                ),
                use_container_width=True,
                hide_index=True,
            )
        else:
            st.caption("Reply rates by angle appear once prospects are marked sent/replied.")


# ---------------------------------------------------------------------------
# History view
# ---------------------------------------------------------------------------
def render_history_view(tenant: TenantConfig) -> None:
    from app.ui.layout import render_main

    C.header_block(f"{tenant.brand.name} — Run history", industry="", tier_label="")

    runs = store.list_runs(tenant.tenant_id, limit=200)
    if not runs:
        C.empty_state(
            "🗂️",
            "No persisted runs yet",
            "Every pipeline run (single or batch) is saved to SQLite and can be re-opened here.",
        )
        return

    df = pd.DataFrame(
        [
            {
                "Run": r["id"],
                "Company": r["company"],
                "When": (r["created_at"] or "")[:16].replace("T", " "),
                "Source": r["source"],
                "Mode": r["mode"],
                "Score": r["account_score"],
                "Critic": r["critic_score"],
                "Gate": (r["gate_verdict"] or "").replace("_", " ").title(),
                "Eval": "✅" if r["eval_passed"] == 1 else ("🛑" if r["eval_passed"] == 0 else "—"),
                "Runtime (s)": r["runtime_seconds"],
                "Error": r["error"],
            }
            for r in runs
        ]
    )
    st.dataframe(df, use_container_width=True, hide_index=True)

    options = {f"#{r['id']} — {r['company']} ({(r['created_at'] or '')[:16]})": r["id"] for r in runs}
    choice = st.selectbox("Open a run", options=["—"] + list(options))
    if choice == "—":
        return

    run = store.get_run(options[choice])
    if not run:
        st.error("Run not found.")
        return
    if run["error"]:
        st.error(f"This run failed: {run['error']}")
        return

    state = store.rehydrate_state(run)
    if run["eval_failures"]:
        st.warning(f"Blocking eval failures on this run: {run['eval_failures']}")
    render_eval_panel(evaluate_state({**state, "tenant": tenant}, strict=(run["mode"] == "live")))
    render_main(tenant, state)
