"""Per-node prompt layout — the optimizer's information-flow axis, and the SINGLE source for which
signals reach each optimizer prompt. There is no second ``{{token}}`` source in the templates."""

from __future__ import annotations

from typing import Any, Literal, cast

from pydantic import ConfigDict, Field, field_serializer

from promptpotter.domain.strict_model import StrictModel
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
        "prompt_block_catalogue",
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

# PromptTemplate slots a layout addresses. ``answer_format`` is omitted —
# it carries the output JSON schema and is owned by the template, not L2.
L1_LAYOUT_SLOTS: tuple[str, ...] = (
    "persona",
    "task_intent",
    "problem_description",
    "thinking_style",
)


class L1Layout(StrictModel):
    """Per-slot list of placeholder names that the dispatch hub resolves
    when filling L1's PromptTemplate. Empty lists ⇒ the slot's static text only."""

    persona: list[str] = Field(default_factory=list)
    task_intent: list[str] = Field(default_factory=list)
    problem_description: list[str] = Field(default_factory=list)
    thinking_style: list[str] = Field(default_factory=list)

    def all_placeholders(self) -> list[str]:
        return [
            *self.persona,
            *self.task_intent,
            *self.problem_description,
            *self.thinking_style,
        ]

    def slot(self, name: str) -> list[str]:
        if name not in L1_LAYOUT_SLOTS:
            raise KeyError(f"Unknown L1 layout slot: {name}")
        return cast("list[str]", getattr(self, name))


class NodeLayoutSpec(StrictModel):
    """One optimizer node's searchable injection axis. ``editor`` is READ, not decoration — ``l2`` is
    ``l1_generate`` alone, and NO path applies an L4 layout override to it. ``mandatory`` is the guard rail."""

    model_config = ConfigDict(frozen=True)

    editor: Literal["l2", "l4"]
    possible: frozenset[str]
    mandatory: frozenset[str]
    floor: L1Layout

    @field_serializer("possible", "mandatory")
    def _sorted_names(self, names: frozenset[str]) -> list[str]:
        # Pydantic dumps a frozenset in ITERATION order, which for str is
        # PYTHONHASHSEED-randomized — so an unsorted dump differs every process.
        # `_identity_config` hashes this dump, so that made the L4 measurement
        # identity non-deterministic and no cached origin could ever be reused.
        return sorted(names)


# The per-node layout registry — L4's information-flow surface. Each optimizer node
# owns its injection set here (was: `l1_generate` in `default_l1_layout()` Python +
# every other node hardcoded as `{{tokens}}` in `promptpotter/assets/optimizer/pipeline.yaml`).
# The dispatch hub fills every node from `NODE_LAYOUTS[node]` (floor ± editor edits),
# so the set of signals reaching each optimizer prompt is one searched axis, not two
# hand-tuned sources. `checkin` is excluded — it runs around the loop, not through
# the injection path (§5 non-goal). Floor order == the prompt's prior `{{token}}`
# order so the collapse stays rendering-identical.
#
# `mandatory` = the GUARD RAIL (L4 may never excise — the node's can't-function-without
# inputs, deliberately minimal). `floor` = the good default a normal campaign runs on
# UNCHANGED (these floors govern every campaign's inner prompts, not only L4 runs, and a
# normal campaign has no outer loop to reconverge — so the floor is a good default, not
# just not-terrible). `possible − mandatory` = L4's search space: it may excise a
# non-mandatory floor signal or insert a possible-but-off-floor one, scored by the same
# proxy as any other mutation. Only change vs. pre-slice-6 behavior: `l1_generate` regains
# `origin_strengths` on its floor — the anti-regression rail `critique` (failure-focused)
# does not carry (the layout-trim review's one restore).
NODE_LAYOUTS: dict[str, NodeLayoutSpec] = {
    # `task_context` sits in `task_intent` (LLM front-of-mind); `problem_description`
    # carries the mandatory structural state, then the evidence: WHAT THE PIPELINE ANSWERS
    # (`answer_distribution` — first, because it frames everything after it: a pipeline that
    # has collapsed onto one label needs that break, not a better-argued instruction, and no
    # other panel can say so), the DISTILLED failure signal (`critique`), the misses themselves
    # (`failing_samples`, one line each, ordered by difficulty — the evidence sits beside its
    # own compression, so the generator can check one against the other), the per-sample RAW
    # narrative when the sample is itself an inner campaign (`inner_narratives` — the L4 outer
    # generator's window into what each inner loop tried and where it stalled; silent off the
    # recursion), what it has ALREADY tried (`mutation_memory` — without it, round 4 re-proposes
    # round 1's measured failure and nothing objects) + L1's own `l1_wounds` + `escalation_panel`
    # + `origin_strengths`.
    # `sample_transcripts` (the RAW misses, three shown COMPLETE) stays OFF this
    # floor: it is the same evidence at ~5x the bytes, and the generator reading it alongside
    # the critique duplicated a ~10k-char payload every round. It stays in `L1_POSSIBLE` (and
    # on `l1_critique`'s floor, the distiller's raw source), so L2 re-adds it on stall — when
    # a reasoning-MECHANISM error needs the model's own trace, which no one-liner can carry —
    # and L4 can search it back in. Raw `diagnostics` and the cross-run panels stay off the
    # floor for the same reason; L2 adds them on stall via its layout edit, L4 optimises that.
    "l1_generate": NodeLayoutSpec(
        editor="l2",
        possible=L1_POSSIBLE,
        mandatory=L1_MANDATORY,
        floor=L1Layout(
            task_intent=["task_context"],
            problem_description=[
                "rendered_prompt",
                "pipeline_param_catalogue",
                "prompt_block_catalogue",
                "plan",
                "answer_distribution",
                "critique",
                "failing_samples",
                "inner_narratives",
                "mutation_memory",
                "l1_wounds",
                "escalation_panel",
                "origin_strengths",
            ],
        ),
    ),
    # The distiller — the one node where a raw dump is justified (everything downstream
    # reads its compression, so it must read the raw source); hence `diagnostics` is its
    # sole mandatory. `sample_transcripts` carries the COMPLETE failing samples (full
    # query + the model's own reasoning) — `diagnostics` alone shows truncated stems,
    # which starved the critique into unverifiable steers. Optional search space adds
    # the cross-run panels.
    # WHICH raw source depends on the level, so both sit on the floor and each renders
    # only where it means something: a miss selects `sample_transcripts`, and one level up
    # a miss is a placeholder-label artifact the outer critique is told to ignore — there
    # the raw source is `inner_narratives`, the same stories ordered by lift and paired
    # against the origin's delta on that seed. A critique shown neither — and unable to be
    # given one, if the panel is not even in this node's search space for L4 to add —
    # prescribes steers the inner loops have already measured and lost.
    "l1_critique": NodeLayoutSpec(
        editor="l4",
        possible=frozenset(
            {
                "evidence_health",
                "diagnostics",
                "sample_transcripts",
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
                "evidence_health",
                "diagnostics",
                "sample_transcripts",
                "inner_narratives",
                "l1_wounds",
                "rare_hit_samples",
            ],
        ),
    ),
    # Framing layer — must see the distilled failure signal (`critique`), its own edit
    # vocabulary (`l1_signal_catalogue`), and the raw round evidence (`diagnostics`).
    # `task_context` is NOT one of them and is off the floor: L2 cannot write the framing
    # (`L2ContextOutput` declares no such field — it is operator-authored and frozen for the
    # run), so the mandatory rail here guarded a capability that does not exist, and what it
    # guarded IN was 752-1298 chars of static text, identical on every fire of every round.
    # It stays in `possible`, which turns it from a law into an axis L4 can search back in.
    # The two layer-control directives (`rebase_capability` / `terminate_capability`) ride
    # the layout — mandatory, so no L4 edit can sever the channel (the sanctioned
    # off-switch stays the config bit, which renders them empty).
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
                "task_context",
                "l1_signal_catalogue",
                "rebase_capability",
                "terminate_capability",
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
                "l1_signal_catalogue",
                "rebase_capability",
                "terminate_capability",
            ],
        ),
    ),
    # Strategic replan — must see the current `plan` it rewrites and the raw evidence
    # (`diagnostics`). `task_context` is off the floor for the same reason as `l2_context`
    # above: frozen, unwritable from here, and identical on every fire. The floor keeps
    # `evidence_health` on its own merit — the round's per-node failure rates, which L3 needs
    # to judge a fault unrecoverable; optional adds archive_top_runs.
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


# Import-time exhaustiveness — the structural contract `validate_l1_layout` enforces
# per edit, asserted once at module load so a drift in any node's spec fails at the
# source (no standalone wiring test). The INJECTIONS-membership check (every possible
# name resolves to a registered renderer) lives in the dispatch registry — domain must
# not import application. Here, pure set-algebra per node:
#   * every mandatory placeholder is a possible one,
#   * the floor only references possible placeholders, and
#   * the floor already satisfies the mandatory set.
for _node, _spec in NODE_LAYOUTS.items():
    _floor_ph = set(_spec.floor.all_placeholders())
    assert _spec.mandatory <= _spec.possible, f"{_node}: mandatory ⊄ possible"
    assert _floor_ph <= _spec.possible, (
        f"{_node}: floor references a placeholder absent from possible"
    )
    assert _floor_ph >= _spec.mandatory, (
        f"{_node}: floor must reference every mandatory placeholder"
    )
del _node, _spec, _floor_ph


def coerce_l1_layout(raw_layout: Any) -> L1Layout | None:
    """Coerce to an ``L1Layout``. ``{}`` is the sanctioned omit-sentinel ("keep current"); malformed
    non-empty input also returns ``None``, so the VALIDATOR and not this coercer surfaces the failure."""
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
    surface to L2's next fire either way as self-healing evidence."""

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
    """Deterministic layout checks against a node's ``spec``; a HARD failure rolls back to floor or prior."""
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
    "NODE_LAYOUTS",
    "L1Layout",
    "NodeLayoutSpec",
    "coerce_l1_layout",
    "default_l1_layout",
    "validate_l1_layout",
]
