"""Validators on L2's parsed output dict.

Each validator examines L2's freshly-parsed JSON response and produces a
``ValidatorOutcome`` (or ``None`` when clean). A non-clean outcome lands on
``OptSearchPoint.l2_output_failures`` and forces L3 escalation this round —
L3 is L2's nurse-LLM, analogue of how L2 nurses L1 via validation_failures.

Three starter validators (extensible registry — add more here):

* :data:`L2_CROSS_FIELD_DUPLICATION` — same N+ line block in ≥2 of
  ``{brief, template_override, *text_overrides.values()}``.
* :data:`L2_VERBATIM_SELF_REPEAT` — L2's ``brief`` this round equals the
  previous round's brief on the OSP (L2 wrote the same thing again).
* :data:`L2_CATALOGUE_REDUNDANCY` — a ``text_overrides[section]`` value
  equals the section's existing override on the OSP (no-op write).
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from promptpotter.domain.opt_search_point import OptSearchPoint
from promptpotter.domain.validators import LLMOutputValidator, ValidatorOutcome

DEFAULT_DUPLICATION_MIN_LINES = 3


def _line_blocks(text: str, min_lines: int) -> list[str]:
    """Return every contiguous block of ``min_lines`` non-blank lines.

    Sliding window: for every starting line ``i``, the block
    ``lines[i : i + min_lines]`` is included if all its lines are non-blank.
    Trailing whitespace stripped per line; cross-block matching uses the
    normalized form.
    """
    if not isinstance(text, str) or not text:
        return []
    lines = [ln.strip() for ln in text.splitlines()]
    blocks: list[str] = []
    for i in range(len(lines) - min_lines + 1):
        window = lines[i : i + min_lines]
        if all(window):
            blocks.append("\n".join(window))
    return blocks


def _gather_l2_text_fields(source_output: Mapping[str, Any]) -> dict[str, str]:
    """Map of {field_label: text} across L2's text-bearing output fields."""
    out: dict[str, str] = {}
    brief = source_output.get("brief")
    if isinstance(brief, str) and brief:
        out["brief"] = brief
    tmpl = source_output.get("template_override")
    if isinstance(tmpl, str) and tmpl:
        out["template_override"] = tmpl
    text_overrides = source_output.get("text_overrides") or {}
    if isinstance(text_overrides, dict):
        for section, val in text_overrides.items():
            if isinstance(val, str) and val:
                out[f"text_overrides.{section}"] = val
    return out


def _check_cross_field_duplication(
    source_output: Mapping[str, Any],
    *,
    min_lines: int = DEFAULT_DUPLICATION_MIN_LINES,
    **_: Any,
) -> ValidatorOutcome | None:
    fields = _gather_l2_text_fields(source_output)
    if len(fields) < 2:
        return None
    block_origin: dict[str, list[str]] = {}
    for field_label, text in fields.items():
        for block in _line_blocks(text, min_lines):
            block_origin.setdefault(block, []).append(field_label)
    duplicates: list[dict[str, Any]] = []
    seen_blocks: set[str] = set()
    for block, origins in block_origin.items():
        unique_origins = sorted(set(origins))
        if len(unique_origins) < 2 or block in seen_blocks:
            continue
        seen_blocks.add(block)
        duplicates.append(
            {
                "block": block,
                "fields": unique_origins,
                "line_count": min_lines,
            }
        )
    if not duplicates:
        return None
    return ValidatorOutcome(
        validator_id=L2_CROSS_FIELD_DUPLICATION.id,
        passed=False,
        score=0.0,
        evidence={"duplicates": duplicates, "min_lines": min_lines},
        nurse_target="l3",
    )


def _check_verbatim_self_repeat(
    source_output: Mapping[str, Any],
    *,
    opt_sp: OptSearchPoint | None = None,
    **_: Any,
) -> ValidatorOutcome | None:
    if opt_sp is None:
        return None
    new_brief = source_output.get("brief")
    if not isinstance(new_brief, str) or not new_brief.strip():
        return None
    prev_brief = opt_sp.l2_brief or ""
    if not prev_brief.strip():
        return None
    if new_brief.strip() != prev_brief.strip():
        return None
    return ValidatorOutcome(
        validator_id=L2_VERBATIM_SELF_REPEAT.id,
        passed=False,
        score=0.0,
        evidence={"brief": new_brief},
        nurse_target="l3",
    )


def _check_catalogue_redundancy(
    source_output: Mapping[str, Any],
    *,
    opt_sp: OptSearchPoint | None = None,
    **_: Any,
) -> ValidatorOutcome | None:
    if opt_sp is None:
        return None
    text_overrides = source_output.get("text_overrides") or {}
    if not isinstance(text_overrides, dict) or not text_overrides:
        return None
    existing = opt_sp.l1_section_overrides_text or {}
    redundant: list[dict[str, Any]] = []
    for section, new_val in text_overrides.items():
        if not isinstance(new_val, str) or not new_val:
            continue
        prev_val = existing.get(section, "")
        if isinstance(prev_val, str) and prev_val.strip() and new_val.strip() == prev_val.strip():
            redundant.append({"section": section, "value": new_val})
    if not redundant:
        return None
    return ValidatorOutcome(
        validator_id=L2_CATALOGUE_REDUNDANCY.id,
        passed=False,
        score=0.0,
        evidence={"redundant_overrides": redundant},
        nurse_target="l3",
    )


L2_CROSS_FIELD_DUPLICATION: LLMOutputValidator = LLMOutputValidator(
    id="l2_cross_field_duplication",
    description=(
        "L2 wrote the same N+ line block into two different output fields "
        "(brief / template_override / text_overrides[*]). Indicates a "
        "broken strategy mode — L3 must rewrite the plan to refine L2's "
        "brief shape so it stops duplicating itself."
    ),
    nurse_target="l3",
    check=_check_cross_field_duplication,
)


L2_VERBATIM_SELF_REPEAT: LLMOutputValidator = LLMOutputValidator(
    id="l2_verbatim_self_repeat",
    description=(
        "L2's brief this round equals the previous round's brief "
        "on the OSP. L2 stalled and repeated itself — no learning. L3 must "
        "replan to break the loop."
    ),
    nurse_target="l3",
    check=_check_verbatim_self_repeat,
)


L2_CATALOGUE_REDUNDANCY: LLMOutputValidator = LLMOutputValidator(
    id="l2_catalogue_redundancy",
    description=(
        "L2 wrote a text_overrides[section] whose value equals the existing "
        "override on the OSP for that section. The override changes nothing. "
        "L3 should refine the plan to direct L2 toward novel mutations."
    ),
    nurse_target="l3",
    check=_check_catalogue_redundancy,
)


L2_OUTPUT_VALIDATORS: tuple[LLMOutputValidator, ...] = (
    L2_CROSS_FIELD_DUPLICATION,
    L2_VERBATIM_SELF_REPEAT,
    L2_CATALOGUE_REDUNDANCY,
)


def run_l2_output_validators(
    source_output: Mapping[str, Any],
    opt_sp: OptSearchPoint,
) -> list[ValidatorOutcome]:
    """Run every registered L2-output validator; return non-None outcomes."""
    outcomes: list[ValidatorOutcome] = []
    for validator in L2_OUTPUT_VALIDATORS:
        outcome = validator.run(source_output, opt_sp=opt_sp)
        if outcome is not None:
            outcomes.append(outcome)
    return outcomes
