"""
Offline verification for per-tenant model overrides + Exa query templates.

Proves zero behavioral change when the optional config is absent (demo tenant
resolves every agent to its hardcoded default and builds byte-identical Exa
queries), and that overrides thread through when set — including into the
strategist's ChatAnthropic construction, captured by a recorder class with no
network. Exit 1 on any failure.

    python scripts/check_config_overrides.py
"""
from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

os.environ.setdefault("BDR_DB_PATH", str(Path(tempfile.mkdtemp(prefix="bdr-check-overrides-")) / "check.db"))

from pydantic import ValidationError  # noqa: E402

from app.agents import critic, enrichment, humanizer, strategist  # noqa: E402
from app.agents.enrichment import (  # noqa: E402
    _build_exa_queries,
    _build_jobs_query,
    _build_news_query,
)
from app.agents.model_config import resolve_model  # noqa: E402
from app.agents.state import StrategyDecision  # noqa: E402
from app.services.demo_eval import build_sample_state  # noqa: E402
from app.tenants import load_tenant  # noqa: E402
from app.tenants.schema import ModelsConfig  # noqa: E402

FAILURES: list[str] = []

AGENT_DEFAULTS = {
    "enrichment": enrichment.HAIKU_MODEL,
    "strategist": strategist.MODEL,
    "humanizer": humanizer.MODEL,
    "critic": critic.MODEL,
}


def check(name: str, fn) -> None:
    try:
        fn()
    except Exception as exc:
        FAILURES.append(name)
        print(f"  FAIL  {name} — {type(exc).__name__}: {exc}")
    else:
        print(f"  ok    {name}")


TENANT = load_tenant("demo")


def check_defaults_unchanged() -> None:
    for agent, default in AGENT_DEFAULTS.items():
        assert resolve_model(TENANT, agent, default) == default, f"{agent} must default"
    assert resolve_model(None, "strategist", "fallback") == "fallback", "tenant=None must default"
    # Byte-identical query pair proves zero behavioral change for existing tenants.
    assert _build_exa_queries("Acme Corp", "SaaS", TENANT) == [
        (_build_news_query("Acme Corp", TENANT), 5),
        (_build_jobs_query("Acme Corp", TENANT), 3),
    ]


def check_model_overrides_resolve() -> None:
    t2 = TENANT.model_copy(
        update={"models": ModelsConfig(strategist="claude-test-model", enrichment="claude-test-haiku")}
    )
    assert resolve_model(t2, "strategist", strategist.MODEL) == "claude-test-model"
    assert resolve_model(t2, "enrichment", enrichment.HAIKU_MODEL) == "claude-test-haiku"
    assert resolve_model(t2, "humanizer", humanizer.MODEL) == humanizer.MODEL
    assert resolve_model(t2, "critic", critic.MODEL) == critic.MODEL
    assert resolve_model(t2, "critic", "") == "", "empty default stays empty when unset"


def check_strategist_receives_override() -> None:
    seen_models: list[str] = []

    class RecorderLLM:
        def __init__(self, *, model: str, **kwargs):
            seen_models.append(model)

        def with_structured_output(self, schema):
            class _Invoker:
                def invoke(self, messages):
                    return StrategyDecision(
                        recommended_angle="angle1",
                        angle_name=TENANT.angle_by_key("angle1").name,
                        rationale="Recorded run — no network involved in this check.",
                        cpo_hypothesis=TENANT.persona.title,
                        pain_signal="synthetic",
                    )

            return _Invoker()

    t2 = TENANT.model_copy(update={"models": ModelsConfig(strategist="claude-test-model")})
    state = build_sample_state(TENANT, {"company": "Override Co", "industry": "B2B SaaS"}, index=1)
    state["tenant"] = t2

    original_llm = strategist.ChatAnthropic
    original_key = os.environ.get("ANTHROPIC_API_KEY")
    strategist.ChatAnthropic = RecorderLLM
    os.environ["ANTHROPIC_API_KEY"] = "fake-key-for-check"
    try:
        out = strategist.run_strategist(state)
    finally:
        strategist.ChatAnthropic = original_llm
        if original_key is None:
            os.environ.pop("ANTHROPIC_API_KEY", None)
        else:
            os.environ["ANTHROPIC_API_KEY"] = original_key

    assert seen_models == ["claude-test-model"], seen_models
    assert not out.get("error"), out.get("error")


def check_exa_templates_render() -> None:
    t3 = TENANT.model_copy(
        update={
            "icp": TENANT.icp.model_copy(
                update={
                    "exa_query_templates": [
                        "{company} funding {industry}",
                        '"{company}" hiring ops',
                    ]
                }
            )
        }
    )
    queries = _build_exa_queries("Acme Corp", "SaaS", t3)
    assert queries == [
        ("Acme Corp funding SaaS", 5),
        ('"Acme Corp" hiring ops', 3),
    ], queries

    # Stray braces must pass through unrendered, never raise.
    t4 = TENANT.model_copy(
        update={"icp": TENANT.icp.model_copy(update={"exa_query_templates": ["{company} {weird} news"]})}
    )
    assert _build_exa_queries("Acme Corp", "SaaS", t4) == [("Acme Corp {weird} news", 5)]


def check_schema_compat() -> None:
    try:
        ModelsConfig(enrichmnet="typo")
    except ValidationError:
        pass
    else:
        raise AssertionError("misspelled agent key must be rejected (extra='forbid')")
    assert ModelsConfig().model_dump() == {
        "enrichment": None, "strategist": None, "humanizer": None, "critic": None
    }
    # Demo config carries neither models nor exa_query_templates keys.
    assert TENANT.models == ModelsConfig()
    assert TENANT.icp.exa_query_templates == []


def main() -> int:
    print("Config override offline checks:")
    check("defaults unchanged (models + byte-identical Exa queries)", check_defaults_unchanged)
    check("model overrides resolve per agent", check_model_overrides_resolve)
    check("strategist ChatAnthropic receives the override (no network)", check_strategist_receives_override)
    check("exa templates render {company}/{industry}, tolerate stray braces", check_exa_templates_render)
    check("schema compat (extra keys rejected, demo defaults clean)", check_schema_compat)

    print()
    if FAILURES:
        print(f"{len(FAILURES)} check(s) FAILED: {', '.join(FAILURES)}")
        return 1
    print("All config override checks passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
