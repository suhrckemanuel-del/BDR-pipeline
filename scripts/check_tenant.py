"""
check_tenant.py — Smoke test the tenant loader against all tenants under tenants/.

Usage:
    python scripts/check_tenant.py            # check every tenant
    python scripts/check_tenant.py demo       # check just one

Exits non-zero on validation failure. Use this in CI or after editing config.yaml.
"""
from __future__ import annotations

import io
import sys
from pathlib import Path

# Force UTF-8 stdout so we can print brand emojis on Windows consoles.
if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

# Make app/ importable when running from repo root
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.tenants import list_tenants, load_tenant  # noqa: E402


def check(tenant_id: str) -> bool:
    print(f"\n--- {tenant_id} ---")
    try:
        t = load_tenant(tenant_id)
    except Exception as e:
        print(f"  FAIL: {type(e).__name__}: {e}")
        return False

    print(f"  brand:     {t.brand.name} {t.brand.icon}  ({t.brand.primary_color})")
    print(f"  business:  {t.business.description[:80]}...")
    print(f"  persona:   {t.persona.title}")
    print(f"  sender:    {t.sender.name}")
    print(f"  angles:    {[a.name for a in t.angles]}")
    print(f"  copy keys: {[a.key for a in t.humanizer_copy.angles]}")
    icp_lines = len(t.icp_definition.splitlines())
    print(f"  icp.txt:   {icp_lines} lines")
    print(f"  prospects: {t.prospects_csv} (exists: {t.prospects_csv.exists()})")
    print(f"  OK")
    return True


def main() -> int:
    requested = sys.argv[1:] or list_tenants()
    if not requested:
        print("No tenants found under tenants/.")
        return 1
    print(f"Checking tenants: {requested}")
    ok = all(check(t) for t in requested)
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
