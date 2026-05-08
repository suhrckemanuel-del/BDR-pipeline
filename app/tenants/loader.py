"""
loader.py — Discover and load TenantConfig objects from `tenants/<slug>/`.

Public surface:
    tenants_root() -> Path
    list_tenants() -> list[str]            # sorted slugs
    load_tenant(tenant_id: str) -> TenantConfig

The loader is the only place that knows about file layout. Agents and the UI
should never read tenant files directly — always go through TenantConfig.
"""
from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import List

import yaml

from .schema import HumanizerCopy, TenantConfig


def tenants_root() -> Path:
    """Absolute path to the repo-level `tenants/` directory."""
    # app/tenants/loader.py -> app/ -> repo root
    return Path(__file__).resolve().parents[2] / "tenants"


def list_tenants() -> List[str]:
    """Return sorted list of available tenant slugs (folders under tenants/ with config.yaml)."""
    root = tenants_root()
    if not root.is_dir():
        return []
    return sorted(
        p.name for p in root.iterdir()
        if p.is_dir() and (p / "config.yaml").is_file() and not p.name.startswith(".")
    )


@lru_cache(maxsize=8)
def load_tenant(tenant_id: str) -> TenantConfig:
    """
    Load and validate a tenant. Cached — call `load_tenant.cache_clear()` to force reload
    (e.g. after the user edits config in the UI).

    Raises:
        FileNotFoundError if the tenant folder or required files are missing.
        pydantic.ValidationError if config is malformed.
    """
    root = tenants_root() / tenant_id
    if not root.is_dir():
        raise FileNotFoundError(f"Tenant {tenant_id!r} not found at {root}")

    config_path = root / "config.yaml"
    angles_path = root / "angles.json"
    copy_path = root / "copy.json"
    icp_path = root / "icp.txt"

    for required in (config_path, angles_path, copy_path):
        if not required.is_file():
            raise FileNotFoundError(f"Tenant {tenant_id!r} missing required file: {required.name}")

    with config_path.open("r", encoding="utf-8") as f:
        config_data = yaml.safe_load(f) or {}

    with angles_path.open("r", encoding="utf-8") as f:
        angles_data = json.load(f)

    with copy_path.open("r", encoding="utf-8") as f:
        copy_data = json.load(f)

    icp_definition = icp_path.read_text(encoding="utf-8").strip() if icp_path.is_file() else ""

    # Folder name is the source of truth for tenant_id (overrides any config.yaml typo)
    config_data["tenant_id"] = tenant_id
    config_data["angles"] = angles_data
    config_data["humanizer_copy"] = HumanizerCopy(angles=copy_data)
    config_data["icp_definition"] = icp_definition
    config_data["root_dir"] = root

    return TenantConfig(**config_data)
