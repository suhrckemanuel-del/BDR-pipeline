"""
model_config.py — per-tenant model override resolution.

Each agent keeps one hardcoded default model constant; tenants may override
per agent via the optional `models` config block (models.enrichment,
models.strategist, models.humanizer, models.critic). Unset/empty overrides
fall back to the agent's default, so behavior is unchanged for existing
tenants.
"""
from __future__ import annotations


def resolve_model(tenant, agent: str, default: str) -> str:
    """The model id for one agent: tenant override when set, else the default.

    Tolerates tenant=None and tenants without a `models` section (e.g. objects
    cached before a schema upgrade).
    """
    return getattr(getattr(tenant, "models", None), agent, None) or default
