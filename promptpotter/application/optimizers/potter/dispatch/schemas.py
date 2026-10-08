"""**``Field(description=)`` is the only model-facing text here; a class docstring is NOT** — the emitted schema
drops those. A field edit IS a prompt edit: regenerate via ``scripts/build_optimizer_schemas.py``."""

from __future__ import annotations

import functools
from collections.abc import Callable, Mapping, Sequence
from typing import Annotated, Any, ClassVar, cast

from pydantic import (
    BaseModel,
    BeforeValidator,
    Field,
    WithJsonSchema,
    create_model,
    model_validator,
)

from promptpotter.application.bench.llm_call import OptimizerResponseModel
from promptpotter.application.optimizers.potter.dispatch.bundle import LAYOUT_SCHEMA_INSTRUCTION
from promptpotter.application.optimizers.potter.dispatch.layout import (
    NODE_LAYOUTS,
    layout_json_schema,
)
from promptpotter.domain.candidate_diff import candidate_delta
from promptpotter.shared.hashing import shapes_optimizer_prompt

shapes_optimizer_prompt(__name__)


def _truncate(max_len: int) -> Callable[[Any], Any]:
    """Silent head-keeping truncation for an over-cap LLM field. Pydantic's ``max_length`` would discard the ENTIRE
    critique on one overrun and force a paid repair round-trip; the schema cap stays, to state the budget."""

    def _v(value: Any) -> Any:
        if isinstance(value, (str, list)) and len(value) > max_len:
            return value[:max_len]
        return value

    return _v


# Per-variant prose ceiling for `l1_generate`'s `changes_description`. 320 is this file's
# established prose cap, and it binds the tail and leaves the median untouched.
VARIANT_PROSE_MAX = 320

# A citation PINS an entry the prompt already holds; it does not reproduce it. Its one machine
# reader (`_citation_in_prompt`) needs a short verbatim run, so a row id plus its decisive phrase is
# the whole job, and every char past that is the prompt decoded back out of the answer.
CITATION_MAX = 120

# The only two keys anything reads off `l1_overrides` (`l1/candidate_source.py`). Filtered at parse
# because `_parse_l2` MERGES this LLM-written dict forward on every fire: an invented key was
# never read, never pruned, and rendered uncapped for the rest of the campaign.
L1_OVERRIDE_KEYS: frozenset[str] = frozenset({"creativity", "n_variants"})

# THE OPTIMIZER'S OWN SEARCH SPACE, as node axes — `L1_OVERRIDE_KEYS` on the node the operator
# sees them on, joined to the name that node calls them. `creativity` IS
# `l1_generate.temperature` (`l1/candidate_source.py` defaults it from exactly that key); the
# synonym is a separate cleanup, and this table is where the two spellings meet so no reader has
# to know both.
#
# Served as potter's `OptimizerRuntime.own_axes`, which the optimizer picture reads as the knobs
# already movable — the manifest's `param_keys` are empty by design, which says "L1 does not search its
# own config" and says NOTHING about L2, which does.
L2_NODE_AXES: dict[str, set[str]] = {"l1_generate": {"temperature", "n_variants"}}


def _keep_known_keys(allowed: frozenset[str]) -> Callable[[Any], Any]:
    def _v(value: Any) -> Any:
        if isinstance(value, dict):
            return {k: v for k, v in value.items() if k in allowed}
        return value

    return _v


def _drop_blank_values(value: object) -> object:
    if isinstance(value, dict):
        return {k: v for k, v in value.items() if not (isinstance(v, str) and not v.strip())}
    return value


def _truncate_marked(max_len: int) -> Callable[[Any], Any]:
    """Word-boundary truncation with a VISIBLE marker — for fields whose tail is load-bearing prose, where a silent
    mid-quote cut reads downstream as a complete-but-wrong steer."""

    def _v(value: Any) -> Any:
        if isinstance(value, str) and len(value) > max_len:
            return value[: max_len - 1].rsplit(" ", 1)[0] + "…"
        return value

    return _v


__all__ = [
    "L1_OVERRIDE_KEYS",
    "L2_NODE_AXES",
    "OPTIMIZER_RESPONSE_MODELS",
    "L1CritiqueOutput",
    "L1GenerateOutput",
    "L1Variant",
    "L2ContextOutput",
    "L3PlanOutput",
    "VariantEvidenceGrounding",
    "build_l1_response_model",
    "build_l2_response_model",
]


# ---------------------------------------------------------------------------
# l1_generate — proposes candidate variants for the next round.
# ---------------------------------------------------------------------------


class VariantEvidenceGrounding(OptimizerResponseModel):
    """One row of evidence justifying a variant's mutation. ``field``'s per-round ``enum`` is grafted onto the wire schema,
    but the parse boundary stays PERMISSIVE because not every provider honours it — the validator is the enforcement."""

    field: str = Field(description="A citable panel named in the prompt, or stall_exploration.")
    # WIRE-strict, PARSE-permissive — the same split `evidence_grounding` itself is built on one
    # class down. `maxLength` states the budget where the model reads it; the parse boundary must
    # NOT truncate, because `_citation_in_prompt` (`validators/l1_behavior.py`) substring-matches
    # this against the rendered prompt, and a truncation marker would fail every real quote.
    citation: Annotated[str, WithJsonSchema({"type": "string", "maxLength": CITATION_MAX})]


class L1Variant(OptimizerResponseModel):
    """One candidate variant. Each delta slot's INNER shape is grafted at runtime from the active schema; the keys stay
    loose so a backend-specific node name never fails the parse. An all-empty variant re-asks instead of costing a slot."""

    # FIELD ORDER IS GENERATION ORDER — evidence precedes the decision it justifies, and the
    # decision is the MUTATION, not the prose about it. `changes_description` trails the two
    # delta slots so it can only ever REPORT a mutation already emitted. Ahead of them it was
    # a promise the model could make and then break, and it did: the required set asked for a
    # name and a paragraph while marking the payload optional, so ~10% of live variants arrived
    # narrated-but-empty, each one a no-op candidate that dragged the round's diversity and
    # cleanliness down. `evidence_grounding` stays optional HERE, at the parse boundary, so a
    # provider omitting the citation on one variant doesn't crash the round —
    # `evidence_grounding_present` is its canonical enforcement point and records misses as wounds
    # without burning an LLM call. It is REQUIRED on the wire (`l1_wire_schema.py`), and the split
    # is the point: tolerating an omission is not the same as offering one. While the emitted
    # schema also carried the `| None`, `null` was a legal answer to a mandatory question, and 2 of
    # 19 live rounds gave it — for every variant in the call, one response being one decision.
    evidence_grounding: VariantEvidenceGrounding | None = None
    # The `l1_critique` root cause this variant attacks, so a batch's spread is named and
    # `_check_distinct_clusters` scores it. Parse-optional and wire-required, as the field above.
    targets_cluster: str = ""
    # The slot's prose rides its own description, so a round that withdraws the slot
    # (`l1_wire_schema._SLOT_PANEL`) stops paying for it with no second rule.
    pipeline_overlay: dict[str, dict[str, Any]] = Field(
        default_factory=dict,
        description=(
            "Nested {node: {param: value}} — scalar backend tunables, and "
            '"output_schema_descriptions.<path>": prose, which rewrites the `description` of that '
            "field of the node's output schema. Each is a param under ITS NODE like any other, "
            "NEVER a top-level key beside the nodes. Paths are FIXED: you describe a field, never "
            "rename or invent one. Scalar params (temperature, tokens, effort) are a last resort: "
            "at most one, and only where a panel or a runtime failure points there."
        ),
    )
    # Blank values dropped at the boundary, so nothing downstream ever holds a "" that one reader
    # takes for a clear and another for no edit.
    prompt_fields_updates: Annotated[dict[str, str], BeforeValidator(_drop_blank_values)] = Field(
        default_factory=dict,
        description=(
            "Top-level prompt-template fields; the open ones are grafted from the "
            "active PipelineSchema at runtime."
        ),
    )
    shot_ids: list[int] | None = Field(
        None,
        description="The shots this variant runs with: demo-pool ids from the DEMO POOL panel, "
        "in order. Replaces the parent's list; omit to keep it.",
    )
    # Capped like every other node's prose. `l1_generate` was the ONLY optimizer node with no
    # length bound on any output field, while it is the most-fired and the one whose answer can
    # run into `max_tokens` — a truncated response is not a short answer, it is a ZERO-CANDIDATE
    # ROUND (`l1/generate.py` classifies it `PARSE_FAILURE_MALFORMED`). Measured over 133
    # banked rounds, this field plus `citation` are 36% of the answer JSON at ~249 chars each,
    # so the cap binds only the tail. Marked truncation, not silent: the tail is a steer a
    # reader would otherwise take as complete.
    changes_description: Annotated[str, BeforeValidator(_truncate_marked(VARIANT_PROSE_MAX))] = (
        Field(max_length=VARIANT_PROSE_MAX)
    )

    # The parent this round mutates away from, bound per round onto the SUBCLASS
    # `build_l1_response_model` mints. Empty on the base class, where only a blank variant is
    # convicted and a restatement of the parent passes to `l1_invariants`.
    parent_prompt: ClassVar[Mapping[str, str]] = {}
    parent_params: ClassVar[Mapping[str, Any]] = {}
    parent_shot_ids: ClassVar[tuple[int, ...]] = ()

    @model_validator(mode="after")
    def _reject_empty_mutation(self) -> L1Variant:
        # The same `candidate_delta` `l1_invariants` convicts with after the call returns, where
        # it can only drop the candidate; here the message rides the schema-repair retry back to
        # the model (`llm/openai_compat.py`), so the round gets the arm instead of losing it.
        cls = type(self)
        written: dict[str, Any] = dict(self.prompt_fields_updates)
        if self.shot_ids is not None:
            written["shot_ids"] = self.shot_ids
        parent = {**cls.parent_prompt, "shot_ids": cls.parent_shot_ids}
        if not candidate_delta(written, parent, self.pipeline_overlay, cls.parent_params):
            raise ValueError(
                "this variant mutates nothing: pipeline_overlay, prompt_fields_updates or "
                "shot_ids must carry a value that DIFFERS from the current one. Describing a "
                "change in changes_description is not making one, and an empty string or the "
                "field's current text is not an edit. Emit the new text."
            )
        return self


class L1GenerateOutput(OptimizerResponseModel):
    variants: list[L1Variant]


def build_l1_response_model(
    field_names: Mapping[str, str],
    *,
    parent_prompt: Mapping[str, str],
    parent_params: Mapping[str, Any],
    parent_shot_ids: Sequence[int],
) -> type[L1GenerateOutput]:
    """``L1GenerateOutput`` validating through renamed wire keys, against the parent this round
    mutates. ``populate_by_name`` is left OFF deliberately: a model emitting the original key fails
    validation and self-penalises, rather than the rename silently half-applying. The parent is
    REQUIRED because it decides which variants ``_reject_empty_mutation`` convicts."""
    variant = cast(
        "type[L1Variant]",
        create_model("L1Variant", __base__=_renamed_variant(tuple(sorted(field_names.items())))),
    )
    # ClassVars, so they ride the subclass without becoming fields the wire schema advertises —
    # the parent's own text must never be emitted back to the model as something to fill in.
    variant.parent_prompt = dict(parent_prompt)
    variant.parent_params = dict(parent_params)
    variant.parent_shot_ids = tuple(parent_shot_ids)
    # `variant` is a class only at runtime, so `list[variant]` written as a subscript is a type
    # expression over a variable that mypy objects to unstably; `__class_getitem__` builds the same
    # `list[...]` as a VALUE, which no configuration type-analyses.
    variants_field: Any = (list.__class_getitem__(variant), ...)
    return create_model("L1GenerateOutput", __base__=L1GenerateOutput, variants=variants_field)


@functools.lru_cache(maxsize=16)
def _renamed_variant(items: tuple[tuple[str, str], ...]) -> type[L1Variant]:
    if not items:
        return L1Variant
    overrides: dict[str, Any] = {}
    for field, wire in items:
        info = L1Variant.model_fields[field]
        kwargs: dict[str, Any] = {
            "validation_alias": wire,
            "serialization_alias": wire,
            "description": info.description,
        }
        if info.default_factory is not None:
            kwargs["default_factory"] = info.default_factory
        elif not info.is_required():
            kwargs["default"] = info.default
        # No default and no default_factory ⇒ Field() stays required, matching the source field.
        overrides[field] = (info.annotation, Field(**kwargs))
    return create_model("L1Variant", __base__=L1Variant, **overrides)


# ---------------------------------------------------------------------------
# l1_critique — round-end analysis, feeds the next round's L1.
# ---------------------------------------------------------------------------


class L1CritiqueOutput(OptimizerResponseModel):
    # First field on purpose: generation order = schema order, so the tail truncates first.
    # failure_highlights carries the per-sample evidence quotes the NEXT round's L1 differentiates
    # on — losing them to a long-output clip is the costliest drop, so it generates before the
    # steers. The answer_format prose states this same order; the two must stay in lock-step.
    failure_highlights: Annotated[list[str], BeforeValidator(_truncate(3))] = Field(
        default_factory=list,
        max_length=3,
        description=(
            "One diagnosis per DISTINCT failure cluster, ≤3 — claim, broken reasoning step, "
            "predicted vs GT. Each item ≤320 chars (downstream truncates)."
        ),
    )
    # Carries no quote: the evidence behind the steer is `failure_highlights`, rendered beside it in
    # the same panel, and a quote here is decoded again by every reader that cites the steer.
    priority_fix: Annotated[str, BeforeValidator(_truncate_marked(320))] = Field(
        default="",
        max_length=320,
        description=(
            "The single strongest steer, as `<axis>: <concrete change>`. The axis leads, before "
            "the colon, named VERBATIM from the vocabulary this node's prompt states. One you "
            "invent is dropped downstream and takes the steer's axis menu with it, so the next "
            "round reads a fix naming no lever it can pull."
        ),
    )
    suggested_axes: Annotated[list[str], BeforeValidator(_truncate(4))] = Field(
        default_factory=list,
        max_length=4,
        description="≤4 short axis names. Each item ≤40 chars (downstream truncates).",
    )


# ---------------------------------------------------------------------------
# Fork proposal — emitted by L2 or L3 to rewind the search to an earlier round.
# ---------------------------------------------------------------------------


class ForkProposal(OptimizerResponseModel):
    """L2/L3-emitted proposal to rewind the search. It carries no round offset — see ``optimizers/potter/CLAUDE.md``."""

    reason: Annotated[str, BeforeValidator(_truncate_marked(150))] = Field(
        default="",
        max_length=150,
        description="1-2 sentences naming the cause and what the operator must fix.",
    )
    unlock_schema_field_rename: bool = Field(
        False,
        description=(
            "Let the fork's L1 rename a field on the inner l1_generate's output schema "
            "(it can only describe fields today). Set ONLY when the panels show the search "
            "stalling on what a field is FOR rather than on what it says — a field whose "
            "name misdescribes its content. Default false."
        ),
    )


# ---------------------------------------------------------------------------
# Terminate proposal — emitted by L2 or L3 to STOP the cycle when the failure
# is unrecoverable through any framing/plan move (e.g. a starved evidence node).
# ---------------------------------------------------------------------------


class TerminateProposal(OptimizerResponseModel):
    """L2/L3-emitted decision to terminate the cycle — the HITL stop, peer of :class:`ForkProposal`."""

    reason: Annotated[str, BeforeValidator(_truncate_marked(150))] = Field(
        default="",
        max_length=150,
        description="1-2 sentences naming the cause and what the operator must fix.",
    )


# ---------------------------------------------------------------------------
# l2_context — move L1's panels (l1_layout) + runtime knobs (l1_overrides).
# ---------------------------------------------------------------------------


class L2ContextOutput(OptimizerResponseModel):
    """L2 refinement. The LEVERS are optional — set only what you change; the REASON
    (``axis_targeted`` + ``rationale``) rides every fire, because it is what the behaviour checks grade and the only
    thing separating a steer from a guess. It deliberately carries neither ``task_context`` (operator-authored,
    frozen for the run) nor ``action``."""

    # Sized off 70 live fires (output 582 median / 2187 max; `rationale` peaked at 745,
    # `axis_targeted` at 14), so a normal call is untouched. `l1_layout` needs no char bound —
    # its vocabulary is the placeholder registry, which the schema now DECLARES instead of leaving
    # `validate_l1_layout` to discover it was ignored. Enforcing a vocabulary is not stating one.
    axis_targeted: Annotated[str, BeforeValidator(_truncate_marked(30))] = Field(
        default="",
        max_length=30,
        description=(
            "The L1 axis the failure cluster routes to — a prompt field for a semantic failure, "
            "a param for a quantitative one. Name it on every fire; it is the anchor the lever "
            "is judged against, not a label for the lever."
        ),
    )
    # PARSED as a plain dict on purpose. Typing the panels would make one off-enum name fail the
    # whole model, taking `axis_targeted`, `rationale` and both control proposals down with the
    # layout edit — and the breach must still reach L2's next fire as a `ValidatorOutcome`, which
    # is a thing only `validate_l1_layout` can emit. The schema teaches; the validator judges.
    l1_layout: Annotated[
        dict[str, str],
        WithJsonSchema(
            layout_json_schema(NODE_LAYOUTS["l1_generate"], description=LAYOUT_SCHEMA_INSTRUCTION)
        ),
    ] = Field(default_factory=dict)
    l1_overrides: Annotated[dict[str, Any], BeforeValidator(_keep_known_keys(L1_OVERRIDE_KEYS))] = (
        Field(
            default_factory=dict,
            description="Optional runtime knobs; only 'creativity' and 'n_variants' are read.",
        )
    )
    # Optional at PARSE time only, so a fire that omits it still lands its layout edit rather
    # than taking the whole model down. It is not optional to WRITE: two behaviour checks read
    # it (`l2_rationale_substantive` against a 40-char floor, `l2_evidence_anchored` for the
    # cited number), and neither floor was stated anywhere the model could see. Undescribed and
    # sitting under an "set only what you change" header, it read as a lever to skip — 3 of 14
    # live fires returned it empty, failing both checks at once.
    rationale: Annotated[str, BeforeValidator(_truncate_marked(400))] = Field(
        default="",
        max_length=400,
        description=(
            "Why this fire, in 1-2 sentences: the failure cluster you are attacking and the "
            "named data point behind it — a sample id, a count, a yield number. Required on "
            "every fire, including one that pulls no lever; a lever with no diagnosis behind "
            "it cannot be told from a guess."
        ),
    )
    fork_proposal: ForkProposal | None = None
    terminate_proposal: TerminateProposal | None = None


@functools.lru_cache(maxsize=4)
def build_l2_response_model(withheld: frozenset[str]) -> type[L2ContextOutput]:
    """``L2ContextOutput`` whose ``l1_layout`` enum leaves *withheld* out."""
    if not withheld:
        return L2ContextOutput
    schema = layout_json_schema(
        NODE_LAYOUTS["l1_generate"], description=LAYOUT_SCHEMA_INSTRUCTION, withheld=withheld
    )
    layout: Any = (Annotated[dict[str, str], WithJsonSchema(schema)], Field(default_factory=dict))
    return create_model("L2ContextOutput", __base__=L2ContextOutput, l1_layout=layout)


# ---------------------------------------------------------------------------
# l3_plan — strategic replan with optional sticky pointer to L2.
# ---------------------------------------------------------------------------


class L3PlanOutput(OptimizerResponseModel):
    # `plan` rides EVERY downstream prompt until the next replan, so its length is a standing
    # tax — and it was the only unbounded output. Asking for the budget in `answer_format`
    # cannot work (a model cannot count the characters it emits), so it is judged here and
    # MARKED: a plan cut mid-bullet must not read downstream as a complete strategy.
    plan: Annotated[str, BeforeValidator(_truncate_marked(800))] = Field(
        max_length=800,
        description=(
            "The strategy every later prompt reads until the next replan. Say what L2 and L1 "
            "should DO with the levers they hold — L2 moves which panels L1 sees and how wide it "
            "explores; L1 rewrites the target prompt's fields. A plan written in the solver's "
            "vocabulary instructs neither, and rides every downstream call regardless."
        ),
    )
    note: Annotated[str, BeforeValidator(_truncate_marked(150))] = Field(default="", max_length=150)
    rationale: Annotated[str, BeforeValidator(_truncate_marked(125))] = Field(
        default="", max_length=125
    )
    fork_proposal: ForkProposal | None = None
    terminate_proposal: TerminateProposal | None = None


# ---------------------------------------------------------------------------
# Node → model registry.
# ---------------------------------------------------------------------------


OPTIMIZER_RESPONSE_MODELS: dict[str, type[BaseModel]] = {
    "l1_generate": L1GenerateOutput,
    "l1_critique": L1CritiqueOutput,
    "l2_context": L2ContextOutput,
    "l3_plan": L3PlanOutput,
}


# Fail import if a model here — or one nested under it — still carries its class docstring as
# a schema `description`. A test is the wrong home: a new model on plain `StrictModel`
# typechecks and the drift gate passes a faithfully-regenerated docstring, so the failure is
# silent. The PROPERTY is asserted; inheriting the base is one way to satisfy it.
_leaky = sorted(
    title
    for _model in OPTIMIZER_RESPONSE_MODELS.values()
    for _schema in (_model.model_json_schema(),)
    for block in (_schema, *(_schema.get("$defs") or {}).values())
    if block.get("description") and (title := str(block.get("title", _model.__name__)))
)
if _leaky:
    raise RuntimeError(
        "Optimizer response schemas carry a class-level `description` — a class docstring "
        f"hoisted onto the wire, read by the model on every call: {_leaky}. Inherit "
        "`OptimizerResponseModel`; put model-facing text in `Field(description=)`."
    )
del _leaky
