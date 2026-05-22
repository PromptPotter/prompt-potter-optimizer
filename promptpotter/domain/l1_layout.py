"""L1 layout — L2's mutation surface for L1_GENERATE.

A layout is a per-prompt-slot list of placeholder names. The dispatch hub
resolves each placeholder to a registered signal renderer when it fills
L1's PromptTemplate. ``answer_format`` is template-fixed (output schema
is a code contract, not L2's call) and absent from the layout.

Lives on ``OptSearchPoint.l1_layout``. L2 mutates it every fire to tune
what L1 sees. Most fires won't touch the layout — L2 only refines
``task_context`` and the layout stays at its prior valid value.

Layout validation is split into HARD and SOFT outcomes
(:func:`validate_l1_layout`):

* HARD — mandatory missing / unknown placeholder / dups within slot →
  rollback to prior layout, append ``ValidatorOutcome`` to
  ``opt_sp.wounds.l2_guard_breaches`` for self-healing next L2 fire.
* SOFT — layout unchanged from prior → apply with warning logged.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from promptpotter.domain.validators import ValidatorOutcome

# Names every valid L1 layout MUST reference somewhere across its slots.
# These are the cross-layer protocol fields plus the parent prompt,
# mutation surface, and last-round critique — without them L1 can't
# operate. ``task_context`` is the broadcast L2-channel (persistent task
# framing, refined each L2 fire). ``critique`` is the last L1_CRITIQUE
# output: the round-local evidence digest L1 grounds its next variants
# in. Dropping critique from the layout fires
# ``l1_layout_missing_mandatory`` with ``nurse_target='l3'`` — L3
# replans rather than letting L2 starve L1 of failure context.
L1_MANDATORY: frozenset[str] = frozenset(
    {"plan", "task_context", "rendered_prompt", "pipeline_param_catalogue", "critique"}
)

# Names L2 may pick from when authoring an L1 layout. Subset of the
# global signal registry — L2-internal signals (e.g. ``l1_overrides``,
# ``l1_signal_catalogue``) are deliberately excluded so L1 can't see
# L2's own state.
L1_POSSIBLE: frozenset[str] = frozenset(
    {
        "plan",
        "rendered_prompt",
        "pipeline_param_catalogue",
        "diagnostics",
        "validation_failures",
        "runtime_failures",
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
        return getattr(self, name)


def default_l1_layout() -> L1Layout:
    """Return the origin L1 layout used before any L2 fire mutates it.

    Mandatory placeholders are spread across ``task_intent`` (the
    persistent task framing L2 refines — front of mind for the LLM) and
    ``problem_description`` (parent prompt + mutation surface + plan +
    post-scoring evidence). ``origin_strengths`` + ``intractable_samples``
    are the trajectory-memory pair — origin's preserved hits next to the
    cumulative miss cluster L1 must attack, both in
    ``problem_description`` where the failure evidence already lives.
    """
    return L1Layout(
        task_intent=["task_context"],
        problem_description=[
            "rendered_prompt",
            "pipeline_param_catalogue",
            "plan",
            "diagnostics",
            "validation_failures",
            "runtime_failures",
            "critique",
            "axis_memory",
            "archive_top_runs",
            "rare_hit_samples",
            "origin_strengths",
        ],
    )


def coerce_l1_layout(raw_layout: Any) -> L1Layout | None:
    """Best-effort coerce ``{slot: [placeholder, …]}`` → :class:`L1Layout`.

    Returns ``None`` when the input is empty (``{}``) or shaped wrong.
    An empty dict is the **sanctioned omit-sentinel** for L2 LLM
    output (L2's prompt accepts both omit and ``{}`` as "keep current
    layout") — the L2 driver treats ``None`` as "no layout proposed"
    and skips validation. Non-empty but malformed input also returns
    ``None`` so the validator surfaces mandatory-presence /
    unknown-name failures uniformly rather than crashing here.
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
    """Bundle of outcomes from validating an L2-proposed L1 layout.

    ``is_valid`` False ⇒ at least one HARD failure fired; the caller
    must rollback to the prior valid layout. Outcomes are recorded
    regardless and surface to L2's next fire as self-healing evidence.
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
                passed=False,
                score=0.0,
                evidence={"missing": sorted(missing)},
                nurse_target="l3",
            )
        )
        is_valid = False

    # HARD: every placeholder must be in L1_POSSIBLE.
    unknown = used - L1_POSSIBLE
    if unknown:
        outcomes.append(
            ValidatorOutcome(
                validator_id="l1_layout_unknown_placeholder",
                passed=False,
                score=0.0,
                evidence={"unknown": sorted(unknown)},
                nurse_target="l3",
            )
        )
        is_valid = False

    # HARD: no slot may list the same placeholder twice (the renderer
    # would emit it twice — wasted tokens).
    for slot_name in L1_LAYOUT_SLOTS:
        slot_vals = getattr(layout, slot_name)
        if len(slot_vals) != len(set(slot_vals)):
            dups = sorted({n for n in slot_vals if slot_vals.count(n) > 1})
            outcomes.append(
                ValidatorOutcome(
                    validator_id="l1_layout_dups_within_slot",
                    passed=False,
                    score=0.0,
                    evidence={"slot": slot_name, "duplicates": dups},
                    nurse_target="l3",
                )
            )
            is_valid = False

    # SOFT: layout proposed but unchanged from prior. L2 spent a fire
    # without changing anything — flag, don't block.
    if prior_layout is not None and layout == prior_layout:
        outcomes.append(
            ValidatorOutcome(
                validator_id="l1_layout_unchanged_from_prior",
                passed=False,
                score=0.5,
                evidence={"slots": list(L1_LAYOUT_SLOTS)},
                nurse_target="l3",
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
