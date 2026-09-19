"""
run_store.py — Durable SQLite run store for completed pipeline states.

A5 (ticket #7). Single-file store under runs/runs.db. Schema-versioned from
day one so Phase D migrations have a hook. Stdlib sqlite3 only — no new
dependency. WAL mode for concurrent readers during Streamlit reruns.

What is persisted
-----------------
The full BDRState as JSON, minus objects that are not reproducible-from-config
or not JSON-safe:

  - tenant (TenantConfig) — stripped; tenant_id is kept because the config is
    reproducible from tenants/<id>/.
  - tenant references nested inside LLM outputs (e.g. any object graph cycles)
    are handled by a strict JSON-safe sanitizer that replaces anything it
    cannot encode with a compact repr string.

Every mutation goes through small functions so callers (main.py, eval
harnesses, future bulk runners) never touch SQL directly.
"""
from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

SCHEMA_VERSION = 1

_DEFAULT_DB = Path(__file__).resolve().parents[2] / "runs" / "runs.db"

_SCHEMA = """
CREATE TABLE IF NOT EXISTS runs (
    run_id         TEXT PRIMARY KEY,
    tenant_id      TEXT NOT NULL,
    company        TEXT NOT NULL,
    status         TEXT NOT NULL,
    schema_version INTEGER NOT NULL,
    created_at     TEXT NOT NULL,
    updated_at     TEXT NOT NULL,
    approved       INTEGER NOT NULL DEFAULT 0,
    state_json     TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_runs_queue
    ON runs (tenant_id, status, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_runs_approved
    ON runs (tenant_id, approved, created_at DESC);
"""


# ---------------------------------------------------------------------------
# Connection handling
# ---------------------------------------------------------------------------

def _connect(db_path: Path | None = None) -> sqlite3.Connection:
    path = Path(db_path) if db_path else _DEFAULT_DB
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path))
    conn.row_factory = sqlite3.Row
    try:
        conn.execute("PRAGMA journal_mode=WAL")
    except sqlite3.Error:
        pass  # WAL unavailable (e.g. exotic filesystem) — default journal is fine
    conn.executescript(_SCHEMA)
    return conn


def _slugify(value: str) -> str:
    import re
    return re.sub(r"[^a-z0-9]+", "-", (value or "").lower()).strip("-") or "run"


# ---------------------------------------------------------------------------
# State sanitization
# ---------------------------------------------------------------------------

def state_to_jsonable(state: dict) -> dict:
    """Convert a BDRState dict into JSON-safe plain Python structures.

    - TenantConfig objects are stripped (key "tenant"); tenant_id survives via
      the store columns.
    - Pydantic models serialize via model_dump (JSON-safe by construction).
    - Anything else non-JSON-safe degrades to a short repr string rather than
      failing the whole run persistence.
    """
    clean: dict = {}
    for key, value in state.items():
        if key == "tenant":
            continue
        clean[key] = _jsonable(value)
    return clean


def _jsonable(value: Any) -> Any:
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, dict):
        return {str(k): _jsonable(v) for k, v in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_jsonable(item) for item in value]
    model_dump = getattr(value, "model_dump", None)
    if callable(model_dump):
        try:
            return _jsonable(model_dump())
        except Exception:
            pass
    try:
        json.dumps(value)
        return value
    except (TypeError, ValueError):
        return repr(value)[:200]


# ---------------------------------------------------------------------------
# Store API
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class RunRecord:
    run_id: str
    tenant_id: str
    company: str
    status: str
    created_at: str
    updated_at: str
    approved: bool


def _run_id(tenant_id: str, company: str, started_at: str) -> str:
    stamp = started_at.replace(":", "").replace("-", "").replace("+", "Z")
    return f"{tenant_id}__{_slugify(company)}__{stamp}"


class RunStore:
    """Thin SQLite-backed persistence layer for pipeline runs."""

    def __init__(self, db_path: Path | str | None = None):
        self._db_path = Path(db_path) if db_path else _DEFAULT_DB
        self._conn: sqlite3.Connection | None = None

    def _connection(self) -> sqlite3.Connection:
        if self._conn is None:
            self._conn = _connect(self._db_path)
        return self._conn

    def close(self) -> None:
        if self._conn is not None:
            self._conn.close()
            self._conn = None

    def available(self) -> bool:
        try:
            self._connection().execute("SELECT 1")
            return True
        except sqlite3.Error:
            self._conn = None
            return False

    def start_run(self, state: dict) -> str:
        """Insert a running row for a fresh pipeline invocation."""
        tenant = state.get("tenant")
        tenant_id = getattr(tenant, "tenant_id", "") or "unknown"
        company = str(state.get("company") or "unknown")
        now = datetime.now(timezone.utc).isoformat(timespec="seconds")
        run_id = _run_id(tenant_id, company, now)
        self._connection().execute(
            "INSERT INTO runs (run_id, tenant_id, company, status, schema_version,"
            " created_at, updated_at, approved, state_json)"
            " VALUES (?, ?, ?, 'running', ?, ?, ?, 0, ?)",
            (run_id, tenant_id, company, SCHEMA_VERSION, now, now, "{}"),
        )
        self._connection().commit()
        return run_id

    def update_run(self, run_id: str, state: dict, status: str | None = None) -> None:
        """Write-through the current state snapshot (and optional status)."""
        payload = json.dumps(state_to_jsonable(state), ensure_ascii=False)
        now = datetime.now(timezone.utc).isoformat(timespec="seconds")
        if status:
            self._connection().execute(
                "UPDATE runs SET state_json = ?, status = ?, updated_at = ? WHERE run_id = ?",
                (payload, status, now, run_id),
            )
        else:
            self._connection().execute(
                "UPDATE runs SET state_json = ?, updated_at = ? WHERE run_id = ?",
                (payload, now, run_id),
            )
        self._connection().commit()

    def finish_run(self, run_id: str, state: dict) -> str:
        """Mark the run complete/degraded/failed based on the final state."""
        status = derive_status(state)
        self.update_run(run_id, state, status=status)
        return status

    def load_state(self, run_id: str) -> dict | None:
        row = self._connection().execute(
            "SELECT state_json, tenant_id FROM runs WHERE run_id = ?", (run_id,)
        ).fetchone()
        if row is None:
            return None
        state = json.loads(row["state_json"])
        state["tenant_id"] = row["tenant_id"]
        return state

    def recent_runs(self, tenant_id: str | None = None, limit: int = 20) -> list[RunRecord]:
        """Newest-first listing for the sidebar."""
        if tenant_id:
            rows = self._connection().execute(
                "SELECT run_id, tenant_id, company, status, created_at, updated_at, approved"
                " FROM runs WHERE tenant_id = ? ORDER BY created_at DESC LIMIT ?",
                (tenant_id, limit),
            ).fetchall()
        else:
            rows = self._connection().execute(
                "SELECT run_id, tenant_id, company, status, created_at, updated_at, approved"
                " FROM runs ORDER BY created_at DESC LIMIT ?",
                (limit,),
            ).fetchall()
        return [
            RunRecord(
                run_id=r["run_id"],
                tenant_id=r["tenant_id"],
                company=r["company"],
                status=r["status"],
                created_at=r["created_at"],
                updated_at=r["updated_at"],
                approved=bool(r["approved"]),
            )
            for r in rows
        ]

    def review_queue(self, tenant_id: str | None = None, limit: int = 50) -> list[RunRecord]:
        """Runs awaiting human approval — completed or degraded, not yet approved."""
        if tenant_id:
            rows = self._connection().execute(
                "SELECT run_id, tenant_id, company, status, created_at, updated_at, approved"
                " FROM runs WHERE tenant_id = ? AND approved = 0"
                " AND status IN ('complete', 'degraded')"
                " ORDER BY created_at DESC LIMIT ?",
                (tenant_id, limit),
            ).fetchall()
        else:
            rows = self._connection().execute(
                "SELECT run_id, tenant_id, company, status, created_at, updated_at, approved"
                " FROM runs WHERE approved = 0 AND status IN ('complete', 'degraded')"
                " ORDER BY created_at DESC LIMIT ?",
                (limit,),
            ).fetchall()
        return [
            RunRecord(
                run_id=r["run_id"],
                tenant_id=r["tenant_id"],
                company=r["company"],
                status=r["status"],
                created_at=r["created_at"],
                updated_at=r["updated_at"],
                approved=bool(r["approved"]),
            )
            for r in rows
        ]

    def set_approved(self, run_id: str, approved: bool) -> bool:
        cur = self._connection().execute(
            "UPDATE runs SET approved = ?, updated_at = ? WHERE run_id = ?",
            (1 if approved else 0, datetime.now(timezone.utc).isoformat(timespec="seconds"), run_id),
        )
        self._connection().commit()
        return cur.rowcount > 0


def derive_status(state: dict) -> str:
    """Running → terminal status from the final state contents."""
    if state.get("error"):
        return "failed"
    if state.get("degradations"):
        return "degraded"
    if state.get("critic_result") is not None:
        return "complete"
    return "failed"


# ---------------------------------------------------------------------------
# Hydration — loaded JSON back into pydantic models for rendering
# ---------------------------------------------------------------------------

def hydrate_state(state: dict) -> dict:
    """Rebuild pydantic model slices from a loaded state dict.

    Keys that fail validation are dropped (renderers skip absent slices)
    rather than failing the whole view.
    """
    from app.agents.state import CRMSyncResult, EnrichmentResult, ProspectCard

    model_map = {
        "enrichment": EnrichmentResult,
        "card": ProspectCard,
        "crm_result": CRMSyncResult,
    }
    try:
        from app.agents.critic import CriticResult
        model_map["critic_result"] = CriticResult
    except Exception:
        pass

    hydrated = dict(state)
    for key, model_cls in model_map.items():
        raw = hydrated.get(key)
        if isinstance(raw, dict):
            try:
                hydrated[key] = model_cls.model_validate(raw)
            except Exception:
                hydrated.pop(key, None)
    return hydrated
