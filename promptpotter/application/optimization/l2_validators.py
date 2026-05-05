"""Validators on L2/L3 parsed outputs.

V1 surface — soft signals only. Layout HARD validators
(``mandatory_placeholders_present``, ``all_placeholders_known``,
``no_dups_within_slot``) live in :mod:`domain.l1_layout` and run from
``escalation._parse_l2`` directly; they roll back to the prior valid
layout when they fail.

L2 soft validators here:
* :data:`L2_DIRECTIVE_LENGTH_FLOOR` — directive is too short to carry signal.
* :data:`L2_DIRECTIVE_VERBATIM_REPEAT` — same directive as last fire.

L3 soft validators here:
* :data:`L3_PLAN_LENGTH_FLOOR` — plan is too short to carry signal.
* :data:`L3_PLAN_VERBATIM_REPEAT` — same plan as the prior plan.

All outcomes append to ``opt_sp.l{2,3}_output_failures`` and surface to
the next fire as self-healing evidence via the unified ``failures``
signal.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from promptpotter.domain.opt_search_point import OptSearchPoint
from promptpotter.domain.validators import LLMOutputValidator, ValidatorOutcome

DIRECTIVE_LENGTH_FLOOR_CHARS = 40
PLAN_LENGTH_FLOOR_CHARS = 60


def _check_directive_length_floor(
    source_output: Mapping[str, Any], **_: Any
) -> ValidatorOutcome | None:
    directive = source_output.get("directive")
    if not isinstance(directive, str):
        return None
    text = directive.strip()
    if len(text) >= DIRECTIVE_LENGTH_FLOOR_CHARS:
        return None
    return ValidatorOutcome(
        validator_id=L2_DIRECTIVE_LENGTH_FLOOR.id,
        passed=False,
        score=0.5,
        evidence={"length": len(text), "floor": DIRECTIVE_LENGTH_FLOOR_CHARS},
        nurse_target="l3",
    )


def _check_directive_verbatim_repeat(
    source_output: Mapping[str, Any],
    *,
    opt_sp: OptSearchPoint | None = None,
    **_: Any,
) -> ValidatorOutcome | None:
    if opt_sp is None:
        return None
    new_directive = source_output.get("directive")
    if not isinstance(new_directive, str) or not new_directive.strip():
        return None
    prev = (opt_sp.l2_brief or "").strip()
    if not prev or new_directive.strip() != prev:
        return None
    return ValidatorOutcome(
        validator_id=L2_DIRECTIVE_VERBATIM_REPEAT.id,
        passed=False,
        score=0.0,
        evidence={"directive": new_directive},
        nurse_target="l3",
    )


def _check_plan_length_floor(source_output: Mapping[str, Any], **_: Any) -> ValidatorOutcome | None:
    plan = source_output.get("plan")
    if not isinstance(plan, str):
        return None
    text = plan.strip()
    if len(text) >= PLAN_LENGTH_FLOOR_CHARS:
        return None
    return ValidatorOutcome(
        validator_id=L3_PLAN_LENGTH_FLOOR.id,
        passed=False,
        score=0.5,
        evidence={"length": len(text), "floor": PLAN_LENGTH_FLOOR_CHARS},
        nurse_target="l3",
    )


def _check_plan_verbatim_repeat(
    source_output: Mapping[str, Any],
    *,
    opt_sp: OptSearchPoint | None = None,
    **_: Any,
) -> ValidatorOutcome | None:
    if opt_sp is None:
        return None
    new_plan = source_output.get("plan")
    if not isinstance(new_plan, str) or not new_plan.strip():
        return None
    prev = (opt_sp.plan or "").strip()
    if not prev or new_plan.strip() != prev:
        return None
    return ValidatorOutcome(
        validator_id=L3_PLAN_VERBATIM_REPEAT.id,
        passed=False,
        score=0.0,
        evidence={"plan": new_plan},
        nurse_target="l3",
    )


L2_DIRECTIVE_LENGTH_FLOOR: LLMOutputValidator = LLMOutputValidator(
    id="l2_directive_length_floor",
    description=(
        "L2's directive is below the minimum length floor. A short directive "
        "rarely carries enough strategic signal to steer L1 — the loop is "
        "wasting a fire. L3 should refine the plan so the next L2 fire "
        "produces a directive with concrete tactical guidance."
    ),
    nurse_target="l3",
    check=_check_directive_length_floor,
)


L2_DIRECTIVE_VERBATIM_REPEAT: LLMOutputValidator = LLMOutputValidator(
    id="l2_directive_verbatim_repeat",
    description=(
        "L2's directive this round equals the previous round's directive on "
        "the OSP. L2 stalled and repeated itself — no learning. L3 must "
        "replan to break the loop."
    ),
    nurse_target="l3",
    check=_check_directive_verbatim_repeat,
)


L3_PLAN_LENGTH_FLOOR: LLMOutputValidator = LLMOutputValidator(
    id="l3_plan_length_floor",
    description=(
        "L3's plan is below the minimum length floor. A terse plan rarely "
        "carries enough strategic framework to steer L2/L1 — surface as "
        "evidence so the next L3 fire produces a richer plan."
    ),
    nurse_target="l3",
    check=_check_plan_length_floor,
)


L3_PLAN_VERBATIM_REPEAT: LLMOutputValidator = LLMOutputValidator(
    id="l3_plan_verbatim_repeat",
    description=(
        "L3's plan this fire equals the prior plan on the OSP. L3 repeated "
        "itself — no strategic shift. Surface as evidence; if the loop "
        "retriggers, the next L3 fire has the prior repetition as input."
    ),
    nurse_target="l3",
    check=_check_plan_verbatim_repeat,
)


L2_OUTPUT_VALIDATORS: tuple[LLMOutputValidator, ...] = (
    L2_DIRECTIVE_LENGTH_FLOOR,
    L2_DIRECTIVE_VERBATIM_REPEAT,
)


L3_OUTPUT_VALIDATORS: tuple[LLMOutputValidator, ...] = (
    L3_PLAN_LENGTH_FLOOR,
    L3_PLAN_VERBATIM_REPEAT,
)


def run_l2_output_validators(
    source_output: Mapping[str, Any],
    opt_sp: OptSearchPoint,
) -> list[ValidatorOutcome]:
    """Run every registered L2-output validator; return non-None outcomes."""
    return _run(L2_OUTPUT_VALIDATORS, source_output, opt_sp)


def run_l3_output_validators(
    source_output: Mapping[str, Any],
    opt_sp: OptSearchPoint,
) -> list[ValidatorOutcome]:
    """Run every registered L3-output validator; return non-None outcomes."""
    return _run(L3_OUTPUT_VALIDATORS, source_output, opt_sp)


def _run(
    validators: tuple[LLMOutputValidator, ...],
    source_output: Mapping[str, Any],
    opt_sp: OptSearchPoint,
) -> list[ValidatorOutcome]:
    outcomes: list[ValidatorOutcome] = []
    for validator in validators:
        outcome = validator.run(source_output, opt_sp=opt_sp)
        if outcome is not None:
            outcomes.append(outcome)
    return outcomes
