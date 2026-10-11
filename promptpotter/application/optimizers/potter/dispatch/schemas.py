"""A field edit IS a prompt edit: regenerate via ``scripts/build_optimizer_schemas.py``."""

from __future__ import annotations

import functools
from collections.abc import Callable, Mapping, Sequence
from typing import Annotated, Any, ClassVar, cast

from pydantic import (
    BeforeValidator,
    Field,
    WithJsonSchema,
    create_model,
    model_validator,
)

from promptpotter.application.bench.llm_call import OptimizerResponseModel
from promptpotter.application.optimizers.potter.dispatch.bundle import (
    L3_PLAN_MAX_CHARS,
    LAYOUT_SCHEMA_INSTRUCTION,
)
from promptpotter.application.optimizers.potter.dispatch.layout import (
    NODE_LAYOUTS,
    layout_json_schema,
)
from promptpotter.domain.candidate_diff import candidate_delta
from promptpotter.shared.hashing import shapes_optimizer_prompt

shapes_optimizer_prompt(__name__)


def _truncate(max_len: int) -> Callable[[Any], Any]:
    """Pydantic's ``max_length`` alone would discard the ENTIRE reply on one overrun and force a paid repair."""

    def _v(value: Any) -> Any:
        if isinstance(value, (str, list)) and len(value) > max_len:
            return value[:max_len]
        return value

    return _v


VARIANT_PROSE_MAX = 320

CITATION_MAX = 120


def _drop_blank_values(value: object) -> object:
    if isinstance(value, dict):
        return {k: v for k, v in value.items() if not (isinstance(v, str) and not v.strip())}
    return value


def _truncate_marked(max_len: int) -> Callable[[Any], Any]:
    """The marker is VISIBLE: a silent mid-quote cut reads downstream as a complete-but-wrong steer."""

    def _v(value: Any) -> Any:
        if isinstance(value, str) and len(value) > max_len:
            return value[: max_len - 1].rsplit(" ", 1)[0] + "…"
        return value

    return _v


__all__ = [
    "L1CritiqueOutput",
    "L1GenerateOutput",
    "L1Variant",
    "L2ContextOutput",
    "L3PlanOutput",
    "VariantEvidenceGrounding",
    "build_l1_response_model",
    "build_l2_response_model",
]


class VariantEvidenceGrounding(OptimizerResponseModel):
    """PERMISSIVE on ``field`` at the parse boundary: not every provider honours the grafted enum, so the validator enforces it."""

    field: str = Field(description="A citable panel named in the prompt, or stall_exploration.")
    # The parse boundary must NOT truncate: `_citation_in_prompt` substring-matches this against the prompt.
    citation: Annotated[str, WithJsonSchema({"type": "string", "maxLength": CITATION_MAX})]


class L1Variant(OptimizerResponseModel):
    """Slot keys stay loose so a backend-specific node name never fails the parse; an all-empty variant re-asks, costing no slot."""

    # Field order is GENERATION order; optional at the PARSE boundary only, REQUIRED on the wire (`l1_wire_schema.py`).
    evidence_grounding: VariantEvidenceGrounding | None = None
    targets_cluster: str = ""
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
    # Capped: a response truncated at `max_tokens` is a ZERO-CANDIDATE ROUND, not a short answer.
    changes_description: Annotated[str, BeforeValidator(_truncate_marked(VARIANT_PROSE_MAX))] = (
        Field(max_length=VARIANT_PROSE_MAX)
    )

    # Bound per round on the SUBCLASS `build_l1_response_model` mints; empty here, convicting only a blank variant.
    parent_prompt: ClassVar[Mapping[str, str]] = {}
    parent_params: ClassVar[Mapping[str, Any]] = {}
    parent_shot_ids: ClassVar[tuple[int, ...]] = ()

    @model_validator(mode="after")
    def _reject_empty_mutation(self) -> L1Variant:
        # Raised HERE so the message rides the schema-repair retry; `l1_invariants` can only drop the arm.
        cls = type(self)
        written: dict[str, Any] = dict(self.prompt_fields_updates)
        if self.shot_ids is not None:
            written["shot_ids"] = self.shot_ids
        parent = {**cls.parent_prompt, "shot_ids": cls.parent_shot_ids}
        child = {**parent, **written}
        if not candidate_delta(child, parent, self.pipeline_overlay, cls.parent_params):
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
    """``populate_by_name`` stays OFF: the original key must fail, never half-apply a rename."""
    variant = cast(
        "type[L1Variant]",
        create_model("L1Variant", __base__=_renamed_variant(tuple(sorted(field_names.items())))),
    )
    # ClassVars, so the parent's own text never becomes a field the wire schema advertises.
    variant.parent_prompt = dict(parent_prompt)
    variant.parent_params = dict(parent_params)
    variant.parent_shot_ids = tuple(parent_shot_ids)
    # `__class_getitem__` builds `list[variant]` as a VALUE; the subscript form is a type expression mypy rejects.
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
        overrides[field] = (info.annotation, Field(**kwargs))
    return create_model("L1Variant", __base__=L1Variant, **overrides)


class L1CritiqueOutput(OptimizerResponseModel):
    # FIRST on purpose: the tail truncates first, and these quotes are the costliest drop.
    failure_highlights: Annotated[list[str], BeforeValidator(_truncate(3))] = Field(
        default_factory=list,
        max_length=3,
        description=(
            "One diagnosis per DISTINCT failure cluster, ≤3 — claim, broken reasoning step, "
            "predicted vs GT. Each item ≤320 chars (downstream truncates)."
        ),
    )
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


class ForkProposal(OptimizerResponseModel):
    """Carries no round offset: ``optimizers/potter/CLAUDE.md`` § The layer-control channel."""

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


class TerminateProposal(OptimizerResponseModel):
    reason: Annotated[str, BeforeValidator(_truncate_marked(150))] = Field(
        default="",
        max_length=150,
        description="1-2 sentences naming the cause and what the operator must fix.",
    )


class L2ContextOutput(OptimizerResponseModel):
    """Carries neither ``task_context`` (operator-authored, frozen for the run) nor ``action``."""

    axis_targeted: Annotated[str, BeforeValidator(_truncate_marked(30))] = Field(
        default="",
        max_length=30,
        description=(
            "The L1 axis the failure cluster routes to — a prompt field for a semantic failure, "
            "a param for a quantitative one. Name it on every fire; it is the anchor the lever "
            "is judged against, not a label for the lever."
        ),
    )
    # A plain dict on purpose: typed, one off-enum panel fails the whole model before `validate_l1_layout` can report it.
    l1_layout: Annotated[
        dict[str, str],
        WithJsonSchema(
            layout_json_schema(NODE_LAYOUTS["l1_generate"], description=LAYOUT_SCHEMA_INSTRUCTION)
        ),
    ] = Field(default_factory=dict)
    l1_overrides: dict[str, Any] = Field(
        default_factory=dict,
        description="Optional runtime knobs; only 'temperature' and 'n_variants' are read.",
    )
    # Optional at PARSE time only, so a fire that omits it still lands its layout edit.
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
    if not withheld:
        return L2ContextOutput
    schema = layout_json_schema(
        NODE_LAYOUTS["l1_generate"], description=LAYOUT_SCHEMA_INSTRUCTION, withheld=withheld
    )
    layout: Any = (Annotated[dict[str, str], WithJsonSchema(schema)], Field(default_factory=dict))
    return create_model("L2ContextOutput", __base__=L2ContextOutput, l1_layout=layout)


class L3PlanOutput(OptimizerResponseModel):
    plan: Annotated[str, BeforeValidator(_truncate_marked(L3_PLAN_MAX_CHARS))] = Field(
        max_length=L3_PLAN_MAX_CHARS,
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


# At import, not in a test: a model on plain `StrictModel` typechecks and passes the drift gate.
_leaky = sorted(
    title
    for _model in (L1GenerateOutput, L1CritiqueOutput, L2ContextOutput, L3PlanOutput)
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
