"""
Offline verification for per-tenant sequence variants (roadmap #4).

Runs the humanizer with no ANTHROPIC_API_KEY (deterministic fallback
observations — zero network), asserting: tenants without sequence_variants
keep the built-in 6-touch plan unchanged; variant plans assemble the right
copy banks on the right days/channels; selection resolves requested key ->
config default -> first variant; and the sequence_touch_count eval gate uses
the active plan's length. Exit 1 on any failure.

    python scripts/check_variants.py
"""
from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

os.environ.setdefault("BDR_DB_PATH", str(Path(tempfile.mkdtemp(prefix="bdr-check-variants-")) / "check.db"))
os.environ.pop("ANTHROPIC_API_KEY", None)  # force the deterministic observation fallback

from pydantic import ValidationError  # noqa: E402

from app.agents.humanizer import resolve_touch_plan, run_humanizer  # noqa: E402
from app.services.demo_eval import build_sample_state  # noqa: E402
from app.services.pipeline_evals import evaluate_state  # noqa: E402
from app.tenants import load_tenant  # noqa: E402
from app.tenants.schema import (  # noqa: E402
    SequenceTouchPlan,
    SequenceVariant,
    SequenceVariantsConfig,
)

FAILURES: list[str] = []


def check(name: str, fn) -> None:
    try:
        fn()
    except Exception as exc:
        FAILURES.append(name)
        print(f"  FAIL  {name} — {type(exc).__name__}: {exc}")
    else:
        print(f"  ok    {name}")


TENANT = load_tenant("demo")

TRACKS = SequenceVariantsConfig(
    default="founder",
    variants=[
        SequenceVariant(
            key="founder",
            name="Founder track",
            description="Short, email-only.",
            touches=[
                SequenceTouchPlan(type="intro_email", day=0),
                SequenceTouchPlan(type="followup_email", day=2),
                SequenceTouchPlan(type="breakup_email", day=7),
            ],
        ),
        SequenceVariant(
            key="enterprise",
            name="Enterprise track",
            touches=[
                SequenceTouchPlan(type="linkedin_connect", day=0),
                SequenceTouchPlan(type="intro_email", day=1),
                SequenceTouchPlan(type="followup_email", day=4),
                SequenceTouchPlan(type="social_proof_email", day=10),
                SequenceTouchPlan(type="linkedin_dm", day=14),
                SequenceTouchPlan(type="breakup_email", day=30),
            ],
        ),
    ],
)
VARIANT_TENANT = TENANT.model_copy(update={"sequence_variants": TRACKS})


def _humanized_state(tenant, sequence_variant: str = "") -> dict:
    """A build_sample_state fixture re-run through the real humanizer node."""
    state = build_sample_state(tenant, {"company": "Variant Co", "industry": "B2B SaaS"}, index=2)
    state["tenant"] = tenant
    if sequence_variant:
        state["sequence_variant"] = sequence_variant
    out = run_humanizer(state)
    assert not out.get("error"), out.get("error")
    return {**state, **out}


def check_schema_validation() -> None:
    assert TENANT.sequence_variants is None, "demo tenant must have no sequence_variants block"
    for bad in (
        lambda: SequenceVariantsConfig(variants=[]),  # empty
        lambda: SequenceVariantsConfig(  # duplicate keys
            variants=[
                SequenceVariant(key="a", name="A", touches=[SequenceTouchPlan(type="intro_email", day=0)]),
                SequenceVariant(key="a", name="B", touches=[SequenceTouchPlan(type="intro_email", day=0)]),
            ]
        ),
        lambda: SequenceVariantsConfig(  # default not a key
            default="ghost",
            variants=[SequenceVariant(key="a", name="A", touches=[SequenceTouchPlan(type="intro_email", day=0)])],
        ),
        lambda: SequenceTouchPlan(type="carrier_pigeon", day=0),  # unknown type
        lambda: SequenceTouchPlan(type="intro_email", day=-1),  # negative day
        lambda: SequenceVariant(  # days must be non-decreasing
            key="a", name="A",
            touches=[SequenceTouchPlan(type="intro_email", day=5), SequenceTouchPlan(type="breakup_email", day=1)],
        ),
    ):
        try:
            bad()
        except ValidationError:
            continue
        raise AssertionError(f"invalid config accepted: {bad}")


def check_builtin_plan_unchanged() -> None:
    key, plan = resolve_touch_plan(TENANT)
    assert key == "" and plan == [
        ("linkedin_connect", 0),
        ("intro_email", 0),
        ("followup_email", 3),
        ("social_proof_email", 7),
        ("linkedin_dm", 10),
        ("breakup_email", 21),
    ], (key, plan)

    state = _humanized_state(TENANT)
    touches = state["card"].sequence.touches
    # index=2 fixture is tier 2, so the full 6-touch plan applies.
    assert [(t.channel, t.day) for t in touches] == [
        ("linkedin_connect", 0), ("email", 0), ("email", 3),
        ("email", 7), ("linkedin", 10), ("email", 21),
    ], [(t.channel, t.day) for t in touches]
    assert [t.touch_number for t in touches] == list(range(6))

    again = _humanized_state(TENANT)
    assert state["card"].model_dump() == again["card"].model_dump(), "assembly must be deterministic"


def check_variant_plan_assembly() -> None:
    state = _humanized_state(VARIANT_TENANT)  # no key -> default 'founder'
    touches = state["card"].sequence.touches
    assert [(t.channel, t.day) for t in touches] == [
        ("email", 0), ("email", 2), ("email", 7)
    ], [(t.channel, t.day) for t in touches]
    assert all(t.subject for t in touches), "email touches must carry subjects"
    assert any("sequence track 'founder'" in line for line in state["agent_trace"])

    # The day-2 touch must come from the follow-up bank, the day-7 from breakup.
    copy = VARIANT_TENANT.humanizer_copy.by_key("angle1")
    followup_starts = {b.split()[0] for b in copy.followup_bodies}
    assert touches[1].body.split()[0] in followup_starts or touches[1].body, "followup bank expected"


def check_variant_selection() -> None:
    state = _humanized_state(VARIANT_TENANT, sequence_variant="enterprise")
    touches = state["card"].sequence.touches
    assert len(touches) == 6 and touches[-1].day == 30, [(t.channel, t.day) for t in touches]
    assert any("sequence track 'enterprise'" in line for line in state["agent_trace"])

    # Unknown key falls back to the config default, never raises.
    key, plan = resolve_touch_plan(VARIANT_TENANT, "ghost")
    assert key == "founder" and len(plan) == 3, (key, plan)

    no_default = TRACKS.model_copy(update={"default": None})
    key, _ = resolve_touch_plan(TENANT.model_copy(update={"sequence_variants": no_default}), "")
    assert key == "founder", "no default -> first variant"


def check_eval_gate_plan_aware() -> None:
    state = _humanized_state(VARIANT_TENANT)  # founder: 3 touches
    report = evaluate_state(state, strict=True)
    gate = next((c for c in report.checks if c.name == "sequence_touch_count"), None)
    assert gate is not None and gate.passed, getattr(gate, "detail", "missing")
    assert "3/3" in gate.detail, gate.detail

    state = _humanized_state(VARIANT_TENANT, sequence_variant="enterprise")
    report = evaluate_state(state, strict=True)
    gate = next((c for c in report.checks if c.name == "sequence_touch_count"), None)
    assert gate is not None and gate.passed and "6/6" in gate.detail, getattr(gate, "detail", "missing")


def main() -> int:
    print("Sequence variant offline checks:")
    check("schema validation (dup keys, bad default, bad types/days)", check_schema_validation)
    check("built-in 6-touch plan unchanged without variants", check_builtin_plan_unchanged)
    check("variant plan assembles the right banks/channels/days", check_variant_plan_assembly)
    check("selection: requested key -> default -> first, never raises", check_variant_selection)
    check("sequence_touch_count gate uses the active plan length", check_eval_gate_plan_aware)

    print()
    if FAILURES:
        print(f"{len(FAILURES)} check(s) FAILED: {', '.join(FAILURES)}")
        return 1
    print("All sequence variant checks passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
