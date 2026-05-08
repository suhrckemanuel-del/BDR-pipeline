"""Tenant configuration loader. Public API: load_tenant, list_tenants, TenantConfig."""
from .loader import load_tenant, list_tenants, tenants_root
from .schema import TenantConfig, OutreachAngle, BrandConfig, SenderConfig, CRMConfig, AngleCopy, HumanizerCopy

__all__ = [
    "load_tenant",
    "list_tenants",
    "tenants_root",
    "TenantConfig",
    "OutreachAngle",
    "BrandConfig",
    "SenderConfig",
    "CRMConfig",
    "AngleCopy",
    "HumanizerCopy",
]
