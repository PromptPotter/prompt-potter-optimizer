"""A field edit IS a prompt edit: regenerate via ``scripts/build_optimizer_schemas.py``."""

from __future__ import annotations

import functools
import re
from collections.abc import Callable, Mapping
from typing import Annotated, Any, Self

from pydantic import (
    AfterValidator,
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
    RecombinationOffer,
)
from promptpotter.application.optimizers.potter.dispatch.layout import (
    NODE_LAYOUTS,
    layout_json_schema,
)
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

# A run this short inside prose of another script is a decoding slip, never a quote.
_CJK_RUN = re.compile(r"[぀-ヿ㐀-䶿一-鿿가-힯豈-﫿]+")
_STRAY_RUN_MAX = 3


def _one_script(value: str) -> str:
    runs = _CJK_RUN.findall(value)
    letters = sum(c.isalpha() for c in value)
    if any(len(r) <= _STRAY_RUN_MAX for r in runs) and 2 * sum(map(len, runs)) < letters:
        raise ValueError(f"stray {runs[0]!r} inside prose of another script: write it in one")
    return value


def _one_script_values(value: dict[str, dict[str, Any]]) -> dict[str, dict[str, Any]]:
    for fields in value.values():
        for text in fields.values():
            if isinstance(text, str):
                _one_script(text)
    return value


def _drop_blank_values(value: object) -> object:
    if isinstance(value, dict):
        return {k: v for k, v in value.items() if not (isinstance(v, str) and not v.strip())}
    return value


def _drop_blank_node_values(value: object) -> object:
    if isinstance(value, dict):
        return {node: _drop_blank_values(fields) for node, fields in value.items()}
    return value


def _truncate_marked(max_len: int) -> Callable[[Any], Any]:
    """The marker is VISIBLE: a silent mid-quote cut reads downstream as a complete-but-wrong steer."""

    # Never beside a wire `maxLength`: a provider grammar enforcing it cuts first, unmarked.

    def _v(value: Any) -> Any:
        if isinstance(value, str) and len(value) > max_len:
            return value[: max_len - 1].rsplit(" ", 1)[0] + "…"
        return value

    return _v


CLUSTER_MECHANISM_MAX = 60
CLUSTER_CLAIM_MAX = 320
# A cluster is a MECHANISM, and one row shows no mechanism: the next generator rewrites around that row.
CLUSTER_ROWS_MIN = 2


class VariantEvidenceGrounding(OptimizerResponseModel):
    """PERMISSIVE on ``field`` at the parse boundary: not every provider honours the grafted enum, so the validator enforces it."""

    field: str = Field(description="A citable panel named in the prompt, or stall_exploration.")
    # Never truncated: `l1_strict.py::l1_citation_in_panel` matches it against the panel `field` names.
    citation: str


class L1Variant(OptimizerResponseModel):
    """Slot keys stay loose so a backend-specific node name never fails the parse; a no-op variant parses, and `l1_invariants` drops that arm alone."""

    # Field order is GENERATION order; optional at the PARSE boundary only, REQUIRED on the wire (`l1_wire_schema.py`).
    evidence_grounding: VariantEvidenceGrounding | None = None
    # Rows, never a label: a name is coined freely on a `strict: False` wire, an id is checked against the prompt.
    targets_rows: list[int] = Field(
        default_factory=list,
        json_schema_extra={"minItems": CLUSTER_ROWS_MIN},
        description=(
            "The rows this edit must hold for, by their `[#id]` marks — at least two, and all "
            "inside ONE critique cluster where the CRITIQUE panel lists clusters."
        ),
    )
    pipeline_overlay: Annotated[dict[str, dict[str, Any]], AfterValidator(_one_script_values)] = (
        Field(
            default_factory=dict,
            description=(
                "Nested {node: {param: value}} — scalar backend tunables and "
                "`output_schema_descriptions.<path>` prose, each a param under ITS NODE, NEVER a "
                "top-level key beside the nodes. Scalar params (temperature, tokens, effort) are a "
                "last resort: at most one, and only where a panel or a runtime failure points there."
            ),
        )
    )
    prompt_fields_updates: Annotated[
        dict[str, dict[str, str]],
        BeforeValidator(_drop_blank_node_values),
        AfterValidator(_one_script_values),
    ] = Field(
        default_factory=dict,
        description="Per prompt node, its prompt fields: {node: {field: new full text}}.",
    )
    shots: dict[str, list[int]] = Field(
        default_factory=dict,
        description="Per prompt node, the shots it runs with: demo-pool ids from the DEMO POOL "
        "panel, in order. Replaces that node's list; omit a node to keep it.",
    )
    changes_description: Annotated[
        str, BeforeValidator(_truncate_marked(VARIANT_PROSE_MAX)), AfterValidator(_one_script)
    ]


class L1Recombination(OptimizerResponseModel):
    """A recombination spec: ids and loci only, so a child carries no text of L1's own."""

    main: str = Field(
        description="A RECOMBINATION POOL id marked main: the line the child continues, giving "
        "every locus `from_donor` omits."
    )
    donor: str = Field(description="Another pool id, of any round: it gives the `from_donor` loci.")
    from_donor: list[str] = Field(
        description="The loci, as `node.field`, the donor gives: fewer than half of those the two "
        "differ on, one where they differ on two; more drops the child mechanically."
    )
    changes_description: Annotated[
        str, BeforeValidator(_truncate_marked(VARIANT_PROSE_MAX)), AfterValidator(_one_script)
    ]


class L1GenerateOutput(OptimizerResponseModel):
    variants: list[L1Variant]
    recombinations: list[L1Recombination] = Field(default_factory=list)


def build_l1_response_model(field_names: Mapping[str, str]) -> type[L1GenerateOutput]:
    """``populate_by_name`` stays OFF: the original key must fail, never half-apply a rename."""
    variant = _renamed_variant(tuple(sorted(field_names.items())))
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


class FailureCluster(OptimizerResponseModel):
    # GENERATION order: the ids commit the cluster to rows before the prose can name one. `minItems`
    # is advisory on a `strict: False` wire; `l1/critique.py::spanning_clusters` is what decides.
    sample_ids: list[int] = Field(
        json_schema_extra={"minItems": CLUSTER_ROWS_MIN},
        description=(
            "The rows this cluster spans, by the `[#id]` marks in the panels — at least two. "
            "Two rows that fail for different reasons are two clusters, never one."
        ),
    )
    mechanism: Annotated[
        str, BeforeValidator(_truncate_marked(CLUSTER_MECHANISM_MAX)), AfterValidator(_one_script)
    ] = Field(
        description=("The ONE root cause, as a short label naming the cognitive move that fails."),
    )
    claim: Annotated[
        str, BeforeValidator(_truncate_marked(CLUSTER_CLAIM_MAX)), AfterValidator(_one_script)
    ] = Field(
        description=(
            "WHY these rows land wrong, in your own words: the step that breaks and what was "
            "said against the truth. General over the cluster, never one row's procedure."
        ),
    )


class L1CritiqueOutput(OptimizerResponseModel):
    # FIRST on purpose: the tail truncates first, and the clusters are what the generator targets.
    failure_highlights: Annotated[list[FailureCluster], BeforeValidator(_truncate(3))] = Field(
        default_factory=list,
        max_length=3,
        description="One per DISTINCT failure cluster, ≤3, largest first.",
    )
    priority_fix: Annotated[
        str, BeforeValidator(_truncate_marked(VARIANT_PROSE_MAX)), AfterValidator(_one_script)
    ] = Field(
        description=(
            "The single strongest steer, as `<axis>: <concrete change>`. The axis leads, before "
            "the colon, named VERBATIM from the axis list this node's prompt states. One you "
            "invent is dropped downstream and takes the steer's axis menu with it, so the next "
            "round reads a fix naming no lever it can pull."
        ),
    )
    suggested_axes: Annotated[list[str], BeforeValidator(_truncate(4))] = Field(
        default_factory=list,
        max_length=4,
        description="≤4 axis names from the same list.",
    )

    @model_validator(mode="after")
    def _one_cluster_per_row(self) -> Self:
        if not self.priority_fix.strip():
            raise ValueError("priority_fix is empty: name the one change")
        owner: dict[int, int] = {}
        for k, cluster in enumerate(self.failure_highlights, start=1):
            for row in set(cluster.sample_ids):
                if (first := owner.setdefault(row, k)) != k:
                    raise ValueError(
                        f"row {row} sits in clusters {first} and {k}: a row shows one mechanism"
                    )
        return self


FORK_REASON_MAX = 400


class ForkProposal(OptimizerResponseModel):
    """Carries no round offset: ``optimizers/potter/CLAUDE.md`` § The layer-control channel."""

    # Uncapped on the wire: a decoder held to a length cuts the reason mid-word.
    reason: Annotated[str, BeforeValidator(_truncate_marked(FORK_REASON_MAX))] = Field(
        default="",
        description="1-2 sentences: why this line is exhausted, and what the fork changes.",
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
        description="1-2 sentences naming the cause and what the operator must fix.",
    )


class L2ContextOutput(OptimizerResponseModel):
    """Carries neither ``task_context`` (operator-authored, frozen for the run) nor ``action``."""

    axis_targeted: Annotated[str, BeforeValidator(_truncate_marked(60))] = Field(
        default="",
        description=(
            "Where the failure cluster's fix lands in the TARGET prompt, as `<node>.<field>` for "
            "a semantic failure or a param for a quantitative one — even where it shares a name "
            "with an l1_layout slot. Name it on every fire; it is the anchor the lever is judged "
            "against, not a label for the lever."
        ),
    )
    # A plain dict on purpose: typed, one off-enum panel fails the whole model before `validate_l1_layout` can report it.
    l1_layout: Annotated[
        dict[str, list[str]],
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
        description=(
            "Why this fire, in 1-2 sentences: the failure cluster you are attacking and the "
            "named data point behind it — a sample id, a count, a yield number. Required on "
            "every fire, including one that pulls no lever; a lever with no diagnosis behind "
            "it cannot be told from a guess."
        ),
    )
    fork_proposal: ForkProposal | None = None


class L2RecombiningOutput(L2ContextOutput):
    recombinations: list[L1Recombination] = Field(default_factory=list)


# The reply carries `terminate_proposal` only where `InjectionBundle.terminate_offer` names a
# starved node: a model volunteers any field its schema carries, and an honored stop aborts.
class L2TerminatingOutput(L2ContextOutput):
    terminate_proposal: TerminateProposal | None = None


class L2RecombiningTerminatingOutput(L2RecombiningOutput, L2TerminatingOutput):
    pass


def recombinations_schema(offer: RecombinationOffer) -> dict[str, Any]:
    """The slot's wire shape, closed on the pool's id heads and the loci a main can take."""
    described = L1Recombination.model_fields
    spec = {
        "type": "object",
        "properties": {
            "main": {
                "type": "string",
                "enum": list(offer.mains),
                "description": described["main"].description,
            },
            "donor": {
                "type": "string",
                "enum": list(offer.ids),
                "description": described["donor"].description,
            },
            "from_donor": {
                "type": "array",
                "items": {"type": "string", "enum": list(offer.loci)},
                "minItems": 1,
                "description": described["from_donor"].description,
            },
            "changes_description": {"type": "string"},
        },
        "required": ["main", "donor", "from_donor", "changes_description"],
        "additionalProperties": False,
    }
    return {"type": "array", "items": spec, "maxItems": offer.children}


@functools.lru_cache(maxsize=4)
def build_l2_response_model(
    withheld: frozenset[str],
    recombination: RecombinationOffer | None = None,
    terminating: bool = False,
) -> type[L2ContextOutput]:
    """``None`` *recombination* offers no slot."""
    base: type[L2ContextOutput] = {
        (False, False): L2ContextOutput,
        (True, False): L2RecombiningOutput,
        (False, True): L2TerminatingOutput,
        (True, True): L2RecombiningTerminatingOutput,
    }[recombination is not None, terminating]
    fields: dict[str, Any] = {}
    if withheld:
        schema = layout_json_schema(
            NODE_LAYOUTS["l1_generate"], description=LAYOUT_SCHEMA_INSTRUCTION, withheld=withheld
        )
        fields["l1_layout"] = (
            Annotated[dict[str, list[str]], WithJsonSchema(schema)],
            Field(default_factory=dict),
        )
    if recombination is not None:
        fields["recombinations"] = (
            Annotated[list[L1Recombination], WithJsonSchema(recombinations_schema(recombination))],
            Field(default_factory=list),
        )
    if not fields:
        return base
    return create_model("L2ContextOutput", __base__=base, **fields)


class L3PlanOutput(OptimizerResponseModel):
    plan: Annotated[str, BeforeValidator(_truncate_marked(L3_PLAN_MAX_CHARS))] = Field(
        description=(
            "The strategy every later prompt reads until the next replan. Say what L2 and L1 "
            "should DO with the levers they hold — L2 moves which panels L1 sees and how wide it "
            "explores; L1 rewrites the target prompt's fields. A plan written in the solver's "
            "vocabulary instructs neither, and rides every downstream call regardless."
        ),
    )
    note: Annotated[str, BeforeValidator(_truncate_marked(150))] = ""
    rationale: Annotated[str, BeforeValidator(_truncate_marked(125))] = ""
    fork_proposal: ForkProposal | None = None


class L3TerminatingOutput(L3PlanOutput):
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
