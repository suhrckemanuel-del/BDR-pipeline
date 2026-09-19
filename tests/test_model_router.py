"""
Tests for B2 (ticket #10): per-node model routes + per-tenant overrides.

Covers:
  - defaults reproduce the pre-B2 hardcoded constants exactly
  - route_for resolution and unknown-node errors
  - openai-compatible client construction against a mocked endpoint module
  - check_tenant's model-route validation catches bad shapes
  - extra="forbid" rejects unknown model-map keys
  - all-Anthropic maps route to the same clients as before (byte-identical
    model ids, temperatures, max_tokens at the call sites are unchanged code)

No network, no API keys.
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import pytest  # noqa: E402

from app.services.model_router import DEFAULT_ROUTES, build_client  # noqa: E402
from app.tenants import load_tenant  # noqa: E402
from app.tenants.schema import ModelRoute, ModelRoutingConfig  # noqa: E402


def _tenant():
    return load_tenant("demo")


# ---------------------------------------------------------------------------
# Defaults — byte-identical routing to pre-B2
# ---------------------------------------------------------------------------

def test_default_routes_match_pre_b2_constants():
    assert DEFAULT_ROUTES == {
        "research_summary": "claude-haiku-4-5-20251001",
        "icp": "claude-haiku-4-5-20251001",
        "strategist": "claude-sonnet-4-6",
        "observations": "claude-sonnet-4-6",
        "rewriter": "claude-sonnet-4-6",
        "gate": "claude-sonnet-4-6",
    }


def test_tenant_without_models_key_gets_defaults():
    tenant = _tenant()
    assert tenant.models.route_for("strategist").model == "claude-sonnet-4-6"
    assert tenant.models.route_for("icp").model == "claude-haiku-4-5-20251001"
    assert tenant.models.route_for("research_summary").provider == "anthropic"


def test_route_for_injects_node_name():
    route = _tenant().models.route_for("gate")
    assert route.node == "gate"


def test_route_for_unknown_node_raises():
    with pytest.raises(KeyError):
        _tenant().models.route_for("does_not_exist")


# ---------------------------------------------------------------------------
# Anthropic client construction
# ---------------------------------------------------------------------------

def test_anthropic_client_builds_with_default_shape():
    from langchain_anthropic import ChatAnthropic

    route = _tenant().models.route_for("strategist")
    client = build_client(route, api_key="test-key", max_tokens=800, temperature=0.3)
    assert isinstance(client, ChatAnthropic)
    assert client.model == "claude-sonnet-4-6"
    assert client.max_tokens == 800


# ---------------------------------------------------------------------------
# openai-compatible provider
# ---------------------------------------------------------------------------

def test_openai_compatible_requires_base_url():
    route = ModelRoute(node="icp", provider="openai-compatible", model="glm-4-flash")
    with pytest.raises(ValueError, match="base_url"):
        build_client(route, api_key="k")


def test_openai_compatible_requires_env_key(monkeypatch):
    route = ModelRoute(
        node="icp", provider="openai-compatible", model="glm-4-flash",
        base_url="https://example.invalid/v4", api_key_env="MISSING_KEY_ENV",
    )
    monkeypatch.delenv("MISSING_KEY_ENV", raising=False)
    with pytest.raises(ValueError, match="MISSING_KEY_ENV"):
        build_client(route, api_key="unused")


def test_openai_compatible_builds_chat_openai(monkeypatch):
    """Mock the optional dependency to prove the wiring shape end-to-end."""
    captured = {}

    class FakeChatOpenAI:
        def __init__(self, **kwargs):
            captured.update(kwargs)

        def with_structured_output(self, schema):
            captured["schema"] = schema
            return self

        def invoke(self, *a, **kw):
            return "ok"

    fake_module = type(sys)("langchain_openai")
    fake_module.ChatOpenAI = FakeChatOpenAI
    monkeypatch.setitem(sys.modules, "langchain_openai", fake_module)

    route = ModelRoute(
        node="icp", provider="openai-compatible", model="glm-4-flash",
        base_url="https://example.invalid/v4", api_key_env="FAKE_GLM_KEY",
    )
    monkeypatch.setenv("FAKE_GLM_KEY", "glk-123")

    from pydantic import BaseModel as _BaseModel

    class Schema(_BaseModel):
        tier: int

    client = build_client(route, api_key="unused", max_tokens=300, temperature=0.0)
    structured = client.with_structured_output(Schema)
    structured.invoke([])

    assert captured["model"] == "glm-4-flash"
    assert captured["base_url"] == "https://example.invalid/v4"
    assert captured["api_key"] == "glk-123"
    assert captured["max_tokens"] == 300
    assert captured["schema"] is Schema


def test_openai_compatible_missing_package_gives_clear_error(monkeypatch):
    monkeypatch.setitem(sys.modules, "langchain_openai", None)  # import fails
    route = ModelRoute(
        node="icp", provider="openai-compatible", model="glm-4-flash",
        base_url="https://example.invalid/v4", api_key_env="ANY_KEY",
    )
    monkeypatch.setenv("ANY_KEY", "x")
    with pytest.raises(RuntimeError, match="langchain-openai"):
        build_client(route, api_key="unused")


# ---------------------------------------------------------------------------
# Schema strictness
# ---------------------------------------------------------------------------

def test_model_route_rejects_unknown_keys():
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        ModelRoute(model="x", bogus_key="y")


def test_model_routing_config_rejects_unknown_node():
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        ModelRoutingConfig(not_a_node=ModelRoute(model="x"))


# ---------------------------------------------------------------------------
# check_tenant validation
# ---------------------------------------------------------------------------

def test_check_tenant_catches_openai_route_without_base_url(tmp_path, monkeypatch):
    import importlib

    import scripts.check_tenant as ct

    importlib.reload(ct)

    tenant = _tenant()
    broken = tenant.model_copy(deep=True)
    bad_route = ModelRoute(provider="openai-compatible", model="glm-4-flash")
    broken_models = broken.models.model_copy(update={"icp": bad_route})
    object.__setattr__(broken, "models", broken_models)

    problems = ct._check_model_routes(broken)
    assert any("models.icp" in p and "base_url" in p for p in problems)

    ok_problems = ct._check_model_routes(_tenant())
    assert ok_problems == []


# ---------------------------------------------------------------------------
# Critical-call-site tiering is deliberate (documented reason, AC #6)
# ---------------------------------------------------------------------------

def test_observations_and_rewriter_default_to_sonnet():
    """Craft-critical nodes stay on the strong tier by default (stated reason:
    sentence-level copy quality drives the gate verdict; down-tiering them is
    a per-tenant experiment, not a default)."""
    t = _tenant().models
    assert t.observations.model == "claude-sonnet-4-6"
    assert t.rewriter.model == "claude-sonnet-4-6"
    assert t.gate.model == "claude-sonnet-4-6"
    # Cheap tier where craft does not matter:
    assert t.research_summary.model == "claude-haiku-4-5-20251001"
    assert t.icp.model == "claude-haiku-4-5-20251001"
