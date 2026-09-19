"""
app/cli.py — Agent-facing CLI for the BDR pipeline (terminal-driven operation).

Purpose
-------
Lets Claude Code, or any terminal agent, drive the pipeline with single
commands instead of clicking through Streamlit. Every subcommand reuses the
production code paths (tenant loader, workflow engine, run store, report
builder) — this file contains no business logic.

Commands
--------
    python -m app.cli check [--tenant ID]        Validate tenant config(s)
    python -m app.cli run --company NAME [...]   One end-to-end prospect run
    python -m app.cli runs [--tenant ID] [--queue] [--limit N]   List / review queue
    python -m app.cli approve RUN_ID             Approve a run in the review queue
    python -m app.cli report RUN_ID [--out P]    Write the markdown account report

Every command accepts ``--json`` for machine-readable output on stdout.

Exit codes
----------
    0  success (run: complete or degraded-but-complete)
    1  operation failed (tenant invalid, run errored, run id unknown)
    2  usage/environment problem (missing ANTHROPIC_API_KEY, no tenants, bad args)

The ``run`` command persists through the same RunStore the dashboard uses, so
runs started here appear in the sidebar's Recent runs / review queue.
"""
from __future__ import annotations

import argparse
import io
import json
import os
import sys
from pathlib import Path

# UTF-8 stdout so brand emojis survive Windows consoles.
if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

try:
    from dotenv import load_dotenv  # type: ignore
    load_dotenv(ROOT / ".env")
except ImportError:
    pass

from app.services.run_store import RunStore, derive_status, hydrate_state  # noqa: E402
from app.services.token_accounting import state_usage  # noqa: E402
from app.tenants import list_tenants, load_tenant  # noqa: E402

EXIT_OK = 0
EXIT_FAILED = 1
EXIT_ENV = 2


def _print_usage_summary(state: dict) -> None:
    """Human-readable cost/health tail for a finished run (stderr when --json)."""
    usage = state_usage(state)
    totals = usage.get("totals", {})
    print(
        f"  cost_usd: {totals.get('cost_usd', 0.0):.4f} | "
        f"llm_calls: {totals.get('calls', 0)} | "
        f"cache_hit_rate: {totals.get('cache_hit_rate', 0.0):.0%}"
    )
    degradations = state.get("degradations") or []
    print(f"  degradations: {len(degradations)}")
    for line in degradations[:5]:
        print(f"    - {line}")


def _out(args: argparse.Namespace):
    """stdout when human-readable; with --json, human lines go to stderr
    so agents reading stdout get exactly one JSON document."""
    return sys.stderr if getattr(args, "json", False) else sys.stdout


# ---------------------------------------------------------------------------
# check
# ---------------------------------------------------------------------------
def _route_problems(tenant) -> list[str]:
    """Same static route rules as scripts/check_tenant.py (no network, no secrets)."""
    problems: list[str] = []
    for node in ("research_summary", "icp", "strategist", "observations", "rewriter", "gate"):
        route = tenant.models.route_for(node)
        if not route.model:
            problems.append(f"models.{node}: empty model id")
        if route.provider == "openai-compatible" and not route.base_url:
            problems.append(f"models.{node}: openai-compatible requires base_url")
    return problems


def cmd_check(args: argparse.Namespace) -> int:
    tenant_ids = [args.tenant] if args.tenant else list_tenants()
    if not tenant_ids:
        print("No tenants found under tenants/.", file=sys.stderr)
        return EXIT_ENV

    failures: list[str] = []
    records = []
    for tenant_id in tenant_ids:
        try:
            tenant = load_tenant(tenant_id)
        except Exception as exc:
            failures.append(tenant_id)
            records.append({"tenant": tenant_id, "ok": False, "error": f"{type(exc).__name__}: {exc}"})
            print(f"{tenant_id}: FAIL — {type(exc).__name__}: {exc}", file=_out(args))
            continue
        problems: list[str] = _route_problems(tenant)
        record = {
            "tenant": tenant_id,
            "ok": not problems,
            "brand": tenant.brand.name,
            "cascade_enabled": tenant.critic.cascade_enabled,
            "council_models": tenant.critic.council_models,
            "prospects_csv_exists": tenant.prospects_csv.exists(),
            "problems": problems,
        }
        records.append(record)
        if problems:
            failures.append(tenant_id)
            print(f"{tenant_id}: FAIL — " + "; ".join(problems), file=_out(args))
        else:
            print(
                f"{tenant_id}: OK — brand={tenant.brand.name}, "
                f"cascade={'on' if tenant.critic.cascade_enabled else 'off'}, "
                f"prospects_csv={'yes' if tenant.prospects_csv.exists() else 'MISSING'}",
                file=_out(args),
            )

    if args.json:
        print(json.dumps({"ok": not failures, "tenants": records}, indent=2))
    return EXIT_FAILED if failures else EXIT_OK


# ---------------------------------------------------------------------------
# run
# ---------------------------------------------------------------------------
def cmd_run(args: argparse.Namespace) -> int:
    if not list_tenants():
        print("No tenants found under tenants/.", file=sys.stderr)
        return EXIT_ENV
    try:
        tenant = load_tenant(args.tenant)
    except Exception as exc:
        print(f"Tenant {args.tenant!r} failed to load: {type(exc).__name__}: {exc}", file=sys.stderr)
        return EXIT_FAILED

    if not os.environ.get("ANTHROPIC_API_KEY"):
        print(
            "ANTHROPIC_API_KEY is not set — a live run needs it (a run without it "
            "would produce degraded placeholder output). Export it in .env or the "
            "shell, or use the offline eval harness in scripts/run_demo_eval.py.",
            file=sys.stderr,
        )
        return EXIT_ENV

    from app.agents.workflow_engine import build_workflow, run_workflow
    from app.services.run_store import state_to_jsonable

    store = RunStore()
    run_id: str | None = None
    try:
        if store.available():
            run_id = store.start_run({
                "tenant": tenant,
                "company": args.company,
                "industry": args.industry,
            })
    except Exception:
        run_id = None  # store unavailable — run proceeds unpersisted

    print(f"Running pipeline: tenant={args.tenant} company={args.company!r} …", file=_out(args))
    try:
        workflow = build_workflow(use_checkpointer=False)
        final_state = run_workflow(
            workflow,
            company=args.company,
            industry=args.industry or "unknown",
            tenant=tenant,
            sync_to_notion=args.sync_notion,
            trigger_headline=args.trigger,
        )
    except Exception as exc:
        print(f"Pipeline crashed: {type(exc).__name__}: {exc}", file=sys.stderr)
        return EXIT_FAILED

    status = derive_status(final_state)
    if run_id:
        try:
            store.finish_run(run_id, final_state)
        except Exception:
            pass
        finally:
            store.close()

    if final_state.get("error"):
        print(f"Pipeline error: {final_state['error']}", file=sys.stderr)
        return EXIT_FAILED

    print(f"  status: {status} | run_id: {run_id or '(unpersisted)'}", file=_out(args))
    _print_usage_summary(final_state)
    if getattr(args, "json", False):
        # Human lines went to stderr; stdout carries exactly one JSON doc.
        print(json.dumps({
            "run_id": run_id,
            "status": status,
            "state": state_to_jsonable(final_state),
        }, indent=2, default=str))
    return EXIT_OK


# ---------------------------------------------------------------------------
# runs / approve
# ---------------------------------------------------------------------------
def cmd_runs(args: argparse.Namespace) -> int:
    store = RunStore()
    if not store.available():
        print("Run store unavailable (runs/runs.db).", file=sys.stderr)
        return EXIT_ENV
    try:
        records = (
            store.review_queue(tenant_id=args.tenant, limit=args.limit)
            if args.queue
            else store.recent_runs(tenant_id=args.tenant, limit=args.limit)
        )
    finally:
        store.close()

    if args.json:
        print(json.dumps([r.__dict__ for r in records], indent=2))
        return EXIT_OK

    label = "review queue" if args.queue else "recent runs"
    if not records:
        print(f"No {label}.")
        return EXIT_OK
    print(f"{len(records)} {label}:")
    for r in records:
        flag = " [approved]" if r.approved else ""
        print(f"  {r.run_id}  {r.status:<9} {r.company}  {r.updated_at}{flag}")
    return EXIT_OK


def cmd_approve(args: argparse.Namespace) -> int:
    store = RunStore()
    if not store.available():
        print("Run store unavailable (runs/runs.db).", file=sys.stderr)
        return EXIT_ENV
    try:
        changed = store.set_approved(args.run_id, True)
    finally:
        store.close()
    if not changed:
        print(f"Run {args.run_id!r} not found.", file=sys.stderr)
        return EXIT_FAILED
    print(f"Approved {args.run_id}", file=_out(args))
    if args.json:
        print(json.dumps({"run_id": args.run_id, "approved": True}))
    return EXIT_OK


# ---------------------------------------------------------------------------
# report
# ---------------------------------------------------------------------------
def cmd_report(args: argparse.Namespace) -> int:
    from app.services.report_builder import (
        build_account_report_markdown,
        build_report_filename,
    )

    store = RunStore()
    if not store.available():
        print("Run store unavailable (runs/runs.db).", file=sys.stderr)
        return EXIT_ENV
    try:
        loaded = store.load_state(args.run_id)
    finally:
        store.close()
    if not loaded:
        print(f"Run {args.run_id!r} not found in the store.", file=sys.stderr)
        return EXIT_FAILED

    state = hydrate_state(loaded)
    tenant_id = state.get("tenant_id") or args.tenant or (list_tenants() or ["demo"])[0]
    try:
        tenant = load_tenant(tenant_id)
    except Exception as exc:
        print(f"Tenant {tenant_id!r} failed to load: {type(exc).__name__}: {exc}", file=sys.stderr)
        return EXIT_FAILED

    markdown = build_account_report_markdown(state, tenant)
    out_path = Path(args.out) if args.out else ROOT / "reports" / build_report_filename(
        state.get("company") or "prospect"
    )
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(markdown, encoding="utf-8")

    print(f"Report written: {out_path}", file=_out(args))
    if args.json:
        print(json.dumps({"run_id": args.run_id, "report_path": str(out_path)}))
    return EXIT_OK


# ---------------------------------------------------------------------------
# arg parsing
# ---------------------------------------------------------------------------
def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="bdr",
        description="Agent-facing CLI for the BDR outreach pipeline.",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("check", help="Validate tenant config(s) without network or keys")
    p.add_argument("--tenant", default="", help="Tenant id (default: all tenants)")
    p.add_argument("--json", action="store_true", help="Machine-readable output")
    p.set_defaults(func=cmd_check)

    p = sub.add_parser("run", help="One end-to-end prospect run (needs ANTHROPIC_API_KEY)")
    p.add_argument("--company", required=True, help="Prospect company name")
    p.add_argument("--industry", default="unknown", help="Industry hint for research")
    p.add_argument("--tenant", default=os.environ.get("BDR_TENANT", "") or "demo")
    p.add_argument("--trigger", default="", help="Optional trigger headline (timely hook)")
    p.add_argument("--sync-notion", action="store_true", help="Push result to Notion CRM (if enabled)")
    p.add_argument("--json", action="store_true", help="Print the full final state as JSON")
    p.set_defaults(func=cmd_run)

    p = sub.add_parser("runs", help="List recent runs or the review queue")
    p.add_argument("--tenant", default="", help="Filter by tenant id")
    p.add_argument("--queue", action="store_true", help="Show the review queue instead of recent runs")
    p.add_argument("--limit", type=int, default=20)
    p.add_argument("--json", action="store_true", help="Machine-readable output")
    p.set_defaults(func=cmd_runs)

    p = sub.add_parser("approve", help="Approve a run in the review queue")
    p.add_argument("run_id")
    p.add_argument("--json", action="store_true", help="Machine-readable output")
    p.set_defaults(func=cmd_approve)

    p = sub.add_parser("report", help="Write the markdown account report for a stored run")
    p.add_argument("run_id")
    p.add_argument("--tenant", default="", help="Tenant id fallback if the run lacks one")
    p.add_argument("--out", default="", help="Output path (default: reports/<generated name>)")
    p.add_argument("--json", action="store_true", help="Machine-readable output")
    p.set_defaults(func=cmd_report)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
