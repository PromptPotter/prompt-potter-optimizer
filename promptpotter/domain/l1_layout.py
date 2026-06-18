"""L1 layout — L2's mutation surface for L1_GENERATE. Per-slot lists of placeholder names that
the dispatch hub resolves when filling L1's PromptTemplate. `answer_format` is template-fixed.

Validation (`validate_l1_layout`):
* HARD (missing mandatory / unknown name / dups within slot) → rollback + wound for L2 next fire.
* SOFT (unchanged from prior) → apply with warning.
"""

from __future__ import annotations

from typing import Any, cast

from pydantic import BaseModel, ConfigDict, Field

from promptpotter.domain.validators import ValidatorOutcome

# Cross-layer protocol fields L1 cannot operate without. Dropping any (esp. critique) fires
# `l1_layout_missing_mandatory` — a guard breach that routes to L3 (replan) rather than letting
# L2 starve L1 of failure context.
L1_MANDATORY: frozenset[str] = frozenset(
    {"plan", "task_context", "rendered_prompt", "pipeline_param_catalogue", "critique"}
)

# Names L2 may pick from. Subset of the global registry; L2-internal signals are excluded so
# L1 can't see L2's own state.
L1_POSSIBLE: frozenset[str] = frozenset(
    {
        "plan",
        "rendered_prompt",
        "pipeline_param_catalogue",
        "diagnostics",
        "escalation_panel",
        "l1_wounds",
        "task_context",
        "critique",
        "axis_memory",
        "origin_strengths",
        "intractable_samples",
        "archive_top_runs",
        "rare_hit_samples",
    }
)

# PromptTemplate slots a layout addresses. ``answer_format`` is omitted —
# it carries the output JSON schema and is owned by the template, not L2.
L1_LAYOUT_SLOTS: tuple[str, ...] = (
    "persona",
    "task_intent",
    "problem_description",
    "thinking_style",
)


class L1Layout(BaseModel):
    """Per-slot list of placeholder names that the dispatch hub resolves
    when filling L1's PromptTemplate. Empty lists ⇒ slot uses only the
    template's static text.
    """

    model_config = ConfigDict(extra="forbid")

    persona: list[str] = Field(default_factory=list)
    task_intent: list[str] = Field(default_factory=list)
    problem_description: list[str] = Field(default_factory=list)
    thinking_style: list[str] = Field(default_factory=list)

    def all_placeholders(self) -> list[str]:
        """Flatten every slot's placeholders in slot-iteration order."""
        return [
            *self.persona,
            *self.task_intent,
            *self.problem_description,
            *self.thinking_style,
        ]

    def slot(self, name: str) -> list[str]:
        """Return the placeholder list for *name*; raise on unknown slot."""
        if name not in L1_LAYOUT_SLOTS:
            raise KeyError(f"Unknown L1 layout slot: {name}")
        return cast("list[str]", getattr(self, name))


def default_l1_layout() -> L1Layout:
    """Origin layout — `task_context` in `task_intent` (LLM front-of-mind), parent prompt + plan +
    evidence in `problem_description`. `origin_strengths` + `intractable_samples` are the
    trajectory-memory pair (kept hits next to the miss cluster).
    """
    return L1Layout(
        task_intent=["task_context"],
        problem_description=[
            "rendered_prompt",
            "pipeline_param_catalogue",
            "plan",
            "diagnostics",
            "escalation_panel",
            "l1_wounds",
            "critique",
            "axis_memory",
            "archive_top_runs",
            "rare_hit_samples",
            "origin_strengths",
        ],
    )


# Import-time exhaustiveness — the structural contract `validate_l1_layout`
# enforces per round, asserted once at module load so a drift in the constants
# or the origin layout fails at the source (no standalone wiring test):
#   * every mandatory placeholder is a possible one,
#   * the origin layout only references possible placeholders, and
#   * the origin layout already satisfies the mandatory set.
_default_placeholders = set(default_l1_layout().all_placeholders())
assert L1_POSSIBLE >= L1_MANDATORY, "L1_MANDATORY must be a subset of L1_POSSIBLE"
assert _default_placeholders <= L1_POSSIBLE, (
    "default_l1_layout references a placeholder absent from L1_POSSIBLE"
)
assert _default_placeholders >= L1_MANDATORY, (
    "default_l1_layout must reference every L1_MANDATORY placeholder"
)
del _default_placeholders


def coerce_l1_layout(raw_layout: Any) -> L1Layout | None:
    """Coerce `{slot: [name…]}` → L1Layout. `{}` is the sanctioned omit-sentinel ("keep current
    layout") — driver treats None as "no layout proposed". Malformed non-empty input also returns
    None so the validator (not this coercer) surfaces failures.
    """
    if not isinstance(raw_layout, dict) or not raw_layout:
        return None
    sanitised: dict[str, list[str]] = {}
    for slot in L1_LAYOUT_SLOTS:
        vals = raw_layout.get(slot)
        if isinstance(vals, list) and all(isinstance(v, str) for v in vals):
            sanitised[slot] = list(vals)
    if not sanitised:
        return None
    try:
        return L1Layout(**sanitised)
    except Exception:
        return None


class LayoutValidationResult:
    """L1-layout validation result. `is_valid=False` ⇒ HARD failure → rollback to prior; outcomes
    surface to L2's next fire either way as self-healing evidence.
    """

    __slots__ = ("is_valid", "outcomes")

    def __init__(self, is_valid: bool, outcomes: list[ValidatorOutcome]) -> None:
        self.is_valid = is_valid
        self.outcomes = outcomes


def validate_l1_layout(
    layout: L1Layout,
    *,
    prior_layout: L1Layout | None = None,
) -> LayoutValidationResult:
    """Run deterministic layout checks. HARD failures flip ``is_valid``."""
    outcomes: list[ValidatorOutcome] = []
    is_valid = True

    used = set(layout.all_placeholders())

    # HARD: every mandatory placeholder must be referenced somewhere.
    missing = L1_MANDATORY - used
    if missing:
        outcomes.append(
            ValidatorOutcome(
                validator_id="l1_layout_missing_mandatory",
                evidence={"missing": sorted(missing)},
            )
        )
        is_valid = False

    # HARD: every placeholder must be in L1_POSSIBLE.
    unknown = used - L1_POSSIBLE
    if unknown:
        outcomes.append(
            ValidatorOutcome(
                validator_id="l1_layout_unknown_placeholder",
                evidence={"unknown": sorted(unknown)},
            )
        )
        is_valid = False

    # HARD: no slot may list the same placeholder twice (renderer would emit it twice).
    for slot_name in L1_LAYOUT_SLOTS:
        slot_vals = getattr(layout, slot_name)
        if len(slot_vals) != len(set(slot_vals)):
            dups = sorted({n for n in slot_vals if slot_vals.count(n) > 1})
            outcomes.append(
                ValidatorOutcome(
                    validator_id="l1_layout_dups_within_slot",
                    evidence={"slot": slot_name, "duplicates": dups},
                )
            )
            is_valid = False

    # SOFT: unchanged from prior — L2 spent a fire on nothing. Flag, don't block.
    if prior_layout is not None and layout == prior_layout:
        outcomes.append(
            ValidatorOutcome(
                validator_id="l1_layout_unchanged_from_prior",
                evidence={"slots": list(L1_LAYOUT_SLOTS)},
            )
        )

    return LayoutValidationResult(is_valid=is_valid, outcomes=outcomes)


__all__ = [
    "L1_LAYOUT_SLOTS",
    "L1_MANDATORY",
    "L1_POSSIBLE",
    "L1Layout",
    "LayoutValidationResult",
    "coerce_l1_layout",
    "default_l1_layout",
    "validate_l1_layout",
]
