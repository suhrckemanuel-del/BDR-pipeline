"""
Tests for B3 (ticket #12): prompt caching done right.

Spike-proven fact these tests lock in: on langchain-anthropic==1.4.1 /
anthropic==0.96.0, cache_control only reaches the request payload in the
CONTENT-BLOCK form. The additional_kwargs form used pre-B3 was silently
dropped — these tests prevent that regression.

No network, no API keys.
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import pytest  # noqa: E402

from app.services.demo_eval import METRIC_FIELDS, _token_usage_cells  # noqa: E402
from app.services.model_router import cached_system_message  # noqa: E402
from app.services.token_accounting import UsageTracker, merge_usage  # noqa: E402
from app.tenants import load_tenant  # noqa: E402
from app.tenants.schema import ModelRoute  # noqa: E402


# ---------------------------------------------------------------------------
# Payload-level proof (the spike, automated)
# ---------------------------------------------------------------------------

def _payload_system(message):
    from langchain_anthropic import ChatAnthropic

    llm = ChatAnthropic(model="claude-sonnet-4-6", api_key="k", max_tokens=10)
    return llm._get_request_payload([message]).get("system")


def test_cache_control_reaches_payload_in_content_block_form():
    msg = cached_system_message(
        ModelRoute(node="strategist", model="claude-sonnet-4-6"),
        "TENANT CONTEXT",
    )
    system = _payload_system(msg)
    assert isinstance(system, list), "cache_control requires the content-block form"
    assert system[0]["cache_control"] == {"type": "ephemeral"}
    assert system[0]["text"] == "TENANT CONTEXT"


def test_old_additional_kwargs_form_is_a_noop_documented():
    """Guards the spike's finding: additional_kwargs cache_control never ships."""
    from langchain_core.messages import SystemMessage

    msg = SystemMessage(
        content="TENANT CONTEXT",
        additional_kwargs={"cache_control": {"type": "ephemeral"}},
    )
    system = _payload_system(msg)
    assert system == "TENANT CONTEXT"  # plain string — marker dropped


def test_non_anthropic_route_gets_plain_message_without_marker():
    msg = cached_system_message(
        ModelRoute(node="icp", provider="openai-compatible", model="glm-4-flash"),
        "TENANT CONTEXT",
    )
    assert msg.content == "TENANT CONTEXT"  # plain string, no blocks
    assert not isinstance(msg.content, list)


def test_all_call_site_routes_default_to_anthropic():
    """Every node the agents cache is Anthropic-routed in the default map."""
    tenant = load_tenant("demo")
    for node in ("research_summary", "icp", "strategist", "observations", "rewriter", "gate"):
        assert tenant.models.route_for(node).provider == "anthropic", node


# ---------------------------------------------------------------------------
# Cache-aware accounting
# ---------------------------------------------------------------------------

def test_tracker_captures_cache_fields():
    tracker = UsageTracker(node="strategist", default_model="claude-sonnet-4-6")

    class Resp:
        model = "claude-sonnet-4-6"
        usage_metadata = {
            "input_tokens": 4000,
            "output_tokens": 200,
            "output_token_details": {"cache_read": 3500, "cache_creation": 400},
        }

    tracker.on_llm_end(Resp())
    snap = tracker.snapshot()
    assert snap["cache_read_tokens"] == 3500
    assert snap["cache_creation_tokens"] == 400
    # uncached input = 4000 - 3500 - 400 = 100
    expected = (
        100 / 1e6 * 3.0          # uncached input
        + 3500 / 1e6 * 3.0 * 0.1  # cache reads at 10%
        + 400 / 1e6 * 3.0 * 1.25  # cache writes at 125%
        + 200 / 1e6 * 15.0        # output
    )
    assert abs(snap["cost_usd"] - round(expected, 6)) < 1e-9
    assert snap["cache_hit_rate"] == round(3500 / 4000, 4)


def test_tracker_without_cache_fields_still_prices():
    """Pre-cache responses (and non-Anthropic providers) keep working."""
    tracker = UsageTracker(node="enrichment", default_model="claude-haiku-4-5-20251001")

    class Resp:
        model = "claude-haiku-4-5-20251001"
        usage_metadata = {"input_tokens": 1000, "output_tokens": 100}

    tracker.on_llm_end(Resp())
    snap = tracker.snapshot()
    assert snap["cache_read_tokens"] == 0
    assert abs(snap["cost_usd"] - (1000 / 1e6 * 1.0 + 100 / 1e6 * 5.0)) < 1e-9


def test_merge_totals_carry_cache_hit_rate():
    tracker = UsageTracker(node="strategist", default_model="claude-sonnet-4-6")

    class Resp:
        model = "claude-sonnet-4-6"
        usage_metadata = {
            "input_tokens": 1000,
            "output_tokens": 10,
            "output_token_details": {"cache_read": 800},
        }

    tracker.on_llm_end(Resp())
    merged = merge_usage(None, tracker.snapshot())
    assert merged["totals"]["cache_read_tokens"] == 800
    assert merged["totals"]["cache_hit_rate"] == 0.8


def test_eval_csv_has_cache_hit_rate_column():
    assert "cache_hit_rate" in METRIC_FIELDS
    cells = _token_usage_cells(
        {"token_usage": {"totals": {"calls": 1, "input_tokens": 1000,
                                    "output_tokens": 10, "cost_usd": 0.01,
                                    "cache_hit_rate": 0.8}}}
    )
    assert cells["cache_hit_rate"] == "0.8000"


def test_full_council_run_message_shapes():
    """Smoke: cached_system_message builds for every default route + node."""
    tenant = load_tenant("demo")
    for node in ("research_summary", "icp", "strategist", "observations", "rewriter", "gate"):
        msg = cached_system_message(tenant.models.route_for(node), "X")
        assert msg is not None
