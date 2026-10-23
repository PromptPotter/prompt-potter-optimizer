"""Soft signals; the HARD layout validators live in ``dispatch/layout.py``."""

from __future__ import annotations

from promptpotter.domain.validators import ValidatorOutcome

PLAN_LENGTH_FLOOR_CHARS = 60


def l3_plan_breaches(plan: str, *, prior_plan: str) -> list[ValidatorOutcome]:
    text, prev = plan.strip(), prior_plan.strip()
    breaches: list[ValidatorOutcome] = []
    if len(text) < PLAN_LENGTH_FLOOR_CHARS:
        breaches.append(
            ValidatorOutcome(
                validator_id="l3_plan_length_floor",
                evidence={"length": len(text), "floor": PLAN_LENGTH_FLOOR_CHARS},
            )
        )
    if text and prev and text == prev:
        breaches.append(
            ValidatorOutcome(validator_id="l3_plan_verbatim_repeat", evidence={"plan": plan})
        )
    return breaches
