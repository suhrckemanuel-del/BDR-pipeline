"""
model_router.py — Provider-aware per-node model resolution (B2, ticket #10).

What this enables
-----------------
Every LLM node reads its client from here instead of constructing one inline.
The model comes from the tenant's per-node map (``tenant.models``), so routing
a node through a cheaper Anthropic tier — or a free/cheap OpenAI-compatible
endpoint such as GLM Flash — becomes a config edit, not a rewrite.

Structured-output invariant
---------------------------
Both providers go through ``.with_structured_output(schema)``. No regex
parsing of LLM text anywhere — the architecture invariant holds regardless of
provider.

Defaults equal today's behavior
-------------------------------
The default map is exactly the constants that were hardcoded in the agents
(Sonnet 4.6 for strategy/craft/gate, Haiku 4.5 for cheap classification), so
an all-Anthropic map routes byte-identically to pre-B2 code.

Legal note
----------
Routing prospect data through third-party endpoints is a per-tenant legal
decision; documented in tenants/README.md next to the new config keys.
"""
from __future__ import annotations

from typing import Any, Type

from pydantic import BaseModel

# The default per-node routes. Keys must match ModelRoutingConfig's known
# nodes; values mirror the constants previously hardcoded in the agents.
DEFAULT_ROUTES: dict[str, str] = {
    "research_summary": "claude-haiku-4-5-20251001",
    "icp": "claude-haiku-4-5-20251001",
    "strategist": "claude-sonnet-4-6",
    "observations": "claude-sonnet-4-6",
    "rewriter": "claude-sonnet-4-6",
    "gate": "claude-sonnet-4-6",
}

_KNOWN_NODES = frozenset(DEFAULT_ROUTES)


def known_nodes() -> frozenset[str]:
    return _KNOWN_NODES


def build_client(
    route: "ModelRoute",
    api_key: str,
    max_tokens: int = 1024,
    temperature: float = 0.0,
    extra_kwargs: dict[str, Any] | None = None,
) -> Any:
    """Build a chat client for a resolved ModelRoute.

    Both providers return a LangChain chat model supporting
    ``.with_structured_output(...)`` and ``.invoke([...messages])``.
    """
    kwargs: dict[str, Any] = {
        "max_tokens": max_tokens,
        "temperature": temperature,
    }
    if extra_kwargs:
        kwargs.update(extra_kwargs)

    # Config-shape validation first, so misconfiguration is reported even when
    # an optional provider package is not installed.
    if route.provider not in ("anthropic", "openai-compatible"):
        raise ValueError(f"Route {route.node!r}: unknown provider {route.provider!r}")
    if not route.model:
        raise ValueError(f"Route {route.node!r}: model id is empty")
    if route.provider == "openai-compatible":
        import os

        if not route.base_url:
            raise ValueError(
                f"Route {route.node!r}: openai-compatible provider needs "
                "base_url set in the tenant's model map."
            )
        if not os.environ.get(route.api_key_env):
            raise ValueError(
                f"Route {route.node!r}: env var {route.api_key_env!r} is not set "
                "(needed as the API key for the openai-compatible endpoint)."
            )

    if route.provider == "anthropic":
        from langchain_anthropic import ChatAnthropic

        # Pre-B2 every Anthropic client sent the caching beta header; keep it
        # here so provider parity is handled in one place (callers may override).
        kwargs.setdefault(
            "extra_headers", {"anthropic-beta": "prompt-caching-2024-07-31"}
        )
        return ChatAnthropic(model=route.model, api_key=api_key, **kwargs)

    if route.provider == "openai-compatible":
        import os

        try:
            from langchain_openai import ChatOpenAI  # optional dependency
        except ImportError as exc:
            raise RuntimeError(
                "Provider 'openai-compatible' needs the optional package "
                "langchain-openai: pip install langchain-openai"
            ) from exc

        return ChatOpenAI(
            model=route.model,
            api_key=os.environ[route.api_key_env],
            base_url=route.base_url,
            **kwargs,
        )


def resolve_schema_client(
    tenant: Any,
    node: str,
    schema: Type[BaseModel],
    api_key: str,
    max_tokens: int = 1024,
    temperature: float = 0.0,
    extra_kwargs: dict[str, Any] | None = None,
):
    """Convenience: resolved client already wrapped for a structured schema."""
    route = tenant.models.route_for(node)
    client = build_client(
        route,
        api_key=api_key,
        max_tokens=max_tokens,
        temperature=temperature,
        extra_kwargs=extra_kwargs,
    )
    return client.with_structured_output(schema)
