"""
sequence_push.py — live API push of eligible sequences to sending tools.

Sibling of sequence_export.py: same eligibility rules (status matches, latest
run passed the eval gates, contact email present, at least one email touch) —
reused verbatim via collect_export_rows(), never forked. Where the export path
produces an import CSV, this module pushes each eligible prospect as a lead
directly into an Instantly (v2 API) or Smartlead campaign.

Campaign ids live in tenant config (outreach.instantly_campaign_id /
outreach.smartlead_campaign_id). API keys never live in config: the tenant may
name an env var (outreach.*_api_key_env), falling back to INSTANTLY_API_KEY /
SMARTLEAD_API_KEY. Missing key or campaign id -> skip with a clear message,
never crash (same philosophy as hubspot_sync.py). BDR_PUSH_DRY_RUN=1 builds
every payload with zero HTTP calls. One 'pushed' event logs per prospect on a
real (non-dry-run) push, detail = tool name.

All HTTP goes through the per-tool _instantly_api / _smartlead_api choke
points so offline checks can mock the wire exactly like check_crm_sync.py.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Sequence

import requests

from app.services import store
from app.services.sequence_export import collect_export_rows
from app.tenants.schema import TenantConfig

PUSH_TOOLS = ("instantly", "smartlead")

INSTANTLY_BASE = "https://api.instantly.ai"
SMARTLEAD_BASE = "https://server.smartlead.ai"

_FALLBACK_KEY_ENVS = {
    "instantly": "INSTANTLY_API_KEY",
    "smartlead": "SMARTLEAD_API_KEY",
}


class PushAPIError(RuntimeError):
    """Non-2xx response from a sending-tool API. Carries status code + body."""

    def __init__(self, status_code: int, text: str):
        super().__init__(f"HTTP {status_code}: {text[:300]}")
        self.status_code = status_code
        self.text = text


@dataclass
class PushResult:
    tool: str
    pushed: list[str] = field(default_factory=list)  # companies pushed (payloads built, in dry-run)
    skipped: list[tuple[str, str]] = field(default_factory=list)  # (company, reason)
    errors: list[tuple[str, str]] = field(default_factory=list)  # (company, error)
    dry_run: bool = False
    payloads: list[dict] = field(default_factory=list)
    skip_reason: str = ""  # whole-push skip (no campaign id / no API key)


def _resolve_api_key(tenant: TenantConfig | None, tool: str) -> str:
    """Env-var indirection, mirroring hubspot_sync._resolve_token."""
    outreach = getattr(tenant, "outreach", None)
    named_env = getattr(outreach, f"{tool}_api_key_env", None)
    if named_env:
        key = os.environ.get(named_env, "")
        if key:
            return key
    return os.environ.get(_FALLBACK_KEY_ENVS[tool], "")


def _instantly_api(method: str, path: str, api_key: str, payload: dict | None = None) -> dict:
    """Single HTTP choke point for every Instantly call (mocked in offline checks)."""
    response = requests.request(
        method,
        INSTANTLY_BASE + path,
        headers={"Authorization": f"Bearer {api_key}"},
        json=payload,
        timeout=15,
    )
    if not 200 <= response.status_code < 300:
        raise PushAPIError(response.status_code, response.text)
    return response.json() if response.text else {}


def _smartlead_api(method: str, path: str, api_key: str, payload: dict | None = None) -> dict:
    """Single HTTP choke point for every Smartlead call (API key as query param)."""
    sep = "&" if "?" in path else "?"
    response = requests.request(
        method,
        f"{SMARTLEAD_BASE}{path}{sep}api_key={api_key}",
        json=payload,
        timeout=15,
    )
    if not 200 <= response.status_code < 300:
        raise PushAPIError(response.status_code, response.text)
    return response.json() if response.text else {}


def _touch_custom_fields(touches: list[tuple]) -> dict:
    """Flatten (subject, body, day) email touches into subject_N/body_N/day_N."""
    fields: dict = {}
    for i, (subject, body, day) in enumerate(touches, start=1):
        fields[f"subject_{i}"] = subject
        fields[f"body_{i}"] = body
        fields[f"day_{i}"] = str(day)
    return fields


def build_lead_payload(row: dict, tool: str, campaign_id: str) -> tuple[str, dict]:
    """(path, payload) for pushing one collect_export_rows row as a lead.

    Pure and deterministic — unit-checked offline. Mirrors the CSV column
    mapping in sequence_export.build_csv.
    """
    if tool not in PUSH_TOOLS:
        raise ValueError(f"Unknown push tool {tool!r}. Valid: {PUSH_TOOLS}")

    touches = row["touches"]
    custom = _touch_custom_fields(touches)
    if tool == "instantly":
        first_body = touches[0][1] if touches else ""
        personalization = first_body.splitlines()[0] if first_body else ""
        payload = {
            "campaign": campaign_id,
            "email": row["email"],
            "first_name": row["first_name"],
            "last_name": row["last_name"],
            "company_name": row["company"],
            "website": row["website"],
            "personalization": personalization,
            "custom_variables": {**custom, "angle": row["angle"]},
        }
        return "/api/v2/leads", payload

    payload = {
        "lead_list": [
            {
                "email": row["email"],
                "first_name": row["first_name"],
                "last_name": row["last_name"],
                "company_name": row["company"],
                "website": row["website"],
                "linkedin_profile": row["linkedin_profile"],
                "custom_fields": {**custom, "angle": row["angle"]},
            }
        ]
    }
    return f"/api/v1/campaigns/{campaign_id}/leads", payload


def push_sequences(
    tenant: TenantConfig,
    tool: str,
    statuses: Sequence[str] = ("queued",),
    *,
    dry_run: bool = False,
    log_events: bool = True,
) -> PushResult:
    """Push a tenant's eligible sequences into one sending tool. Never raises.

    Skips whole-push (with a clear reason, zero HTTP) when the tenant has no
    campaign id for the tool or no API key is resolvable. Per-prospect API
    failures land in .errors and the remaining prospects are still attempted.
    """
    if tool not in PUSH_TOOLS:
        return PushResult(tool=tool, skip_reason=f"Unknown push tool {tool!r}. Valid: {PUSH_TOOLS}")

    dry_run = dry_run or os.environ.get("BDR_PUSH_DRY_RUN") == "1"

    campaign_id = getattr(getattr(tenant, "outreach", None), f"{tool}_campaign_id", None) or ""
    if not campaign_id:
        return PushResult(
            tool=tool,
            dry_run=dry_run,
            skip_reason=f"outreach.{tool}_campaign_id not set in tenant config.",
        )

    api_key = _resolve_api_key(tenant, tool)
    if not api_key and not dry_run:
        return PushResult(
            tool=tool,
            skip_reason=(
                f"{_FALLBACK_KEY_ENVS[tool]} missing in .env "
                f"(or tenant outreach.{tool}_api_key_env unset)."
            ),
        )

    api = _instantly_api if tool == "instantly" else _smartlead_api
    rows, eligibility_skips = collect_export_rows(tenant.tenant_id, statuses)
    result = PushResult(tool=tool, dry_run=dry_run, skipped=list(eligibility_skips))

    for row in rows:
        path, payload = build_lead_payload(row, tool, campaign_id)
        result.payloads.append(payload)
        if dry_run:
            result.pushed.append(row["company"])
            continue
        try:
            api("POST", path, api_key, payload)
        except Exception as exc:
            result.errors.append((row["company"], str(exc)))
            continue
        result.pushed.append(row["company"])
        if log_events:
            store.log_event(tenant.tenant_id, row["company"], "pushed", detail=tool, angle=row["angle"])

    return result
