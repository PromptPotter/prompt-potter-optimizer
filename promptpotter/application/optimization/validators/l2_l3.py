"""Validators on L2/L3 parsed outputs.

V1 surface — soft signals only. Layout HARD validators
(``mandatory_placeholders_present``, ``all_placeholders_known``,
``no_dups_within_slot``) live in :mod:`domain.l1_layout` and run from
``escalation._parse_l2`` directly; they roll back to the prior valid
layout when they fail.

L2 soft validators here:
* :data:`L2_TASK_CONTEXT_VERBATIM_REPEAT` — proposed ``task_context``
  refinement produced no semantic change vs the prior OSP framing
  (either the LLM repeated the existing fields or sent a no-op merge).
* :data:`L2_DUPLICATE_INSERT` — proposed ``task_context`` re-asserts
  ≥``DUPLICATE_INSERT_LINE_THRESHOLD`` lines already in the prior
  framing. Merge still succeeds, but L2 is pasting back what's there.

L3 soft validators here:
* :data:`L3_PLAN_LENGTH_FLOOR` — plan is too short to carry signal.
* :data:`L3_PLAN_VERBATIM_REPEAT` — same plan as the prior plan.

All outcomes append to ``opt_sp.l{2,3}_guard_breaches`` and surface to
L3's next fire as self-healing evidence via the ``l2_guard_breaches``
and ``l3_guard_breaches`` dispatch-hub signals.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from typing import Any

from promptpotter.domain.opt_search_point import OptSearchPoint
from promptpotter.domain.validators import LLMOutputValidator, ValidatorOutcome

_WORD_RE = re.compile(r"\w+")

PLAN_LENGTH_FLOOR_CHARS = 60
DUPLICATE_INSERT_LINE_THRESHOLD = 3
PARAPHRASE_REPEAT_JACCARD_THRESHOLD = 0.5


def _check_task_context_verbatim_repeat(
    source_output: Mapping[str, Any],
    *,
    opt_sp: OptSearchPoint | None = None,
    **_: Any,
) -> ValidatorOutcome | None:
    """Fire when L2 *proposed* a task_context update but it merged to a no-op.

    ``escalation._parse_l2`` passes ``task_context_proposed`` (the raw
    dict L2 emitted) and ``task_context_applied`` (the merged
    TaskDecomposition or ``None`` when the merge produced no change).
    A non-empty proposal that yielded ``None`` means the LLM tried but
    repeated the prior framing — the no-op task_context analogue of a
    verbatim signal repeat.
    """
    proposed = source_output.get("task_context_proposed")
    applied = source_output.get("task_context_applied")
    if not isinstance(proposed, dict) or not proposed:
        return None
    if applied is not None:
        return None
    return ValidatorOutcome(
        validator_id=L2_TASK_CONTEXT_VERBATIM_REPEAT.id,
        passed=False,
        score=0.0,
        evidence={"proposed_keys": sorted(proposed.keys())},
        nurse_target="l3",
    )


def _check_task_context_paraphrase_repeat(
    source_output: Mapping[str, Any],
    *,
    opt_sp: OptSearchPoint | None = None,
    **_: Any,
) -> ValidatorOutcome | None:
    """Fire when L2's proposed task_context paraphrases the prior framing.

    Sibling of ``_check_task_context_verbatim_repeat`` (no-op merge) and
    ``_check_duplicate_insert`` (≥3 verbatim lines). This catches the
    middle ground: the proposed update *is* a new string, the merge
    *succeeds*, but the word-set overlap with the prior framing is so
    high that no real semantic delta lands. Same recovery as the other
    two — surface as evidence; L3 force-trigger via
    ``executor.py::escalate_l2`` reads ``l2_guard_breaches`` and replans.

    Token-set Jaccard above
    :data:`PARAPHRASE_REPEAT_JACCARD_THRESHOLD` on any single updated
    field is sufficient to fire. Per-field rather than aggregate so a
    legitimate refinement on one field isn't drowned by a stale repeat
    on another.
    """
    if opt_sp is None:
        return None
    proposed = source_output.get("task_context_proposed")
    if not isinstance(proposed, dict) or not proposed:
        return None
    prior = opt_sp.task_context.to_dict()
    worst_overlap = 0.0
    worst_field = ""
    for field_name, new_value in proposed.items():
        if not isinstance(new_value, str) or not new_value:
            continue
        prior_value = prior.get(field_name, "")
        if not isinstance(prior_value, str) or not prior_value:
            continue
        new_words = {w for w in _WORD_RE.findall(new_value.lower()) if len(w) > 2}
        prior_words = {w for w in _WORD_RE.findall(prior_value.lower()) if len(w) > 2}
        if not new_words or not prior_words:
            continue
        overlap = len(new_words & prior_words) / len(new_words | prior_words)
        if overlap > worst_overlap:
            worst_overlap = overlap
            worst_field = field_name
    if worst_overlap < PARAPHRASE_REPEAT_JACCARD_THRESHOLD:
        return None
    return ValidatorOutcome(
        validator_id=L2_TASK_CONTEXT_PARAPHRASE_REPEAT.id,
        passed=False,
        score=0.0,
        evidence={
            "field": worst_field,
            "jaccard": round(worst_overlap, 3),
            "threshold": PARAPHRASE_REPEAT_JACCARD_THRESHOLD,
        },
        nurse_target="l3",
    )


def _check_duplicate_insert(
    source_output: Mapping[str, Any],
    *,
    opt_sp: OptSearchPoint | None = None,
    **_: Any,
) -> ValidatorOutcome | None:
    """Fire when L2's proposed task_context re-asserts ≥3 lines already
    in the prior OSP framing. Distinct from :data:`L2_TASK_CONTEXT_VERBATIM_REPEAT`
    (no-op merge): here the merge succeeds, but L2 still pasted back lines
    that were already there — the refinement surface is exhausted.
    """
    if opt_sp is None:
        return None
    proposed = source_output.get("task_context_proposed")
    if not isinstance(proposed, dict) or not proposed:
        return None
    prior = opt_sp.task_context.to_dict()
    duplicate_lines = 0
    overlapped_fields: list[str] = []
    for field_name, new_value in proposed.items():
        if not isinstance(new_value, str) or not new_value:
            continue
        prior_value = prior.get(field_name, "")
        if not isinstance(prior_value, str) or not prior_value:
            continue
        new_lines = {ln.strip() for ln in new_value.splitlines() if ln.strip()}
        prior_lines = {ln.strip() for ln in prior_value.splitlines() if ln.strip()}
        overlap = new_lines & prior_lines
        if overlap:
            duplicate_lines += len(overlap)
            overlapped_fields.append(field_name)
    if duplicate_lines < DUPLICATE_INSERT_LINE_THRESHOLD:
        return None
    return ValidatorOutcome(
        validator_id=L2_DUPLICATE_INSERT.id,
        passed=False,
        score=0.0,
        evidence={
            "duplicate_lines": duplicate_lines,
            "threshold": DUPLICATE_INSERT_LINE_THRESHOLD,
            "fields": sorted(overlapped_fields),
        },
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


L2_TASK_CONTEXT_VERBATIM_REPEAT: LLMOutputValidator = LLMOutputValidator(
    id="l2_task_context_verbatim_repeat",
    description=(
        "L2's proposed ``task_context`` refinement merged to a no-op "
        "against the prior OSP framing — the LLM tried to refine but "
        "produced no semantic delta. L3 must replan to break the loop."
    ),
    nurse_target="l3",
    check=_check_task_context_verbatim_repeat,
)


L2_DUPLICATE_INSERT: LLMOutputValidator = LLMOutputValidator(
    id="l2_duplicate_insert",
    description=(
        "L2's proposed ``task_context`` re-asserts ≥3 lines already in "
        "the prior framing. Different from verbatim_repeat (no-op merge): "
        "the merge succeeds, but L2 is still pasting back what's there. "
        "The framing-refinement surface is exhausted — L3 must replan."
    ),
    nurse_target="l3",
    check=_check_duplicate_insert,
)


L2_TASK_CONTEXT_PARAPHRASE_REPEAT: LLMOutputValidator = LLMOutputValidator(
    id="l2_task_context_paraphrase_repeat",
    description=(
        "L2's proposed ``task_context`` paraphrases the prior framing — "
        "the merge succeeds with a new string, but per-field token-set "
        "Jaccard ≥ 0.5 means no real semantic delta lands. Sibling of "
        "verbatim_repeat (no-op merge) and duplicate_insert (≥3 verbatim "
        "lines); catches the paraphrase middle ground. L3 must replan."
    ),
    nurse_target="l3",
    check=_check_task_context_paraphrase_repeat,
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
    L2_TASK_CONTEXT_VERBATIM_REPEAT,
    L2_DUPLICATE_INSERT,
    L2_TASK_CONTEXT_PARAPHRASE_REPEAT,
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


__all__ = [
    "DUPLICATE_INSERT_LINE_THRESHOLD",
    "L2_DUPLICATE_INSERT",
    "L2_OUTPUT_VALIDATORS",
    "L2_TASK_CONTEXT_VERBATIM_REPEAT",
    "L3_OUTPUT_VALIDATORS",
    "L3_PLAN_LENGTH_FLOOR",
    "L3_PLAN_VERBATIM_REPEAT",
    "PLAN_LENGTH_FLOOR_CHARS",
    "run_l2_output_validators",
    "run_l3_output_validators",
]
