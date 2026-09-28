"""Per-node prompt layout — potter's information-flow axis, and the SINGLE source for which
signals reach each of its optimizer prompts. There is no second ``{{token}}`` source in the
templates."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Literal

from pydantic import ConfigDict

from promptpotter.application.optimizer_manifest import declared_node_override
from promptpotter.domain.opt_search_point import OptimizerPromptTemplate
from promptpotter.domain.optimizer_state import L1Layout
from promptpotter.domain.strict_model import StrictModel
from promptpotter.domain.validators import ValidatorOutcome
from promptpotter.shared.hashing import shapes_optimizer_prompt

shapes_optimizer_prompt(__name__)

# What every L1 layout must carry: a field L1 cannot OPERATE without, or the sole carrier of a state
# L1 must not enter blind — `answer_distribution` is the second kind, and it self-suppresses where
# there is no label to be constant about, so it costs nothing on the runs it cannot speak about.
# An edit only MOVES panels (`coerce_l1_layout`), so a layout lacking one can only be authored, and
# `l1_layout_missing_mandatory` rolls it back rather than starving L1.
L1_MANDATORY: frozenset[str] = frozenset(
    {
        "plan",
        "task_context",
        "rendered_prompt",
        "pipeline_param_catalogue",
        "critique",
        "answer_distribution",
        # Also the second kind: `measurand` names the number every variant is judged on — without
        # it an edit optimizes a column rather than an objective — and `confounds` is the sole
        # carrier of the states where that number is not ability.
        "measurand",
        "confounds",
        # The one carrier of the parent's shots, which the rendered prompt leaves out; silent
        # without a demo pool, so it costs nothing where there are none to edit.
        "demo_pool",
    }
)

# Names L2 may pick from. Subset of the global registry; L2-internal signals are excluded so
# L1 can't see L2's own state.
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

# PromptTemplate slots a layout addresses, IN RENDER ORDER: `L1Layout`'s fields, asserted below to
# be a subsequence of `OptimizerPromptTemplate.RENDER_ORDER`. ``answer_format`` is omitted — it
# carries the output JSON schema and is owned by the template, not L2.
#
# The order is what makes `VOLATILE_SLOT` readable below: the slots ahead of it are the ones a
# panel voids the provider's prefix cache from.
L1_LAYOUT_SLOTS: tuple[str, ...] = tuple(L1Layout.model_fields)

VOLATILE_SLOT: str = L1_LAYOUT_SLOTS[-1]
"""The slot behind the provider's prefix-cache boundary — the LAST thing an optimizer prompt
renders (`OptimizerPromptTemplate.RENDER_ORDER`), which is why every floor below puts its evidence there.

An implicit prefix cache hits on an identical LEADING byte range, so a panel whose text CHANGES
between rounds voids the discount on every byte after it. Ahead of this slot that means the static
template itself — instruction, thinking_style, answer_format — is re-billed at full rate every
round, for a panel that had to be re-sent anyway."""

PREFIX_STABLE_PANELS: frozenset[str] = frozenset({"task_context"})
"""Panels that may sit ahead of :data:`VOLATILE_SLOT` for free, because their text does not change
from round to round — so the shared prefix survives them.

**Membership is a claim about a WRITER, not about a renderer**, and there is exactly one today:
`_r_task_context` renders the operator's framing, which no layer writes, so it never moves within
a run. Every other panel is derived from measurement and moves whenever the measurement does.

This list is what keeps :func:`validate_l1_layout`'s prefix check silent on the floors while still
catching an EDIT that walks a live panel forward — L2 addresses any of the four slots
(`layout_json_schema`), so without it the placement axis can spend the discount with nothing
reporting that it did. Adding a name here means re-reading its renderer and its writer; a panel
that is merely stable TODAY is not stable, it is untested."""

# Both load-bearing: without the second, `L1_LAYOUT_SLOTS[:-1]` is not "the slots ahead of the
# boundary" and `validate_l1_layout`'s prefix check reads the wrong ones.
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
    """One optimizer node's searchable injection axis. ``mandatory`` is the guard rail.

    ``editor`` is READ, not decoration — ``l2`` is ``l1_generate`` alone, and NO path applies an L4
    layout override to it. A node's live layout comes from ``prompts.py::node_layout``; a caller
    branching on ``editor`` for that re-derives the function badly."""

    model_config = ConfigDict(frozen=True)

    editor: Literal["l2", "l4"]
    possible: frozenset[str]
    mandatory: frozenset[str]
    floor: L1Layout


# The per-node layout registry — L4's information-flow surface. The dispatch hub fills every
# node from here, so the set of signals reaching each optimizer prompt is ONE searched axis
# rather than two hand-tuned sources. `checkin` is excluded: it runs around the loop.
#
# `mandatory` = the GUARD RAIL every layout of the node carries, deliberately minimal. `floor` = the
# good default a normal campaign runs on UNCHANGED — these govern every campaign's inner prompts,
# and a normal campaign has no outer loop to reconverge, so the floor must be good rather than
# merely not-terrible. `possible − mandatory` = L4's search space, scored by the same proxy as
# any other mutation.
NODE_LAYOUTS: dict[str, NodeLayoutSpec] = {
    # Order is load-bearing. `answer_distribution` leads because it frames everything after
    # it: a pipeline collapsed onto one label needs that break, not a better-argued
    # instruction, and no other panel can say so. Then the DISTILLED failure signal, then the
    # misses themselves ordered by difficulty — the evidence beside its own compression, so
    # the generator can check one against the other — then what it has ALREADY tried, without
    # which round 4 re-proposes round 1's measured failure and nothing objects.
    # `sample_transcripts` stays OFF this floor: the same evidence at several times the bytes,
    # duplicating a large payload every round. It stays in `L1_POSSIBLE` and on
    # `l1_critique`'s floor, so L2 re-adds it on stall — when a reasoning-MECHANISM error
    # needs the model's own trace — and L4 can search it back in. Raw `diagnostics` and the
    # cross-run panels are off the floor for the same reason.
    "l1_generate": NodeLayoutSpec(
        editor="l2",
        possible=L1_POSSIBLE,
        mandatory=L1_MANDATORY,
        floor=L1Layout(
            # The one floor placement ahead of `VOLATILE_SLOT`, and it is free: `task_context` is
            # in `PREFIX_STABLE_PANELS`, whose docstring names the one way it moves. Anything NOT
            # on that list belongs behind the boundary.
            task_intent=["task_context"],
            problem_description=[
                "rendered_prompt",
                "measurand",
                "precision",
                "detectable_move",
                "confounds",
                "sample_provenance",
                "pipeline_param_catalogue",
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
            ],
        ),
    ),
    # The distiller — the one node where a raw dump is justified, since everything downstream
    # reads its compression. `diagnostics` alone shows truncated stems, which starves the
    # critique into unverifiable steers.
    # WHICH raw source depends on the level, so both sit on the floor and each renders only
    # where it means something: a miss selects `sample_transcripts`, while one level up a miss
    # is a placeholder-label artifact and the raw source is `inner_narratives`. A critique
    # shown neither prescribes steers the inner loops have already measured and lost.
    # `failing_samples` carries the BREADTH both lack — the deep panels reach ~5 misses of ~20,
    # and clusters ranked "largest first, share of the misses" cannot be read off a sample.
    # `mutation_memory` because the round under critique IS a set of edits: which cells the
    # parent's run hit that they keep missing is a failure no miss panel can carry.
    "l1_critique": NodeLayoutSpec(
        editor="l4",
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
    # Framing layer — must see the distilled failure signal, its own edit vocabulary and the
    # raw round evidence. `task_context` is off the floor: L2 cannot write the framing, so a
    # mandatory rail here would guard a capability that does not exist while admitting static
    # text identical on every fire. It stays in `possible`, an axis L4 can search back in.
    # The two layer-control directives are mandatory, so no L4 edit can sever the channel —
    # the sanctioned off-switch stays the config bit, which renders them empty.
    "l2_context": NodeLayoutSpec(
        editor="l4",
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
                # The frame leads: L2 holds terminate authority, and a layer deciding whether a
                # fault is recoverable must read the objective and its caveats before the evidence.
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
                # Both levers' CURRENT state, side by side — an edit needs to read what it lands on.
                "l1_overrides",
                "l1_layout",
                "l1_signal_catalogue",
                "rebase_capability",
                "terminate_capability",
            ],
        ),
    ),
    # Strategic replan — must see the `plan` it rewrites and the raw evidence. `task_context`
    # is off the floor for the same reason as `l2_context` above. `evidence_health` earns its
    # place: the per-node failure rates L3 needs to judge a fault unrecoverable.
    "l3_plan": NodeLayoutSpec(
        editor="l4",
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
                # Same reason as L2: a replan that may terminate the cycle reads the objective and
                # its caveats first, or it argues about a number it was never shown the shape of.
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
                "rebase_capability",
                "terminate_capability",
            ],
        ),
    ),
}


def default_l1_layout() -> L1Layout:
    """Origin layout for ``l1_generate``, deep-copied so the OSP's mutable per-slot lists never alias the
    shared floor."""
    return NODE_LAYOUTS["l1_generate"].floor.model_copy(deep=True)


# Import-time exhaustiveness — the same structural contract `validate_l1_layout` enforces per
# edit, asserted at module load so a drift in any node's spec fails at the source. The
# registry-membership half lives in `injection_table()`; this half is pure set algebra.
for _node, _spec in NODE_LAYOUTS.items():
    _floor_ph = set(_spec.floor.all_placeholders())
    assert _spec.mandatory <= _spec.possible, f"{_node}: mandatory ⊄ possible"
    # An EDIT cannot name a panel twice — a panel addresses one slot — so the floor is the only
    # producer left that could, and `DispatchHub.fill` renders one copy per occurrence.
    assert len(_floor_ph) == len(_spec.floor.all_placeholders()), (
        f"{_node}: floor names a placeholder in two places"
    )
    assert _floor_ph <= _spec.possible, (
        f"{_node}: floor references a placeholder absent from possible"
    )
    assert _floor_ph >= _spec.mandatory, (
        f"{_node}: floor must reference every mandatory placeholder"
    )
    # No floor may cost the prefix. `PREFIX_STABLE_PANELS` is the exemption and it is short on
    # purpose, so this is what stops the list growing to fit a floor rather than the other way
    # round — and it is why `validate_l1_layout`'s prefix check is silent on an unedited run.
    _early = {n for s in L1_LAYOUT_SLOTS[:-1] for n in _spec.floor.slot(s)} - PREFIX_STABLE_PANELS
    assert not _early, (
        f"{_node}: floor places {sorted(_early)} ahead of {VOLATILE_SLOT!r}, voiding the prefix "
        f"cache for every field behind it. Move it there, or — only if its text cannot change "
        f"within a run — add it to PREFIX_STABLE_PANELS with the writer that freezes it."
    )
del _node, _spec, _floor_ph, _early


def layout_json_schema(spec: NodeLayoutSpec, *, description: str) -> dict[str, Any]:
    """The wire shape of a layout edit against ``spec``: a panel name addresses the ONE slot it fills.
    ONE builder for BOTH seams that offer the edit — L4's per-node ``layout`` param and L2's
    ``l1_layout`` — because a vocabulary the emitter is never shown is not a vocabulary.

    ``propertyNames`` + ``additionalProperties`` state each enum once. Per-slot arrays restate the
    signal enum for every slot of every node, and this schema is prompt text on each call.

    ``description`` is passed rather than written here: the wire schema's prose lives in ``bundle``
    (`LAYOUT_SCHEMA_INSTRUCTION`), beside every other description it sends."""
    return {
        "type": "object",
        "description": description,
        "propertyNames": {"enum": sorted(spec.possible)},
        "additionalProperties": {"type": "string", "enum": list(L1_LAYOUT_SLOTS)},
    }


def coerce_l1_layout(raw_layout: Any, *, base: L1Layout) -> L1Layout | None:
    """Apply a ``{panel: slot}`` EDIT onto ``base``, or ``None`` for BOTH "no edit asked" (``{}``, the
    sanctioned omit-sentinel) and "edit asked in a shape no slot can hold". The CALLER separates the
    two off the raw input; this returns no outcome of its own, because a coercer that judged would be
    a second validator.

    A panel is MOVED, so it reaches at most one slot and a duplicate has no shape to arrive in. One
    the edit does not name keeps its slot AND its position — the floor's order is authored and
    load-bearing, so only a moved panel is repositioned, to the end of the slot it moves to. An
    unknown PANEL is placed rather than dropped, so ``l1_layout_unknown_placeholder`` rolls the edit
    back instead of the floor surviving in silence."""
    if not isinstance(raw_layout, dict) or not raw_layout:
        return None
    moves = {
        name: slot
        for name, slot in raw_layout.items()
        if isinstance(name, str) and isinstance(slot, str) and slot in L1_LAYOUT_SLOTS
    }
    if not moves:
        return None
    update: dict[str, list[str]] = {}
    for slot in L1_LAYOUT_SLOTS:
        kept = [n for n in base.slot(slot) if moves.get(n, slot) == slot]
        update[slot] = kept + [n for n, s in moves.items() if s == slot and n not in kept]
    out = base.model_copy(update=update, deep=True)
    # "A panel is MOVED" is the whole contract above, and a panel placed twice is rendered twice
    # into one prompt with no validator between here and the wire. Asserted over every real edit
    # rather than over a test's enumeration of them.
    placed = out.all_placeholders()
    assert len(placed) == len(set(placed)), f"a layout edit placed one panel twice: {placed}"
    return out


class LayoutValidationResult:
    """L1-layout validation result. What `is_valid=False` COSTS is the caller's, and the two differ:
    L2's edit of `l1_generate` keeps the prior layout, so the fire is spent but the cycle runs on;
    an L4 override is rejected at proposal (`l1_inner_layout_applies`), because substituting the
    floor there would spend a whole inner campaign measuring the parent's information flow and
    report it as the edit's own reading. Outcomes surface as self-healing evidence either way."""

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
    """Deterministic layout checks against a node's ``spec``; what a HARD failure costs is the
    caller's, and ``LayoutValidationResult`` states the two."""
    outcomes: list[ValidatorOutcome] = []
    is_valid = True

    used = set(layout.all_placeholders())

    # HARD: every mandatory placeholder must be referenced somewhere.
    missing = spec.mandatory - used
    if missing:
        outcomes.append(
            ValidatorOutcome(
                validator_id="l1_layout_missing_mandatory",
                evidence={"missing": sorted(missing)},
            )
        )
        is_valid = False

    # HARD: every placeholder must be in the node's `possible` set.
    unknown = used - spec.possible
    if unknown:
        outcomes.append(
            ValidatorOutcome(
                validator_id="l1_layout_unknown_placeholder",
                evidence={"unknown": sorted(unknown)},
            )
        )
        is_valid = False

    # SOFT: a panel whose text moves between rounds, placed ahead of the prefix boundary. Costs
    # money, not correctness — placement is a real axis and an edit may be worth its discount — so
    # this reports rather than rolls back, and the report is the whole point: the discount is
    # otherwise spent with nothing able to say it was.
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

    # SOFT: unchanged from prior — L2 spent a fire on nothing. Flag, don't block.
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
    """One node's floor with an L4 ``{panel: slot}`` edit applied, and the outcomes that edit
    breaks — empty on a clean apply, where the returned layout is what the inner cycle renders.

    ONE derivation asked at two boundaries. `validators/l1_strict.py` convicts the PROPOSAL, where
    the arm can be told and costs a synthetic 0; this module re-asks at render time, one recursion
    level down, where nothing can be told and the arm has already paid for a whole inner campaign.
    Two derivations would let the boundary that rejects and the boundary that applies disagree
    about which edits are legal."""
    spec = NODE_LAYOUTS[node]
    # The `editor` field is a contract, so it is asked rather than assumed. `l1_generate`'s
    # layout is L2's in-campaign surface (`PotterState.memory.l1_layout`) and nothing here applies
    # to it — reaching this with that node means a caller believes in an L4 lever that has no
    # code path, and silence would let the belief survive.
    if spec.editor != "l4":
        raise ValueError(
            f"resolve_layout_override({node!r}): this node's layout is edited by {spec.editor!r}, "
            "not L4. Only `editor='l4'` nodes resolve a layout through the per-node override "
            "channel; l1_generate's rides PotterState.memory.l1_layout instead."
        )
    merged = coerce_l1_layout(raw_layout, base=spec.floor)
    if merged is None:
        # Absent is "no layout edit"; a non-empty declaration that coerces to nothing asked for one
        # in a shape no slot can hold. Both land here, and treating them alike is the defect
        # `escalation/firing.py::_parse_l2` already carries the L2 twin of — `l1_layout_unparseable`
        # is that arm's id, shared so one shape cannot be a breach on one path and silence on the other.
        if not raw_layout:
            return spec.floor, []
        return spec.floor, [
            ValidatorOutcome(
                validator_id="l1_layout_unparseable",
                evidence={"keys": sorted(raw_layout) if isinstance(raw_layout, dict) else []},
            )
        ]
    result = validate_l1_layout(merged, spec=spec)
    if not result.is_valid:
        return spec.floor, list(result.outcomes)
    return merged, []


def resolve_node_layout(node: str) -> L1Layout:
    """The layout this node renders under. A declaration that does not apply RAISES: an L1 proposal
    is convicted upstream by `l1_inner_layout_applies`, so what reaches here is operator-authored,
    and rendering the floor for it would attribute the measurement to a layout nobody ran."""
    layout, breaches = resolve_layout_override(node, declared_node_override(node).get("layout"))
    if breaches:
        raise ValueError(
            f"resolve_node_layout({node!r}): the declared layout edit breaks "
            f"{sorted(o.validator_id for o in breaches)} and cannot be applied"
        )
    return layout


def layout_levers(node: str, declared: Mapping[str, Any]) -> dict[str, Any]:
    """``PotterRuntime.override_levers``: the layout an L4 declaration RESOLVES to, dropped where it
    lands back on the node's floor — so two declarations rendering one prompt hash alike."""
    spec = NODE_LAYOUTS.get(node)
    if spec is None or spec.editor != "l4":
        return {}
    layout, _breaches = resolve_layout_override(node, declared.get("layout"))
    return {} if layout == spec.floor else {"layout": layout.model_dump(mode="json")}


__all__ = [
    "L1_LAYOUT_SLOTS",
    "L1_MANDATORY",
    "L1_POSSIBLE",
    "NODE_LAYOUTS",
    "PREFIX_STABLE_PANELS",
    "VOLATILE_SLOT",
    "NodeLayoutSpec",
    "coerce_l1_layout",
    "default_l1_layout",
    "layout_json_schema",
    "layout_levers",
    "resolve_layout_override",
    "resolve_node_layout",
    "validate_l1_layout",
]
