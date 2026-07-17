"""
store.py — SQLite persistence for pipeline runs and the prospect tracker.

Replaces "results evaporate on Streamlit rerun" with a durable, file-based
store at pipeline/bdr.db (override with BDR_DB_PATH). No ORM — stdlib sqlite3
with WAL mode, one short-lived connection per call so Streamlit's threading
model stays happy.

Three tables:
  runs       — every pipeline execution: headline metrics + the full state
               serialized to JSON so past runs can be re-rendered.
  prospects  — one row per (tenant, company): the lightweight pipeline tracker
               with a status funnel (researched → queued → sent → replied →
               meeting) and the recommended angle for reply-rate stats.
  events     — append-only activity log (status changes, sends, replies).

Serialization: state["tenant"] is dropped (only tenant_id is stored) and every
Pydantic model is dumped to plain JSON. `rehydrate_state()` rebuilds the
Pydantic slices so the existing render_main() can display historical runs.
"""
from __future__ import annotations

import json
import os
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from pydantic import BaseModel

ROOT = Path(__file__).resolve().parents[2]

# Ordered status funnel for the prospect tracker.
PROSPECT_STATUSES = (
    "researched",
    "queued",
    "sent",
    "replied",
    "meeting",
    "not_a_fit",
)

_SCHEMA = """
CREATE TABLE IF NOT EXISTS runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    tenant_id TEXT NOT NULL,
    company TEXT NOT NULL,
    industry TEXT DEFAULT '',
    created_at TEXT NOT NULL,
    source TEXT DEFAULT 'ui',
    mode TEXT DEFAULT 'live',
    runtime_seconds REAL DEFAULT 0,
    icp_tier INTEGER,
    account_score INTEGER,
    priority_label TEXT DEFAULT '',
    critic_score REAL,
    gate_verdict TEXT DEFAULT '',
    recommended_angle TEXT DEFAULT '',
    eval_passed INTEGER,
    eval_failures TEXT DEFAULT '',
    error TEXT DEFAULT '',
    state_json TEXT DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_runs_tenant ON runs (tenant_id, created_at DESC);

CREATE TABLE IF NOT EXISTS prospects (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    tenant_id TEXT NOT NULL,
    company TEXT NOT NULL,
    domain TEXT DEFAULT '',
    industry TEXT DEFAULT '',
    status TEXT DEFAULT 'researched',
    angle TEXT DEFAULT '',
    contact_name TEXT DEFAULT '',
    contact_email TEXT DEFAULT '',
    last_run_id INTEGER,
    eval_passed INTEGER,
    account_score INTEGER,
    priority_label TEXT DEFAULT '',
    notes TEXT DEFAULT '',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    replied_at TEXT DEFAULT '',
    UNIQUE (tenant_id, company)
);

CREATE TABLE IF NOT EXISTS events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    tenant_id TEXT NOT NULL,
    company TEXT NOT NULL,
    event_type TEXT NOT NULL,
    detail TEXT DEFAULT '',
    angle TEXT DEFAULT '',
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_events_tenant ON events (tenant_id, created_at DESC);
"""


def db_path() -> Path:
    override = os.environ.get("BDR_DB_PATH", "").strip()
    if override:
        return Path(override)
    return ROOT / "pipeline" / "bdr.db"


def _connect() -> sqlite3.Connection:
    path = db_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path, timeout=15)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.executescript(_SCHEMA)
    return conn


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


# ---------------------------------------------------------------------------
# State (de)serialization
# ---------------------------------------------------------------------------
def _to_jsonable(value: Any) -> Any:
    if isinstance(value, BaseModel):
        return value.model_dump(mode="json")
    if isinstance(value, dict):
        return {str(k): _to_jsonable(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_to_jsonable(v) for v in value]
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    return str(value)


def serialize_state(state: dict) -> str:
    """Serialize a BDRState to JSON, dropping the (re-loadable) tenant config."""
    payload = {k: _to_jsonable(v) for k, v in state.items() if k != "tenant"}
    return json.dumps(payload, ensure_ascii=False)


def rehydrate_state(row: sqlite3.Row | dict) -> dict:
    """
    Rebuild a renderable state dict from a stored run row.

    Pydantic slices are re-validated so render_main() gets real objects.
    Any slice that fails validation is dropped rather than crashing the view.
    """
    from app.agents.critic import CriticResult
    from app.agents.state import (
        CRMSyncResult,
        EnrichmentResult,
        ProspectCard,
        StrategyDecision,
    )

    raw = dict(row)
    try:
        state: dict = json.loads(raw.get("state_json") or "{}")
    except json.JSONDecodeError:
        state = {}

    validators = {
        "enrichment": EnrichmentResult,
        "strategy": StrategyDecision,
        "card": ProspectCard,
        "critic_result": CriticResult,
        "crm_result": CRMSyncResult,
    }
    for key, model in validators.items():
        value = state.get(key)
        if isinstance(value, dict):
            try:
                state[key] = model.model_validate(value)
            except Exception:
                state.pop(key, None)

    state.setdefault("company", raw.get("company", ""))
    state.setdefault("industry", raw.get("industry", ""))
    return state


# ---------------------------------------------------------------------------
# Metric extraction helpers
# ---------------------------------------------------------------------------
def _get(obj: Any, attr: str, default: Any = None) -> Any:
    if obj is None:
        return default
    if isinstance(obj, dict):
        return obj.get(attr, default)
    return getattr(obj, attr, default)


def _run_metrics(state: dict) -> dict:
    enrichment = state.get("enrichment")
    strategy = state.get("strategy")
    critic = state.get("critic_result")
    icp = _get(enrichment, "icp")
    account_score = _get(enrichment, "account_score")
    gate = _get(critic, "quality_gate")
    return {
        "icp_tier": _get(icp, "tier"),
        "account_score": _get(account_score, "overall_score"),
        "priority_label": _get(account_score, "priority_label", "") or "",
        "critic_score": _get(critic, "overall_quality"),
        "gate_verdict": _get(gate, "verdict", "") or "",
        "recommended_angle": _get(strategy, "recommended_angle", "") or "",
    }


# ---------------------------------------------------------------------------
# Runs
# ---------------------------------------------------------------------------
def record_run(
    state: dict,
    *,
    source: str = "ui",
    mode: str = "live",
    runtime_seconds: float = 0.0,
    eval_passed: Optional[bool] = None,
    eval_failures: str = "",
) -> int:
    """Persist a completed (or failed) pipeline run. Returns the run id."""
    tenant = state.get("tenant")
    tenant_id = _get(tenant, "tenant_id", "") or ""
    metrics = _run_metrics(state)
    with _connect() as conn:
        cur = conn.execute(
            """
            INSERT INTO runs (
                tenant_id, company, industry, created_at, source, mode,
                runtime_seconds, icp_tier, account_score, priority_label,
                critic_score, gate_verdict, recommended_angle,
                eval_passed, eval_failures, error, state_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                tenant_id,
                state.get("company", ""),
                state.get("industry", ""),
                _now(),
                source,
                mode,
                round(float(runtime_seconds), 2),
                metrics["icp_tier"],
                metrics["account_score"],
                metrics["priority_label"],
                metrics["critic_score"],
                metrics["gate_verdict"],
                metrics["recommended_angle"],
                None if eval_passed is None else int(eval_passed),
                eval_failures,
                str(state.get("error") or ""),
                serialize_state(state),
            ),
        )
        return int(cur.lastrowid)


def list_runs(tenant_id: str, limit: int = 100) -> list[dict]:
    with _connect() as conn:
        rows = conn.execute(
            """
            SELECT id, tenant_id, company, industry, created_at, source, mode,
                   runtime_seconds, icp_tier, account_score, priority_label,
                   critic_score, gate_verdict, recommended_angle,
                   eval_passed, eval_failures, error
            FROM runs WHERE tenant_id = ?
            ORDER BY created_at DESC, id DESC LIMIT ?
            """,
            (tenant_id, limit),
        ).fetchall()
    return [dict(r) for r in rows]


def get_run(run_id: int) -> Optional[dict]:
    with _connect() as conn:
        row = conn.execute("SELECT * FROM runs WHERE id = ?", (run_id,)).fetchone()
    return dict(row) if row else None


# ---------------------------------------------------------------------------
# Prospects (pipeline tracker)
# ---------------------------------------------------------------------------
def upsert_prospect_from_state(
    state: dict,
    run_id: int,
    *,
    eval_passed: Optional[bool] = None,
) -> None:
    """Create or refresh the tracker row for the company in this run.

    A new company starts at 'researched'. An existing row keeps its funnel
    status — reruns refresh scores and contacts without resetting progress.
    """
    tenant = state.get("tenant")
    tenant_id = _get(tenant, "tenant_id", "") or ""
    company = (state.get("company") or "").strip()
    if not tenant_id or not company:
        return

    enrichment = state.get("enrichment")
    metrics = _run_metrics(state)
    contacts = _get(enrichment, "contacts") or []
    top = contacts[0] if contacts else None
    now = _now()

    with _connect() as conn:
        conn.execute(
            """
            INSERT INTO prospects (
                tenant_id, company, domain, industry, status, angle,
                contact_name, contact_email, last_run_id, eval_passed,
                account_score, priority_label, created_at, updated_at
            ) VALUES (?, ?, ?, ?, 'researched', ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT (tenant_id, company) DO UPDATE SET
                domain = excluded.domain,
                industry = excluded.industry,
                angle = excluded.angle,
                contact_name = CASE WHEN excluded.contact_name != ''
                                    THEN excluded.contact_name ELSE prospects.contact_name END,
                contact_email = CASE WHEN excluded.contact_email != ''
                                     THEN excluded.contact_email ELSE prospects.contact_email END,
                last_run_id = excluded.last_run_id,
                eval_passed = excluded.eval_passed,
                account_score = excluded.account_score,
                priority_label = excluded.priority_label,
                updated_at = excluded.updated_at
            """,
            (
                tenant_id,
                company,
                _get(enrichment, "domain", "") or "",
                state.get("industry", "") or "",
                metrics["recommended_angle"],
                _get(top, "name", "") or "",
                _get(top, "email", "") or "",
                run_id,
                None if eval_passed is None else int(eval_passed),
                metrics["account_score"],
                metrics["priority_label"],
                now,
                now,
            ),
        )


def list_prospects(tenant_id: str, status: str = "") -> list[dict]:
    query = "SELECT * FROM prospects WHERE tenant_id = ?"
    params: list = [tenant_id]
    if status:
        query += " AND status = ?"
        params.append(status)
    query += " ORDER BY updated_at DESC"
    with _connect() as conn:
        rows = conn.execute(query, params).fetchall()
    return [dict(r) for r in rows]


def set_prospect_status(tenant_id: str, company: str, status: str, detail: str = "") -> bool:
    if status not in PROSPECT_STATUSES:
        raise ValueError(f"Unknown status {status!r}. Valid: {PROSPECT_STATUSES}")
    now = _now()
    with _connect() as conn:
        row = conn.execute(
            "SELECT status, angle FROM prospects WHERE tenant_id = ? AND company = ?",
            (tenant_id, company),
        ).fetchone()
        if not row:
            return False
        if row["status"] == status:
            return True
        replied_at = now if status == "replied" else ""
        conn.execute(
            """
            UPDATE prospects SET status = ?, updated_at = ?,
                   replied_at = CASE WHEN ? != '' THEN ? ELSE replied_at END
            WHERE tenant_id = ? AND company = ?
            """,
            (status, now, replied_at, replied_at, tenant_id, company),
        )
        conn.execute(
            """
            INSERT INTO events (tenant_id, company, event_type, detail, angle, created_at)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (tenant_id, company, f"status:{status}", detail, row["angle"] or "", now),
        )
    return True


def log_event(tenant_id: str, company: str, event_type: str, detail: str = "", angle: str = "") -> None:
    with _connect() as conn:
        conn.execute(
            """
            INSERT INTO events (tenant_id, company, event_type, detail, angle, created_at)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (tenant_id, company, event_type, detail, angle, _now()),
        )


def list_events(tenant_id: str, limit: int = 200) -> list[dict]:
    with _connect() as conn:
        rows = conn.execute(
            "SELECT * FROM events WHERE tenant_id = ? ORDER BY created_at DESC, id DESC LIMIT ?",
            (tenant_id, limit),
        ).fetchall()
    return [dict(r) for r in rows]


# ---------------------------------------------------------------------------
# Aggregates for the tracker dashboard
# ---------------------------------------------------------------------------
def funnel_counts(tenant_id: str) -> dict[str, int]:
    with _connect() as conn:
        rows = conn.execute(
            "SELECT status, COUNT(*) AS n FROM prospects WHERE tenant_id = ? GROUP BY status",
            (tenant_id,),
        ).fetchall()
    counts = {status: 0 for status in PROSPECT_STATUSES}
    for row in rows:
        counts[row["status"]] = row["n"]
    return counts


def angle_reply_stats(tenant_id: str) -> list[dict]:
    """Per-angle counts of prospects contacted vs replied — the reply-rate loop."""
    with _connect() as conn:
        rows = conn.execute(
            """
            SELECT angle,
                   SUM(CASE WHEN status IN ('sent', 'replied', 'meeting') THEN 1 ELSE 0 END) AS contacted,
                   SUM(CASE WHEN status IN ('replied', 'meeting') THEN 1 ELSE 0 END) AS replied
            FROM prospects
            WHERE tenant_id = ? AND angle != ''
            GROUP BY angle ORDER BY angle
            """,
            (tenant_id,),
        ).fetchall()
    stats = []
    for row in rows:
        contacted = row["contacted"] or 0
        replied = row["replied"] or 0
        stats.append(
            {
                "angle": row["angle"],
                "contacted": contacted,
                "replied": replied,
                "reply_rate": round(replied / contacted, 3) if contacted else 0.0,
            }
        )
    return stats


def run_stats(tenant_id: str) -> dict:
    """Headline aggregates for the ROI strip."""
    with _connect() as conn:
        row = conn.execute(
            """
            SELECT COUNT(*) AS total,
                   SUM(CASE WHEN error = '' THEN 1 ELSE 0 END) AS completed,
                   SUM(CASE WHEN eval_passed = 1 THEN 1 ELSE 0 END) AS eval_passed,
                   AVG(CASE WHEN critic_score IS NOT NULL THEN critic_score END) AS avg_critic,
                   SUM(runtime_seconds) AS total_runtime
            FROM runs WHERE tenant_id = ?
            """,
            (tenant_id,),
        ).fetchone()
    return {
        "total": row["total"] or 0,
        "completed": row["completed"] or 0,
        "eval_passed": row["eval_passed"] or 0,
        "avg_critic": round(row["avg_critic"], 2) if row["avg_critic"] is not None else None,
        "total_runtime": round(row["total_runtime"] or 0.0, 1),
    }
