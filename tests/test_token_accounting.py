"""
Unit tests for per-node token/cost accounting (B0 / ticket #8).

Mocked usage metadata only — no network, no API keys. Proves:
  - UsageTracker captures usage_metadata from on_llm_end events
  - pricing math against the dated price table
  - RMW accumulation across nodes via merge_usage
  - capture_usage is a no-op for zero-LLM runs (no behavioral change)
  - eval CSVs carry the new token/cost columns
"""
from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.services.demo_eval import METRIC_FIELDS, _token_usage_cells  # noqa: E402
from app.services import council_eval as ce  # noqa: E402
from app.services.token_accounting import (  # noqa: E402
    PRICES_USD_PER_MTOK,
    UsageTracker,
    capture_usage,
    empty_usage,
    merge_usage,
    price_for,
    state_usage,
)


class FakeResponse:
    def __init__(self, model, in_tok, out_tok):
        self.model = model
        self.usage_metadata = {"input_tokens": in_tok, "output_tokens": out_tok}


def test_tracker_captures_usage_metadata():
    tracker = UsageTracker(node="strategist", default_model="claude-sonnet-4-6")
    tracker.on_llm_end(FakeResponse("claude-sonnet-4-6", 1000, 200))
    tracker.on_llm_end(FakeResponse("claude-sonnet-4-6", 1500, 300))

    snap = tracker.snapshot()
    assert snap["calls"] == 2
    assert snap["input_tokens"] == 2500
    assert snap["output_tokens"] == 500
    assert snap["model"] == "claude-sonnet-4-6"


def test_pricing_math_sonnet():
    tracker = UsageTracker(node="strategist", default_model="claude-sonnet-4-6")
    tracker.on_llm_end(FakeResponse("claude-sonnet-4-6", 1_000_000, 100_000))
    snap = tracker.snapshot()
    # 1M input at $3/MTok + 0.1M output at $15/MTok = 3.00 + 1.50
    assert abs(snap["cost_usd"] - 4.5) < 1e-9


def test_pricing_math_haiku():
    tracker = UsageTracker(node="enrichment", default_model="claude-haiku-4-5-20251001")
    tracker.on_llm_end(FakeResponse("claude-haiku-4-5-20251001", 2_000_000, 1_000_000))
    snap = tracker.snapshot()
    # 2M in at $1 + 1M out at $5 = 7.00
    assert abs(snap["cost_usd"] - 7.0) < 1e-9


def test_unknown_model_costs_nothing_but_counts():
    tracker = UsageTracker(node="x", default_model="mystery-model")
    tracker.on_llm_end(FakeResponse("mystery-model", 1000, 100))
    snap = tracker.snapshot()
    assert snap["calls"] == 1
    assert snap["cost_usd"] == 0.0
    assert price_for("mystery-model") == {"input": 0.0, "output": 0.0}
    assert PRICES_USD_PER_MTOK["claude-sonnet-4-6"]["input"] == 3.0


def test_merge_accumulates_across_nodes():
    a = UsageTracker(node="enrichment", default_model="claude-haiku-4-5-20251001")
    a.on_llm_end(FakeResponse("claude-haiku-4-5-20251001", 100, 10))
    b = UsageTracker(node="strategist", default_model="claude-sonnet-4-6")
    b.on_llm_end(FakeResponse("claude-sonnet-4-6", 200, 20))

    usage = empty_usage()
    usage = merge_usage(usage, a.snapshot())
    usage = merge_usage(usage, b.snapshot())
    assert usage["totals"]["calls"] == 2
    assert usage["totals"]["input_tokens"] == 300
    assert usage["totals"]["output_tokens"] == 30
    assert set(usage["sites"]) == {"enrichment", "strategist"}


def test_merge_same_node_twice_sums():
    a = UsageTracker(node="critic", default_model="claude-sonnet-4-6")
    a.on_llm_end(FakeResponse("claude-sonnet-4-6", 100, 10))
    a.on_llm_end(FakeResponse("claude-sonnet-4-6", 100, 10))

    usage = merge_usage(None, a.snapshot())
    usage = merge_usage(usage, a.snapshot())
    assert usage["sites"]["critic"]["calls"] == 4
    assert usage["sites"]["critic"]["input_tokens"] == 400


def test_capture_usage_noop_when_no_traffic():
    tracker = UsageTracker(node="humanizer", default_model="claude-sonnet-4-6")
    state = {"token_usage": empty_usage()}
    out = capture_usage(state, "humanizer", tracker)
    assert out == {}  # zero-LLM runs leave the update dict empty


def test_capture_usage_merges_prior_state():
    tracker = UsageTracker(node="critic", default_model="claude-sonnet-4-6")
    tracker.on_llm_end(FakeResponse("claude-sonnet-4-6", 500, 50))
    state = {"token_usage": {"sites": {"strategist": {"model": "claude-sonnet-4-6", "calls": 1, "input_tokens": 100, "output_tokens": 10, "cost_usd": 0.0018}}}}
    out = capture_usage(state, "critic", tracker)
    merged = out["token_usage"]
    assert set(merged["sites"]) == {"strategist", "critic"}
    assert merged["totals"]["calls"] == 2


def test_state_usage_defaults():
    assert state_usage({}) == empty_usage()


def test_demo_eval_csv_has_token_columns():
    for field in ("llm_calls", "input_tokens", "output_tokens", "cost_usd"):
        assert field in METRIC_FIELDS


def test_token_usage_cells_blank_and_populated():
    assert _token_usage_cells({}) == {
        "llm_calls": "", "input_tokens": "", "output_tokens": "", "cost_usd": ""
    }
    state = {"token_usage": {"totals": {"calls": 3, "input_tokens": 1000, "output_tokens": 200, "cost_usd": 0.012}}}
    cells = _token_usage_cells(state)
    assert cells["llm_calls"] == 3
    assert cells["cost_usd"] == "0.012000"


def test_council_eval_csv_has_token_columns():
    for field in ("llm_calls", "input_tokens", "output_tokens", "cost_usd"):
        assert field in ce.CSV_FIELDS


def test_tracker_survives_structured_output_wrapper_shape():
    """The callback handler is a plain object — safe to pass in client kwargs."""
    tracker = UsageTracker(node="strategist", default_model="claude-sonnet-4-6")
    assert hasattr(tracker, "on_llm_end")
    assert tracker.node == "strategist"
    # BaseCallbackHandler import fallback keeps this importable without langchain
    import app.services.token_accounting as ta
    assert ta.UsageTracker is not None
