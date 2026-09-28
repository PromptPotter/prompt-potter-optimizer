"""Soft signals on L3-parsed output; the HARD layout validators live in ``dispatch/layout.py``. Outcomes land in
``PotterState.memory.wounds.l3_guard_breaches`` and surface to L3's next fire as self-healing evidence."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from promptpotter.domain.validators import LLMOutputValidator, ValidatorOutcome, run_validators

PLAN_LENGTH_FLOOR_CHARS = 60


def _check_plan_length_floor(source_output: Mapping[str, Any], **_: Any) -> ValidatorOutcome | None:
    plan = source_output.get("plan")
    if not isinstance(plan, str):
        return None
    text = plan.strip()
    if len(text) >= PLAN_LENGTH_FLOOR_CHARS:
        return None
    return ValidatorOutcome(
        validator_id=L3_PLAN_LENGTH_FLOOR.id,
        evidence={"length": len(text), "floor": PLAN_LENGTH_FLOOR_CHARS},
    )


def _check_plan_verbatim_repeat(
    source_output: Mapping[str, Any],
    *,
    prior_plan: str,
    **_: Any,
) -> ValidatorOutcome | None:
    new_plan = source_output.get("plan")
    if not isinstance(new_plan, str) or not new_plan.strip():
        return None
    prev = prior_plan.strip()
    if not prev or new_plan.strip() != prev:
        return None
    return ValidatorOutcome(
        validator_id=L3_PLAN_VERBATIM_REPEAT.id,
        evidence={"plan": new_plan},
    )


L3_PLAN_LENGTH_FLOOR: LLMOutputValidator = LLMOutputValidator(
    id="l3_plan_length_floor",
    check=_check_plan_length_floor,
)


L3_PLAN_VERBATIM_REPEAT: LLMOutputValidator = LLMOutputValidator(
    id="l3_plan_verbatim_repeat",
    check=_check_plan_verbatim_repeat,
)


L3_OUTPUT_VALIDATORS: tuple[LLMOutputValidator, ...] = (
    L3_PLAN_LENGTH_FLOOR,
    L3_PLAN_VERBATIM_REPEAT,
)


def run_l3_output_validators(
    source_output: Mapping[str, Any],
    *,
    prior_plan: str,
) -> list[ValidatorOutcome]:
    return run_validators(L3_OUTPUT_VALIDATORS, source_output, prior_plan=prior_plan)


__all__ = [
    "L3_OUTPUT_VALIDATORS",
    "L3_PLAN_LENGTH_FLOOR",
    "L3_PLAN_VERBATIM_REPEAT",
    "run_l3_output_validators",
]
