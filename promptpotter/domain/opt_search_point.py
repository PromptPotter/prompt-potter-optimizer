from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from typing import TYPE_CHECKING, Annotated, Any, ClassVar, Literal, NamedTuple, Self

from pydantic import Field, field_validator

from promptpotter.domain.pipeline_overlay import (
    apply_node_overlay,
    fold_output_contract,
    node_config_items,
    pin_default_routes,
)
from promptpotter.domain.search_point import (
    SHOTS_KEY,
    JobSearchPoint,
    TaskDecomposition,
    strip_rendered_prompt,
)
from promptpotter.domain.strict_model import FrozenModel, StrictModel
from promptpotter.shared.hashing import shapes_optimizer_prompt, stable_hash

if TYPE_CHECKING:
    from promptpotter.domain.pipeline_schema import PipelineNode, PipelineSchema
    from promptpotter.domain.sample import Sample


TEMPLATE_TOKEN_RE: Annotated[re.Pattern[str], shapes_optimizer_prompt] = re.compile(
    r"\{\{(\w+)\}\}"
)


FEW_SHOT_BLOCK: Annotated[str, shapes_optimizer_prompt] = "few_shot_block"
"""The rendered shots' key — last in a target render, and in the wire's ``prompt_fields``."""


@shapes_optimizer_prompt
def render_prompt(fields: Mapping[str, str], order: Sequence[str]) -> str:
    """A field *order* omits renders nowhere, so the caller refuses one first."""
    return "\n\n".join(v for f in order if (v := fields.get(f, "")))


PROMPT_FIELDS: Annotated[tuple[str, ...], shapes_optimizer_prompt] = (
    "persona",
    "task_intent",
    "problem_description",
    "instruction",
    "thinking_style",
    "answer_format",
)
"""The six fields a check-in decomposes a task into, in the order a prompt node declares them."""


class OptimizerPromptTemplate(StrictModel):
    """A prompt the optimizer RUNS ON (`dispatch/llm_call`), never one it produces."""

    RENDER_ORDER: ClassVar[Annotated[tuple[str, ...], shapes_optimizer_prompt]] = (
        *(f for f in PROMPT_FIELDS if f != "problem_description"),
        "problem_description",
    )
    """Prefix-cache order: a value that CHANGES between rounds goes in ``problem_description``, last."""

    persona: str = ""
    task_intent: str = ""
    problem_description: str = ""
    instruction: str = ""
    thinking_style: str = ""
    answer_format: str = ""

    @shapes_optimizer_prompt
    def render_fields(self) -> list[tuple[str, str]]:
        return [(f, v) for f in self.RENDER_ORDER if (v := getattr(self, f))]

    @shapes_optimizer_prompt
    def render(self) -> str:
        return "\n\n".join(v for _, v in self.render_fields())

    @shapes_optimizer_prompt
    def compile_prompt(self, **kwargs: str | int) -> str:
        """Any ``{{…}}`` left after substitution stays LITERAL: it is the backend's placeholder to fill."""
        text = self.render()
        for key, value in kwargs.items():
            text = text.replace("{{" + key + "}}", str(value))
        return text

    def prompt_fields(self) -> dict[str, str]:
        return {f: v for f in self.RENDER_ORDER if (v := getattr(self, f))}


# A field the order omits renders nowhere — silently, in a prompt.
assert tuple(OptimizerPromptTemplate.model_fields) == PROMPT_FIELDS


class EvidenceGrounding(FrozenModel):
    """The panel field and citation L1 declares to justify a mutation."""

    field: str = Field(description="A citable panel named in the prompt, or stall_exploration.")
    citation: str = Field(description="Short string naming the panel entry cited.")


ORIGIN_SOURCE = "origin"
OPERATOR_SOURCE = "operator"


class Locus(NamedTuple):
    """``(node, key)``: a prompt field, the node's shots (``SHOTS_KEY``) or a config param — the roster says which."""

    node: str
    key: str

    @property
    def name(self) -> str:
        return f"{self.node}.{self.key}"


VariationMode = Literal["deterministic", "llm", "operator"]


def node_source(manifest: str, node: str) -> str:
    return f"{manifest}:{node}"


class Variation(FrozenModel):
    """One variation node's act: which node ran, whether it asked a model, and the loci it wrote."""

    node: str = Field(
        description="`{manifest}:{node}` of the variation node that ran; `operator` where a "
        "person wrote it."
    )
    mode: VariationMode = Field(
        description="`deterministic`: a function of its inputs and the run's seed. `llm`: a "
        "model's reply. `operator`: a person's own edit, submitted at the arm gate."
    )
    loci: list[str] = Field(
        default_factory=list,
        description="The loci it left different from what it was handed, as `node.key`: the "
        "first one's against the first parent, a later one's against the variation before it.",
    )


class IndividualLineage(StrictModel):
    """The provenance of one mint: its edges, variations and words; it names no individual."""

    parent_ids: list[str] = Field(
        default_factory=list,
        description=(
            "Every individual this one derives from — one for a mutation, several for a "
            "crossover; empty at the origin. A tree view hangs it under `parent_ids[0]`."
        ),
    )
    changes_description: str = ""
    variations: list[Variation] = Field(
        default_factory=list,
        description="Every variation node that wrote it, in the order they ran; empty for an "
        "individual the bench minted as an origin. A later node appends its own and rewrites none.",
    )
    evidence_grounding: EvidenceGrounding | None = None

    @property
    def source(self) -> str:
        """Its FIRST variation, which no later one replaces; ``origin`` where the bench minted it."""
        return self.variations[0].node if self.variations else ORIGIN_SOURCE


class NodePrompt(FrozenModel):
    """One prompt node's loci: its fields by name and its shots, both written only through ``derive`` / ``edited``."""

    fields: dict[str, str] = Field(
        default_factory=dict,
        description="Each field it sets, by the name its node declares; an empty field is absent.",
    )
    shots: list[str] = Field(
        default_factory=list,
        description="Its few-shot shots in render order, as `Sample.key`s of the campaign's demo "
        "pool — resolved to each row's query and ground truth only when the prompt renders.",
    )

    @field_validator("fields")
    @classmethod
    def _empty_is_absent(cls, fields: dict[str, str]) -> dict[str, str]:
        return {f: text for f, text in fields.items() if text}

    def __bool__(self) -> bool:
        return bool(self.fields or self.shots)


def _resolved_config(pipeline_params: dict[str, Any], schema: PipelineSchema) -> dict[str, Any]:
    """Idempotent."""
    pp = strip_rendered_prompt(pipeline_params)
    if schema.active_steps:
        pp["steps"] = list(schema.active_steps)
    return pin_default_routes(fold_output_contract(pp, schema), schema)


class OptSearchPoint(StrictModel):
    """The individual: a value per locus of the roster — prompt fields and shots per prompt node, node config — plus its lineage."""

    prompts: dict[str, NodePrompt] = Field(
        default_factory=dict,
        description="Per prompt node, the loci it sets there; a node it sets nothing on is absent.",
    )
    pipeline_params: dict[str, Any] = Field(
        default_factory=dict,
        description=(
            "Its node configuration, resolved: the active steps named, the output contract "
            "folded, no rendered prompt. `configured` is the one writer."
        ),
    )
    lineage: IndividualLineage = Field(default_factory=IndividualLineage)

    @field_validator("prompts")
    @classmethod
    def _unset_is_absent(cls, prompts: dict[str, NodePrompt]) -> dict[str, NodePrompt]:
        return {node: prompt for node, prompt in prompts.items() if prompt}

    @property
    def id(self) -> str:
        """The individual IS its content, never stored: one configuration reached twice is one id."""
        return stable_hash([self.prompts_dump(), self.pipeline_params])

    def prompts_dump(self) -> dict[str, Any]:
        return {node: prompt.model_dump(mode="json") for node, prompt in self.prompts.items()}

    def configured(self, pipeline_params: dict[str, Any] | None, schema: PipelineSchema) -> Self:
        resolved = _resolved_config(pipeline_params or {}, schema)
        return self.model_copy(update={"pipeline_params": resolved})

    def prompt(self, node: str) -> NodePrompt:
        return self.prompts.get(node) or NodePrompt()

    @shapes_optimizer_prompt
    def fields_of(self, node: PipelineNode) -> dict[str, str]:
        """Its own text on *node*, refusing a field the node does not declare (it would render nowhere)."""
        fields = self.prompt(node.name).fields
        if undeclared := sorted(set(fields) - set(node.prompt_fields)):
            raise ValueError(
                f"node {node.name!r} declares the prompt fields {list(node.prompt_fields)}; this "
                f"individual sets {undeclared} there, which would render nowhere"
            )
        return fields

    @shapes_optimizer_prompt
    def render(self, node: PipelineNode) -> str:
        """Its own fields on *node* — never the campaign's framing nor its shots, which ``scored_prompts`` adds."""
        return render_prompt(self.fields_of(node), node.prompt_fields)

    @shapes_optimizer_prompt
    def _render_few_shot_block(self, node: str, demo: Sequence[Sample]) -> str:
        rows = {s.key: s for s in demo}
        shots = self.prompt(node).shots
        if missing := [k for k in shots if k not in rows]:
            raise ValueError(f"shots {missing} are not in the demo pool this render was handed")
        return "\n".join(f"Input: {rows[k].query}\nOutput: {rows[k].ground_truth}" for k in shots)

    @shapes_optimizer_prompt
    def _target_fields(
        self, node: PipelineNode, framing: TaskDecomposition, *, demo: Sequence[Sample]
    ) -> list[tuple[str, str]]:
        """What is SCORED on *node*, in its declared order; an EMPTY ``problem_description`` still renders the framing."""
        fields = self.fields_of(node)

        def value_of(name: str) -> str:
            v = fields.get(name, "")
            if name != "problem_description":
                return v
            if not (framing.upstream_context or framing.downstream_context):
                return v
            parts = (framing.upstream_context, v, framing.downstream_context)
            return "\n\n".join(part for part in parts if part)

        pairs = [(f, v) for f in node.prompt_fields if (v := value_of(f))]
        if block := self._render_few_shot_block(node.name, demo):
            pairs.append((FEW_SHOT_BLOCK, block))
        return pairs

    @shapes_optimizer_prompt
    def render_target(
        self, node: PipelineNode, framing: TaskDecomposition, *, demo: Sequence[Sample]
    ) -> str:
        return "\n\n".join(v for _, v in self._target_fields(node, framing, demo=demo))

    @shapes_optimizer_prompt
    def scored_prompts(
        self, schema: PipelineSchema, framing: TaskDecomposition, *, demo: Sequence[Sample]
    ) -> dict[str, dict[str, str]]:
        """Per prompt node, what is scored there; the framing splices into the ONE node declaring ``problem_description``."""
        prompt_nodes = schema.prompt_nodes()
        if off_roster := sorted(set(self.prompts) - {node.name for node in prompt_nodes}):
            raise ValueError(
                f"this individual sets prompt loci on {off_roster}, which the pipeline's chain "
                f"holds no prompt node for ({[node.name for node in prompt_nodes]})"
            )
        framed = [n.name for n in prompt_nodes if "problem_description" in n.prompt_fields]
        if (framing.upstream_context or framing.downstream_context) and len(framed) != 1:
            raise ValueError(
                "the campaign's framing splices into the one prompt node declaring "
                f"`problem_description`, and this pipeline's chain has {len(framed)}: {framed}"
            )
        unframed = TaskDecomposition()
        return {
            node.name: dict(pairs)
            for node in prompt_nodes
            if (
                pairs := self._target_fields(
                    node, framing if [node.name] == framed else unframed, demo=demo
                )
            )
        }

    def to_job_search_point(
        self,
        *,
        schema: PipelineSchema,
        framing: TaskDecomposition,
        demo: Sequence[Sample],
    ) -> JobSearchPoint:
        pp = _resolved_config(self.pipeline_params, schema)
        scored = self.scored_prompts(schema, framing, demo=demo)
        for node, fields in scored.items():
            pp.setdefault(node, {})["prompt"] = "\n\n".join(fields.values())
        return JobSearchPoint(pipeline_params=pp, prompt_fields=scored)

    def as_origin(self, *, changes_description: str) -> OptSearchPoint:
        lineage = IndividualLineage(changes_description=changes_description)
        return self.model_copy(update={"lineage": lineage})

    def loci(self) -> dict[Locus, Any]:
        loci: dict[Locus, Any] = {}
        for node, prompt in self.prompts.items():
            for field, text in prompt.fields.items():
                loci[Locus(node, field)] = text
            if prompt.shots:
                loci[Locus(node, SHOTS_KEY)] = tuple(prompt.shots)
        for node, config in node_config_items(self.pipeline_params):
            for param, value in config.items():
                if (locus := Locus(node, param)) in loci:
                    raise ValueError(f"{locus.name} is a prompt locus, and the config sets it too")
                loci[locus] = value
        return loci

    def loci_moved_from(self, before: OptSearchPoint) -> list[str]:
        mine, theirs = self.loci(), before.loci()
        return [
            locus.name
            for locus in dict.fromkeys((*theirs, *mine))
            if (locus in mine, mine.get(locus)) != (locus in theirs, theirs.get(locus))
        ]

    def _written(self, writes: Mapping[Locus, Any], schema: PipelineSchema) -> OptSearchPoint:
        """A config locus is laid on as an overlay is, so a new model drops the old one's route."""
        fields = {node: dict(prompt.fields) for node, prompt in self.prompts.items()}
        shots = {node: list(prompt.shots) for node, prompt in self.prompts.items()}
        overlay: dict[str, dict[str, Any]] = {}
        for locus, value in writes.items():
            match schema.locus_kind(*locus):
                case "prompt":
                    fields.setdefault(locus.node, {})[locus.key] = value
                case "shots":
                    shots[locus.node] = list(value)
                case "config":
                    overlay.setdefault(locus.node, {})[locus.key] = value
        written = OptSearchPoint(
            prompts={
                node: NodePrompt(fields=fields.get(node, {}), shots=shots.get(node, []))
                for node in sorted(fields.keys() | shots.keys())
            },
            pipeline_params=self.pipeline_params,
            lineage=self.lineage,
        )
        if not overlay:
            return written
        return written.configured(apply_node_overlay(self.pipeline_params, overlay, schema), schema)

    @classmethod
    def derive(
        cls,
        parents: Sequence[OptSearchPoint],
        *,
        schema: PipelineSchema,
        variation: Variation,
        take: Mapping[Locus, int] | None = None,
        changes: Mapping[Locus, Any] | None = None,
        changes_description: str = "",
        evidence_grounding: EvidenceGrounding | None = None,
    ) -> OptSearchPoint:
        """*take* maps a locus to its donor's position in *parents*; a donor that sets none empties it."""
        base = parents[0]
        writes: dict[Locus, Any] = {}
        for locus, donor in (take or {}).items():
            held = parents[donor].loci()
            writes[locus] = held[locus] if locus in held else _unset(locus, schema)
        writes.update(changes or {})
        child = base._written(writes, schema)
        lineage = IndividualLineage(
            parent_ids=[p.id for p in parents],
            changes_description=changes_description,
            variations=[variation.model_copy(update={"loci": child.loci_moved_from(base)})],
            evidence_grounding=evidence_grounding,
        )
        return child.model_copy(update={"lineage": lineage})

    def edited(
        self,
        variation: Variation,
        *,
        schema: PipelineSchema,
        changes: Mapping[Locus, Any],
        changes_description: str | None = None,
    ) -> OptSearchPoint:
        after = self._written(changes, schema)
        written = after.loci_moved_from(self)
        update: dict[str, Any] = {}
        if written:
            step = variation.model_copy(update={"loci": written})
            update["variations"] = [*self.lineage.variations, step]
        if changes_description is not None:
            update["changes_description"] = changes_description
        return after.model_copy(update={"lineage": self.lineage.model_copy(update=update)})


def _unset(locus: Locus, schema: PipelineSchema) -> Any:
    match schema.locus_kind(*locus):
        case "prompt":
            return ""
        case "shots":
            return ()
        case "config":
            raise KeyError(f"no donor value for {locus.name}: a config locus has no empty value")


def loci_of(edits: Mapping[str, Mapping[str, Any]]) -> dict[Locus, Any]:
    """``{node: {key: value}}`` — the nesting every node-keyed edit arrives in — as loci."""
    return {
        Locus(node, key): value for node, keyed in edits.items() for key, value in keyed.items()
    }
