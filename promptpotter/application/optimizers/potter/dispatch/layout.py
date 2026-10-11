from __future__ import annotations

from collections.abc import Mapping
from typing import Any, ClassVar

from pydantic import ConfigDict

from promptpotter.application.optimizer_manifest import declared_node_override
from promptpotter.application.optimizers.nodes import LlmNode
from promptpotter.application.optimizers.potter.records import L1Layout
from promptpotter.domain.opt_search_point import OptimizerPromptTemplate
from promptpotter.domain.strict_model import StrictModel
from promptpotter.domain.validators import ValidatorOutcome
from promptpotter.shared.hashing import shapes_optimizer_prompt

shapes_optimizer_prompt(__name__)

# A field L1 cannot operate without, or the SOLE carrier of a state L1 must not enter blind.
L1_MANDATORY: frozenset[str] = frozenset(
    {
        "plan",
        "task_context",
        "rendered_prompt",
        "pipeline_param_catalogue",
        "critique",
        "answer_distribution",
        "measurand",
        "confounds",
        # The one carrier of the parent's shots, which the rendered prompt leaves out.
        "demo_pool",
    }
)

# L2-internal signals are excluded, so L1 cannot see L2's own state.
L1_POSSIBLE: frozenset[str] = frozenset(
    {
        "measurand",
        "precision",
        "detectable_move",
        "sample_provenance",
        "confounds",
        "budget_state",
        "plan",
        "rendered_prompt",
        "pipeline_param_catalogue",
        "prompt_block_catalogue",
        "demo_pool",
        "diagnostics",
        "escalation_panel",
        "l1_wounds",
        "task_context",
        "critique",
        "answer_distribution",
        "failing_samples",
        "inner_narratives",
        "mutation_memory",
        "axis_memory",
        "origin_strengths",
        "archive_top_runs",
        "rare_hit_samples",
        "sample_transcripts",
    }
)

# IN RENDER ORDER; ``answer_format`` is omitted, carrying the output JSON schema the template owns.
L1_LAYOUT_SLOTS: tuple[str, ...] = tuple(L1Layout.model_fields)

VOLATILE_SLOT: str = L1_LAYOUT_SLOTS[-1]
"""The provider's prefix-cache boundary: a changing panel ahead of it voids the discount on every byte behind (`dispatch-hub.md` § L1 layout)."""

PREFIX_STABLE_PANELS: frozenset[str] = frozenset({"task_context"})
"""Membership is a claim about a WRITER, never a renderer: re-read both before adding a name (`dispatch-hub.md` § L1 layout)."""

_OPTIMIZER_ORDER = OptimizerPromptTemplate.RENDER_ORDER
assert _OPTIMIZER_ORDER[-1] == VOLATILE_SLOT, (
    f"the optimizer prompt must render {VOLATILE_SLOT!r} last — it is where every NODE_LAYOUTS "
    f"floor puts its evidence, so a field behind it can never sit in a provider's stable prefix."
)
assert [f for f in _OPTIMIZER_ORDER if f in L1_LAYOUT_SLOTS] == list(L1_LAYOUT_SLOTS), (
    f"L1Layout's fields {L1_LAYOUT_SLOTS} must be a subsequence of RENDER_ORDER "
    f"{_OPTIMIZER_ORDER} — reorder the fields, so 'ahead of the boundary' means one thing."
)


class NodeLayoutSpec(StrictModel):
    model_config = ConfigDict(frozen=True)

    possible: frozenset[str]
    mandatory: frozenset[str]
    floor: L1Layout
    # Only the DISCRETIONARY panels: the mandatory floor is the dataset's, so no whole-prompt ceiling.
    discretionary_chars: int
    # `compile_prompt` extras that are not signals; any other unknown token in a template body raises.
    caller_extras: frozenset[str] = frozenset()


# `checkin` is excluded: it runs around the loop. `floor` is what a normal campaign runs UNCHANGED.
NODE_LAYOUTS: dict[str, NodeLayoutSpec] = {
    # Floor order is load-bearing: the composition places in order and `sample_transcripts` takes the rest.
    "l1_generate": NodeLayoutSpec(
        # Room for two whole transcripts behind the frame.
        discretionary_chars=11_000,
        caller_extras=frozenset({"n_variants"}),
        possible=L1_POSSIBLE,
        mandatory=L1_MANDATORY,
        floor=L1Layout(
            # The one floor placement ahead of `VOLATILE_SLOT`, free because it is prefix-stable.
            task_intent=["task_context"],
            problem_description=[
                "rendered_prompt",
                "pipeline_param_catalogue",
                "measurand",
                "precision",
                "detectable_move",
                "confounds",
                "sample_provenance",
                "prompt_block_catalogue",
                "demo_pool",
                "plan",
                "answer_distribution",
                "critique",
                "failing_samples",
                "inner_narratives",
                "mutation_memory",
                "l1_wounds",
                "escalation_panel",
                "origin_strengths",
                "budget_state",
                "sample_transcripts",
            ],
        ),
    ),
    # Both raw sources sit on the floor: each renders only at the recursion level it means something.
    "l1_critique": NodeLayoutSpec(
        # A transcript is indivisible, so this alone decides how many fit; two, beside the frame.
        discretionary_chars=7_500,
        possible=frozenset(
            {
                "measurand",
                "precision",
                "detectable_move",
                "sample_provenance",
                "confounds",
                "evidence_health",
                "diagnostics",
                "mutation_memory",
                "sample_transcripts",
                "failing_samples",
                "inner_narratives",
                "l1_wounds",
                "rare_hit_samples",
                "axis_memory",
                "archive_top_runs",
                "origin_strengths",
            }
        ),
        mandatory=frozenset({"diagnostics"}),
        floor=L1Layout(
            problem_description=[
                "measurand",
                "confounds",
                "evidence_health",
                "diagnostics",
                "mutation_memory",
                "sample_transcripts",
                "failing_samples",
                "inner_narratives",
                "l1_wounds",
                "rare_hit_samples",
            ],
        ),
    ),
    # `task_context` is off the floor (L2 cannot write the framing); the directives are mandatory so no L4 edit severs the channel.
    "l2_context": NodeLayoutSpec(
        discretionary_chars=5_500,
        possible=frozenset(
            {
                "plan",
                "l3_to_l2_note",
                "rendered_prompt",
                "diagnostics",
                "evidence_health",
                "guard_breaches",
                "axis_memory",
                "archive_top_runs",
                "rare_hit_samples",
                "critique",
                "l1_overrides",
                "l1_layout",
                "task_context",
                "l1_signal_catalogue",
                "skill_tiers",
                "rebase_capability",
                "terminate_capability",
                "measurand",
                "precision",
                "confounds",
                "budget_state",
            }
        ),
        mandatory=frozenset(
            {
                "critique",
                "l1_signal_catalogue",
                "diagnostics",
                "rebase_capability",
                "terminate_capability",
            }
        ),
        floor=L1Layout(
            problem_description=[
                "measurand",
                "confounds",
                "budget_state",
                "plan",
                "l3_to_l2_note",
                "rendered_prompt",
                "diagnostics",
                "evidence_health",
                "guard_breaches",
                "axis_memory",
                "archive_top_runs",
                "rare_hit_samples",
                "critique",
                "l1_overrides",
                "l1_layout",
                "l1_signal_catalogue",
                "skill_tiers",
                "rebase_capability",
                "terminate_capability",
            ],
        ),
    ),
    "l3_plan": NodeLayoutSpec(
        discretionary_chars=6_400,
        possible=frozenset(
            {
                "plan",
                "task_context",
                "diagnostics",
                "l1_wounds",
                "axis_memory",
                "guard_breaches",
                "critique",
                "evidence_health",
                "archive_top_runs",
                "skill_tiers",
                "rebase_capability",
                "terminate_capability",
                "measurand",
                "precision",
                "confounds",
                "budget_state",
            }
        ),
        mandatory=frozenset(
            {
                "plan",
                "diagnostics",
                "rebase_capability",
                "terminate_capability",
            }
        ),
        floor=L1Layout(
            problem_description=[
                "measurand",
                "confounds",
                "budget_state",
                "plan",
                "diagnostics",
                "evidence_health",
                "l1_wounds",
                "axis_memory",
                "guard_breaches",
                "critique",
                "skill_tiers",
                "rebase_capability",
                "terminate_capability",
            ],
        ),
    ),
}


for _node, _spec in NODE_LAYOUTS.items():
    _floor_ph = set(_spec.floor.all_placeholders())
    assert _spec.mandatory <= _spec.possible, f"{_node}: mandatory ⊄ possible"
    assert len(_floor_ph) == len(_spec.floor.all_placeholders()), (
        f"{_node}: floor names a placeholder in two places"
    )
    assert _floor_ph <= _spec.possible, (
        f"{_node}: floor references a placeholder absent from possible"
    )
    assert _floor_ph >= _spec.mandatory, (
        f"{_node}: floor must reference every mandatory placeholder"
    )
    _early = {n for s in L1_LAYOUT_SLOTS[:-1] for n in _spec.floor.slot(s)} - PREFIX_STABLE_PANELS
    assert not _early, (
        f"{_node}: floor places {sorted(_early)} ahead of {VOLATILE_SLOT!r}, voiding the prefix "
        f"cache for every field behind it. Move it there, or — only if its text cannot change "
        f"within a run — add it to PREFIX_STABLE_PANELS with the writer that freezes it."
    )
del _node, _spec, _floor_ph, _early


def layout_json_schema(
    spec: NodeLayoutSpec, *, description: str, withheld: frozenset[str] = frozenset()
) -> dict[str, Any]:
    """``propertyNames`` + ``additionalProperties`` state each enum once: this schema is prompt text."""
    return {
        "type": "object",
        "description": description,
        "propertyNames": {"enum": sorted(spec.possible - withheld)},
        "additionalProperties": {"type": "string", "enum": list(L1_LAYOUT_SLOTS)},
    }


def unplaceable_edit(raw_layout: object) -> ValidatorOutcome | None:
    """ONE stray move refuses the whole edit: the rest would land as a layout nobody asked for."""
    if not raw_layout:
        return None
    asked = raw_layout if isinstance(raw_layout, dict) else {}
    stray = {
        str(name): str(slot)[:40]
        for name, slot in asked.items()
        if not (isinstance(name, str) and isinstance(slot, str) and slot in L1_LAYOUT_SLOTS)
    }
    if asked and not stray:
        return None
    return ValidatorOutcome(
        validator_id="l1_layout_unparseable",
        evidence={
            "not_a_slot": sorted(set(stray.values())),
            "asked_for_panel": sorted(stray),
            "slots": list(L1_LAYOUT_SLOTS),
        },
    )


def coerce_l1_layout(raw_layout: Any, *, base: L1Layout) -> L1Layout | None:
    """``None`` is "no edit asked". An unknown PANEL is placed, not dropped, so ``l1_layout_unknown_placeholder`` rolls the edit back."""
    if not raw_layout:
        return None
    moves: dict[str, str] = dict(raw_layout)
    update: dict[str, list[str]] = {}
    for slot in L1_LAYOUT_SLOTS:
        kept = [n for n in base.slot(slot) if moves.get(n, slot) == slot]
        update[slot] = kept + [n for n, s in moves.items() if s == slot and n not in kept]
    out = base.model_copy(update=update, deep=True)
    placed = out.all_placeholders()
    assert len(placed) == len(set(placed)), f"a layout edit placed one panel twice: {placed}"
    return out


class LayoutValidationResult:
    """What ``is_valid=False`` costs is the caller's: L2 keeps the prior layout, an L4 override is rejected at proposal."""

    __slots__ = ("is_valid", "outcomes")

    def __init__(self, is_valid: bool, outcomes: list[ValidatorOutcome]) -> None:
        self.is_valid = is_valid
        self.outcomes = outcomes


def validate_l1_layout(
    layout: L1Layout,
    *,
    spec: NodeLayoutSpec,
    prior_layout: L1Layout | None = None,
) -> LayoutValidationResult:
    outcomes: list[ValidatorOutcome] = []
    is_valid = True

    used = set(layout.all_placeholders())

    missing = spec.mandatory - used
    if missing:
        outcomes.append(
            ValidatorOutcome(
                validator_id="l1_layout_missing_mandatory",
                evidence={"missing": sorted(missing)},
            )
        )
        is_valid = False

    unknown = used - spec.possible
    if unknown:
        outcomes.append(
            ValidatorOutcome(
                validator_id="l1_layout_unknown_placeholder",
                evidence={"unknown": sorted(unknown)},
            )
        )
        is_valid = False

    # SOFT: costs money, not correctness, and placement is a real axis, so it reports and never rolls back.
    early = [
        name
        for slot in L1_LAYOUT_SLOTS[:-1]
        for name in layout.slot(slot)
        if name not in PREFIX_STABLE_PANELS
    ]
    if early:
        outcomes.append(
            ValidatorOutcome(
                validator_id="l1_layout_voids_prefix",
                evidence={"panels": early, "stable_slot": VOLATILE_SLOT},
            )
        )

    # SOFT: L2 spent a fire on nothing.
    if prior_layout is not None and layout == prior_layout:
        outcomes.append(
            ValidatorOutcome(
                validator_id="l1_layout_unchanged_from_prior",
                evidence={"slots": list(L1_LAYOUT_SLOTS)},
            )
        )

    return LayoutValidationResult(is_valid=is_valid, outcomes=outcomes)


def resolve_layout_override(
    node: str, raw_layout: object
) -> tuple[L1Layout, list[ValidatorOutcome]]:
    """ONE derivation for both boundaries (`validators/l1_strict.py` at proposal, this module at render), so they cannot disagree."""
    spec = NODE_LAYOUTS[node]
    if breach := unplaceable_edit(raw_layout):
        return spec.floor, [breach]
    merged = coerce_l1_layout(raw_layout, base=spec.floor)
    if merged is None:
        return spec.floor, []
    result = validate_l1_layout(merged, spec=spec)
    if not result.is_valid:
        return spec.floor, list(result.outcomes)
    return merged, []


def resolve_node_layout(node: str) -> L1Layout:
    """Raises rather than render the floor: that attributes the measurement to a layout nobody ran."""
    layout, breaches = resolve_layout_override(node, declared_node_override(node).get("layout"))
    if breaches:
        raise ValueError(
            f"resolve_node_layout({node!r}): the declared layout edit breaks "
            f"{sorted(o.validator_id for o in breaches)} and cannot be applied"
        )
    return layout


class LayoutNode(LlmNode):
    name: ClassVar[str]
    outer_levers: ClassVar[Mapping[str, str]] = {"layout": "object"}

    def resolved_levers(self, declared: Mapping[str, Any]) -> dict[str, Any]:
        return layout_levers(self.name, declared)


def layout_levers(node: str, declared: Mapping[str, Any]) -> dict[str, Any]:
    """Dropped where it resolves to the node's floor, so two declarations rendering one prompt hash alike."""
    spec = NODE_LAYOUTS[node]
    layout, _breaches = resolve_layout_override(node, declared.get("layout"))
    return {} if layout == spec.floor else {"layout": layout.model_dump(mode="json")}


__all__ = [
    "L1_LAYOUT_SLOTS",
    "L1_MANDATORY",
    "L1_POSSIBLE",
    "NODE_LAYOUTS",
    "PREFIX_STABLE_PANELS",
    "VOLATILE_SLOT",
    "LayoutNode",
    "NodeLayoutSpec",
    "coerce_l1_layout",
    "layout_json_schema",
    "layout_levers",
    "resolve_layout_override",
    "resolve_node_layout",
    "unplaceable_edit",
    "validate_l1_layout",
]
