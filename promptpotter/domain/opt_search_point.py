from __future__ import annotations

import re
from collections.abc import Callable, Iterable, Mapping, Sequence
from typing import TYPE_CHECKING, Annotated, Any, ClassVar, Literal, Self

from pydantic import ConfigDict, Field

from promptpotter.domain.pipeline_overlay import fold_output_contract, node_config_items
from promptpotter.domain.search_point import (
    PROMPT_STRING_FIELDS,
    JobSearchPoint,
    TaskDecomposition,
    strip_rendered_prompt,
)
from promptpotter.domain.strict_model import StrictModel
from promptpotter.shared.hashing import shapes_optimizer_prompt, stable_hash

if TYPE_CHECKING:
    from promptpotter.domain.pipeline_schema import PipelineSchema
    from promptpotter.domain.sample import Sample
    from promptpotter.domain.search_point import JobSearchPoint

__all__ = [
    "FEW_SHOT_BLOCK",
    "ORIGIN_SOURCE",
    "SHOTS_LOCUS",
    "TEMPLATE_TOKEN_RE",
    "EvidenceGrounding",
    "IndividualLineage",
    "Locus",
    "OptSearchPoint",
    "OptimizerPromptTemplate",
    "PromptTemplate",
    "Variation",
    "VariationMode",
    "locus_name",
    "node_source",
]


TEMPLATE_TOKEN_RE: Annotated[re.Pattern[str], shapes_optimizer_prompt] = re.compile(
    r"\{\{(\w+)\}\}"
)


FEW_SHOT_BLOCK: Annotated[str, shapes_optimizer_prompt] = "few_shot_block"
"""The rendered shots' key — last in a target render, and in the wire's ``prompt_fields``."""


def _check_render_order(cls: type[PromptTemplate]) -> None:
    """A field the order omits renders nowhere, silently; ``__init_subclass__`` runs this for every subclass."""
    if sorted(cls.RENDER_ORDER) != sorted(PROMPT_STRING_FIELDS):
        raise RuntimeError(
            f"{cls.__name__}.RENDER_ORDER must be a permutation of "
            f"PROMPT_STRING_FIELDS: an order chooses SEQUENCE, never membership."
        )


class PromptTemplate(StrictModel):
    def __init_subclass__(cls, **kwargs: Any) -> None:
        super().__init_subclass__(**kwargs)
        _check_render_order(cls)

    RENDER_ORDER: ClassVar[Annotated[tuple[str, ...], shapes_optimizer_prompt]] = tuple(
        PROMPT_STRING_FIELDS
    )
    """The TARGET prompt's order sits inside the archive key: moving it re-cuts every banked cell."""

    persona: str = ""
    task_intent: str = ""
    problem_description: str = ""
    instruction: str = ""
    thinking_style: str = ""
    answer_format: str = ""

    @shapes_optimizer_prompt
    def _ordered_pairs(self, value_of: Callable[[str], str]) -> list[tuple[str, str]]:
        return [(f, v) for f in type(self).RENDER_ORDER if (v := value_of(f))]

    @shapes_optimizer_prompt
    def render_fields(self) -> list[tuple[str, str]]:
        """Each field's OWN value — never a campaign's framing, which ``target_fields`` splices."""
        return self._ordered_pairs(lambda name: getattr(self, name))

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
        return {f: v for f in PROMPT_STRING_FIELDS if (v := getattr(self, f))}

    @classmethod
    def from_prompt_fields(cls, fields: dict[str, Any], **kwargs: Any) -> Self:
        return cls(**fields, **kwargs)


class OptimizerPromptTemplate(PromptTemplate):
    """A prompt the optimizer RUNS ON (`dispatch/llm_call`), never one it produces."""

    RENDER_ORDER: ClassVar[Annotated[tuple[str, ...], shapes_optimizer_prompt]] = (
        "persona",
        "task_intent",
        "instruction",
        "thinking_style",
        "answer_format",
        "problem_description",
    )
    """Prefix-cache order: a value that CHANGES between rounds goes in ``problem_description``, last."""


class EvidenceGrounding(StrictModel):
    """The panel field and citation L1 declares to justify a mutation."""

    model_config = ConfigDict(frozen=True)

    field: str = Field(description="A citable panel named in the prompt, or stall_exploration.")
    citation: str = Field(description="Short string naming the panel entry cited.")


ORIGIN_SOURCE = "origin"

SHOTS_LOCUS = "shot_ids"
"""The shots' locus: one ordered list, so its order is part of its value."""

Locus = str | tuple[str, str]
"""A prompt field by name, ``SHOTS_LOCUS``, or a ``(node, param)`` of the configuration."""

VariationMode = Literal["deterministic", "llm"]


def node_source(manifest: str, node: str) -> str:
    return f"{manifest}:{node}"


def locus_name(locus: Locus) -> str:
    return locus if isinstance(locus, str) else ".".join(locus)


class Variation(StrictModel):
    """One variation node's act: which node ran, whether it asked a model, and the loci it wrote."""

    model_config = ConfigDict(frozen=True)

    node: str = Field(description="`{manifest}:{node}` of the variation node that ran.")
    mode: VariationMode = Field(
        description="`deterministic`: a function of its inputs and the run's seed. `llm`: a "
        "model's reply."
    )
    loci: list[str] = Field(
        default_factory=list,
        description="The loci it left different from what it was handed, by `locus_name`: the "
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


def _resolved_config(pipeline_params: dict[str, Any], schema: PipelineSchema) -> dict[str, Any]:
    """Idempotent."""
    pp = strip_rendered_prompt(pipeline_params)
    if schema.active_steps:
        pp["steps"] = list(schema.active_steps)
    return fold_output_contract(pp, schema)


def _refuse_unwritable(names: Iterable[str]) -> None:
    """A configuration locus is written through a whole resolved configuration alone, never by name."""
    if unknown := [name for name in names if name not in (*PROMPT_STRING_FIELDS, SHOTS_LOCUS)]:
        raise ValueError(f"an individual is written by prompt field and shots, not {unknown}")


class OptSearchPoint(PromptTemplate):
    """The individual: a configuration of prompt fields, shots and node config, plus its lineage."""

    model_config = ConfigDict(extra="forbid")

    shot_ids: list[int] = Field(
        default_factory=list,
        description=(
            "Its few-shot shots, in render order, as ids of the campaign's demo pool — resolved "
            "to each row's query and ground truth only when the target prompt renders."
        ),
    )
    pipeline_params: dict[str, Any] = Field(
        default_factory=dict,
        description=(
            "Its node configuration, resolved: the active steps named, the output contract "
            "folded, no rendered prompt. `configured` is the one writer."
        ),
    )
    lineage: IndividualLineage = Field(default_factory=IndividualLineage)

    @property
    def id(self) -> str:
        """The individual IS its content, never stored: one configuration reached twice is one id."""
        return stable_hash([self.prompt_field_dict(), self.pipeline_params])

    def configured(self, pipeline_params: dict[str, Any] | None, schema: PipelineSchema) -> Self:
        resolved = _resolved_config(pipeline_params or {}, schema)
        return self.model_copy(update={"pipeline_params": resolved})

    def prompt_field_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = dict(self.prompt_fields())
        if self.shot_ids:
            d["shot_ids"] = list(self.shot_ids)
        return d

    @shapes_optimizer_prompt
    def _render_few_shot_block(self, demo: Sequence[Sample]) -> str:
        rows = {s.id: s for s in demo}
        if missing := [i for i in self.shot_ids if i not in rows]:
            raise ValueError(f"shot ids {missing} are not in the demo pool this render was handed")
        return "\n".join(
            f"Input: {rows[i].query}\nOutput: {rows[i].ground_truth}" for i in self.shot_ids
        )

    @shapes_optimizer_prompt
    def target_fields(
        self, framing: TaskDecomposition, *, demo: Sequence[Sample]
    ) -> list[tuple[str, str]]:
        """What is SCORED, not ``render()``; an EMPTY ``problem_description`` still renders the framing."""

        def value_of(name: str) -> str:
            v: str = getattr(self, name)
            if name != "problem_description":
                return v
            if not (framing.upstream_context or framing.downstream_context):
                return v
            parts = (framing.upstream_context, v, framing.downstream_context)
            return "\n\n".join(part for part in parts if part)

        pairs = self._ordered_pairs(value_of)
        if block := self._render_few_shot_block(demo):
            pairs.append((FEW_SHOT_BLOCK, block))
        return pairs

    @shapes_optimizer_prompt
    def render_target(self, framing: TaskDecomposition, *, demo: Sequence[Sample]) -> str:
        return "\n\n".join(v for _, v in self.target_fields(framing, demo=demo))

    def to_job_search_point(
        self,
        *,
        schema: PipelineSchema,
        framing: TaskDecomposition,
        demo: Sequence[Sample],
    ) -> JobSearchPoint:
        pp = _resolved_config(self.pipeline_params, schema)
        prompt_nodes = schema.prompt_node_names()
        prompt_node = prompt_nodes[0] if prompt_nodes else ""
        pairs = self.target_fields(framing, demo=demo)
        rendered = "\n\n".join(v for _, v in pairs)
        if rendered and prompt_node:
            pp.setdefault(prompt_node, {})["prompt"] = rendered

        pf: dict[str, Any] = {}
        if rendered and prompt_node:
            pf = self.prompt_fields()
            if block := dict(pairs).get(FEW_SHOT_BLOCK):
                pf[FEW_SHOT_BLOCK] = block

        return JobSearchPoint(
            pipeline_params=pp,
            prompt_fields=pf,
        )

    def as_origin(self, *, changes_description: str) -> OptSearchPoint:
        lineage = IndividualLineage(changes_description=changes_description)
        return self.model_copy(update={"lineage": lineage})

    def loci(self) -> dict[Locus, Any]:
        loci: dict[Locus, Any] = {f: getattr(self, f) for f in PROMPT_STRING_FIELDS}
        loci[SHOTS_LOCUS] = tuple(self.shot_ids)
        for node, config in node_config_items(self.pipeline_params):
            for param, value in config.items():
                loci[(node, param)] = value
        return loci

    def loci_moved_from(self, before: OptSearchPoint) -> list[str]:
        mine, theirs = self.loci(), before.loci()
        return [
            locus_name(locus)
            for locus in dict.fromkeys((*theirs, *mine))
            if (locus in mine, mine.get(locus)) != (locus in theirs, theirs.get(locus))
        ]

    @classmethod
    def derive(
        cls,
        parents: Sequence[OptSearchPoint],
        *,
        variation: Variation,
        take: Mapping[str, int] | None = None,
        config: tuple[dict[str, Any], PipelineSchema] | None = None,
        changes_description: str = "",
        evidence_grounding: EvidenceGrounding | None = None,
        **changes: Any,
    ) -> OptSearchPoint:
        """*take* maps a locus to its donor's position in *parents*; a configuration locus comes from *config* alone."""
        base = parents[0]
        values = base.loci()
        _refuse_unwritable((*(take or {}), *changes))
        for locus, donor in (take or {}).items():
            values[locus] = parents[donor].loci()[locus]
        values.update(changes)
        child = cls(
            **{f: values[f] for f in PROMPT_STRING_FIELDS},
            shot_ids=list(values[SHOTS_LOCUS]),
            pipeline_params=base.pipeline_params,
        )
        if config is not None:
            child = child.configured(*config)
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
        changes_description: str | None = None,
        **changes: Any,
    ) -> OptSearchPoint:
        _refuse_unwritable(changes)
        after = self.model_copy(update=changes)
        written = after.loci_moved_from(self)
        update: dict[str, Any] = {}
        if written:
            step = variation.model_copy(update={"loci": written})
            update["variations"] = [*self.lineage.variations, step]
        if changes_description is not None:
            update["changes_description"] = changes_description
        return after.model_copy(update={"lineage": self.lineage.model_copy(update=update)})


_check_render_order(PromptTemplate)
