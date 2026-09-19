"""
Tests for B1 (ticket #9): the strategist prompt must not carry the
cold-email craft prompt.

The craft rules govern sentence-level email writing. The strategist only
picks an angle — the craft prompt there is pure input-token waste (~378
tokens per run). The humanizer keeps it: that is where emails get written.

No network, no API keys.
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.agents.strategist import _build_system_prompt  # noqa: E402
from app.tenants import load_tenant  # noqa: E402


def _craft_prompt() -> str:
    from app.prompts import load_prompt

    return load_prompt("cold_email")


def test_strategist_prompt_excludes_craft_prompt():
    tenant = load_tenant("demo")
    prompt = _build_system_prompt(tenant)
    craft = _craft_prompt()
    # Neither the full block nor its distinctive header may appear.
    assert craft.strip() not in prompt
    assert "Observation craft" not in prompt


def test_strategist_prompt_keeps_angle_decision_content():
    tenant = load_tenant("demo")
    prompt = _build_system_prompt(tenant)
    assert "Pick exactly ONE angle" in prompt
    assert tenant.angles[0].name in prompt  # angle menu intact


def test_humanizer_prompt_still_carries_craft_prompt():
    """The craft prompt stays where it is actionable (email writing)."""
    from app.agents.humanizer import _build_system_prompt

    tenant = load_tenant("demo")
    prompt = _build_system_prompt(tenant)
    craft = _craft_prompt()
    assert craft.strip() in prompt


def test_strategist_prompt_is_meaningfully_shorter_than_craft_inclusive_size():
    """Guard against the craft block sneaking back in: prompt must stay lean."""
    tenant = load_tenant("demo")
    prompt = _build_system_prompt(tenant)
    craft_len = len(_craft_prompt())
    # Without the craft block the prompt is ~3.9k chars; with it ~5.4k.
    assert len(prompt) < 4_500
    assert craft_len > 1_000  # sanity: the craft block itself still exists
