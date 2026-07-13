"""Per-node prompt layout — the optimizer's information-flow axis. A layout is per-slot
lists of placeholder names the dispatch hub resolves when filling a node's PromptTemplate.

Every optimizer node owns a ``NodeLayoutSpec`` in ``NODE_LAYOUTS`` (editor / possible /
mandatory / floor). ``l1_generate`` is edited by L2 in-campaign (its live layout rides
``OptSearchPoint.memory.l1_layout``); the other meta-prompt nodes are edited only by L4
across the recursion (their floor is their layout until an outer loop mutates it). This
is the single source for which signals reach each meta-prompt — the dispatch hub fills
every node from here (no second `{{token}}` source in the templates).

Validation (`validate_l1_layout`, against a node's spec):
* HARD (missing mandatory / unknown name / dups within slot) → rollback to floor + wound.
* SOFT (unchanged from prior) → apply with warning.
"""

from __future__ import annotations

from typing import Any, Literal, cast

from pydantic import BaseModel, ConfigDict, Field, field_serializer

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


class NodeLayoutSpec(BaseModel):
    """Per-node information-flow spec — one optimizer node's searchable injection axis.

    * ``editor`` — who may mutate this node's layout: ``l2_l4`` (L2 in-campaign +
      L4 across the recursion — only ``l1_generate``), ``l4`` (L4 only — the other
      meta-prompt nodes, since nothing sits above them in a normal campaign), or
      ``static`` (never edited).
    * ``possible`` — the full add/excise vocabulary (⊇ ``mandatory`` and ⊇ ``floor``).
    * ``mandatory`` — the GUARD RAIL: placeholders an edit may never excise; the
      validator rolls back an edit that drops one, so the search stays "creative
      within guard rails."
    * ``floor`` — the origin layout (a conservative floor, expanded on evidence),
      used verbatim when no editor has mutated it.
    """

    model_config = ConfigDict(frozen=True)

    editor: Literal["l2_l4", "l4"]
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
# every other node hardcoded as `{{tokens}}` in `datasets/_optimizer/pipeline.json`).
# The dispatch hub fills every node from `NODE_LAYOUTS[node]` (floor ± editor edits),
# so the set of signals reaching each meta-prompt is one searched axis, not two
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
    # carries the mandatory structural state + the DISTILLED failure signal (`critique`)
    # + L1's own `l1_wounds` + `escalation_panel` + `origin_strengths` (regression guard).
    # `sample_transcripts` (the RAW misses) is OFF this floor: the `critique` is already its
    # compression, so the generator reading both duplicated a ~10k-char payload every round.
    # It stays in `L1_POSSIBLE` (and on `l1_critique`'s floor, the distiller's raw source), so
    # L2 re-adds it on stall — when the distillation proves lossy for a reasoning-MECHANISM
    # error — and L4 can search it back in. Raw `diagnostics` and the cross-run panels stay off
    # the floor for the same reason; L2 adds them on stall via its layout edit, L4 optimises that.
    "l1_generate": NodeLayoutSpec(
        editor="l2_l4",
        possible=L1_POSSIBLE,
        mandatory=L1_MANDATORY,
        floor=L1Layout(
            task_intent=["task_context"],
            problem_description=[
                "rendered_prompt",
                "pipeline_param_catalogue",
                "prompt_block_catalogue",
                "plan",
                "critique",
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
    "l1_critique": NodeLayoutSpec(
        editor="l4",
        possible=frozenset(
            {
                "evidence_health",
                "diagnostics",
                "sample_transcripts",
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
                "l1_wounds",
                "rare_hit_samples",
            ],
        ),
    ),
    # Framing layer — must see the framing dict it refines (`task_context`), the distilled
    # failure signal (`critique`), its own edit vocabulary (`l1_signal_catalogue`), and the
    # raw round evidence (`diagnostics`). Everything else on the floor is excisable.
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
            }
        ),
        mandatory=frozenset({"task_context", "critique", "l1_signal_catalogue", "diagnostics"}),
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
                "task_context",
                "l1_signal_catalogue",
            ],
        ),
    ),
    # Strategic replan — must see the current `plan` it rewrites, the framing it steers
    # (`task_context`), and the raw evidence (`diagnostics`). Optional adds evidence_health
    # + archive_top_runs.
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
            }
        ),
        mandatory=frozenset({"plan", "task_context", "diagnostics"}),
        floor=L1Layout(
            problem_description=[
                "plan",
                "task_context",
                "diagnostics",
                "l1_wounds",
                "axis_memory",
                "guard_breaches",
                "critique",
            ],
        ),
    ),
}


def default_l1_layout() -> L1Layout:
    """Origin layout for ``l1_generate`` = ``NODE_LAYOUTS['l1_generate'].floor`` (one
    source). Returns a deep copy so the OSP's mutable per-slot lists never alias the
    shared floor. Origin = conservative floor, expanded on evidence by L2."""
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
    spec: NodeLayoutSpec,
    prior_layout: L1Layout | None = None,
) -> LayoutValidationResult:
    """Run deterministic layout checks against a node's ``spec``. HARD failures flip
    ``is_valid`` (→ rollback to the floor / prior — the guard rail)."""
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
    "LayoutValidationResult",
    "NodeLayoutSpec",
    "coerce_l1_layout",
    "default_l1_layout",
    "validate_l1_layout",
]
