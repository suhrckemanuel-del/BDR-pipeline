"""
token_accounting.py — Per-node token/cost accounting (B0, ticket #8).

Mechanism
---------
Each LLM call site attaches a :class:`UsageTracker` to its ChatAnthropic
client via the constructor-level ``callbacks`` parameter. The handler rides
along through ``with_structured_output`` wrappers and rewrite retries,
capturing ``usage_metadata`` from every ``on_llm_end`` event. Node code merges
the tracker snapshot into ``state["token_usage"]`` with the same
read-modify-write convention as ``state["degradations"]``.

Prices
------
USD per million tokens (MTok), dated. Input/output only — the Anthropic
prompt-caching discounts that B3 will introduce are NOT modeled here (the
baseline predates caching; B3 will add cache-read/cache-write rates).

Zero API-key / zero-network runs must behave exactly as before: trackers with
no captured events merge to nothing, so the state stays clean and eval CSVs
simply report blank cost columns.
"""
from __future__ import annotations

import threading
from datetime import date
from typing import Any

try:  # langchain-core is present in any runtime that constructs ChatAnthropic
    from langchain_core.callbacks import BaseCallbackHandler
except Exception:  # pragma: no cover - keeps the module importable for tests
    BaseCallbackHandler = object  # type: ignore[assignment,misc]

# USD per million tokens, verified 2026-09-19 against the Anthropic pricing
# page. Keyed by the exact model strings the agents construct clients with.
# long-context (>200K) tiers intentionally omitted — no call site uses them.
PRICES_USD_PER_MTOK: dict[str, dict[str, float]] = {
    "claude-sonnet-4-6": {"input": 3.0, "output": 15.0},
    "claude-haiku-4-5-20251001": {"input": 1.0, "output": 5.0},
    "claude-haiku-4-5": {"input": 1.0, "output": 5.0},
}

PRICE_TABLE_DATE = date(2026, 9, 19)


def price_for(model: str) -> dict[str, float]:
    """Input/output USD-per-MTok for a model, or zeros when unlisted."""
    entry = PRICES_USD_PER_MTOK.get(model)
    if entry is None:
        # Tolerate dated snapshots like claude-haiku-4-5-20251001 already
        # listed; unknown models cost nothing in the ledger but are counted.
        return {"input": 0.0, "output": 0.0}
    return entry


class UsageTracker(BaseCallbackHandler):
    """Thread-safe usage_metadata collector for one LLM call site."""

    def __init__(self, node: str, default_model: str = "") -> None:
        self.node = node
        self.default_model = default_model
        self._lock = threading.Lock()
        self.calls = 0
        self.input_tokens = 0
        self.output_tokens = 0
        self.models: set[str] = set()

    # LangChain invokes this on the client's callbacks list after each LLM run.
    def on_llm_end(self, response: Any, **kwargs: Any) -> None:
        meta = getattr(response, "usage_metadata", None) or {}
        model = str(getattr(response, "model", "") or self.default_model or "unknown")
        with self._lock:
            self.calls += 1
            self.models.add(model)
            self.input_tokens += int(meta.get("input_tokens", 0) or 0)
            self.output_tokens += int(meta.get("output_tokens", 0) or 0)

    @property
    def model(self) -> str:
        with self._lock:
            return next(iter(self.models), self.default_model or "unknown")

    def snapshot(self) -> dict[str, Any]:
        """Per-call-site ledger entry, priced at snapshot time."""
        with self._lock:
            calls = self.calls
            in_tok = self.input_tokens
            out_tok = self.output_tokens
            models = sorted(self.models) or [self.default_model or "unknown"]
        model = models[0] if len(models) == 1 else "+".join(models)
        price = price_for(model.split("+")[0])
        return {
            "node": self.node,
            "model": model,
            "calls": calls,
            "input_tokens": in_tok,
            "output_tokens": out_tok,
            "cost_usd": round(
                in_tok / 1_000_000 * price["input"]
                + out_tok / 1_000_000 * price["output"],
                6,
            ),
        }


# ---------------------------------------------------------------------------
# State accumulation (read-modify-write, mirrors the degradations convention)
# ---------------------------------------------------------------------------

def empty_usage() -> dict[str, Any]:
    return {"sites": {}, "totals": {"calls": 0, "input_tokens": 0, "output_tokens": 0, "cost_usd": 0.0}}


def merge_usage(prior: dict[str, Any] | None, *snapshots: dict[str, Any]) -> dict[str, Any]:
    """Fold one or more site snapshots into the cumulative token_usage dict.

    Totals are always recomputed from the per-site ledger, so a prior state
    carrying only ``sites`` (or nothing) stays consistent.
    """
    usage = prior or empty_usage()
    sites: dict[str, Any] = dict(usage.get("sites", {}))

    for snap in snapshots:
        key = snap["node"]
        existing = sites.get(key) or {
            "model": snap["model"], "calls": 0, "input_tokens": 0,
            "output_tokens": 0, "cost_usd": 0.0,
        }
        existing["calls"] += snap["calls"]
        existing["input_tokens"] += snap["input_tokens"]
        existing["output_tokens"] += snap["output_tokens"]
        existing["cost_usd"] = round(existing["cost_usd"] + snap["cost_usd"], 6)
        if snap["model"] not in existing["model"]:
            existing["model"] = "+".join(sorted({*existing["model"].split("+"), snap["model"]}))
        sites[key] = existing

    totals = {
        "calls": sum(s["calls"] for s in sites.values()),
        "input_tokens": sum(s["input_tokens"] for s in sites.values()),
        "output_tokens": sum(s["output_tokens"] for s in sites.values()),
        "cost_usd": round(sum(s["cost_usd"] for s in sites.values()), 6),
    }
    return {"sites": sites, "totals": totals}


def state_usage(state: dict) -> dict[str, Any]:
    """Current cumulative usage from state (never mutates)."""
    return state.get("token_usage") or empty_usage()


def capture_usage(state: dict, node: str, tracker: UsageTracker) -> dict[str, Any]:
    """RMW helper: merge a node's tracker snapshot into a partial state dict."""
    snapshot = tracker.snapshot()
    if snapshot["calls"] == 0:
        return {}  # no LLM traffic this run (e.g. no API key) — nothing to record
    return {"token_usage": merge_usage(state_usage(state), snapshot)}
