"""The individual and the prompt scheme under it — models only. An optimizer's own state is not
here: it rides ``optimizer_state.py``.

The ``pipeline_params`` SHAPE lives in ``pipeline_overlay.py`` and the delta / idea views in
``candidate_diff.py``; neither references a model here, and their consumers are disjoint from
this file's (connectors and the dispatcher take the overlay, validators and renderers take the
diff). One edge crosses back: ``to_job_search_point`` folds schema descriptions."""

from __future__ import annotations

import copy
import re
import uuid
from collections.abc import Callable, Sequence
from typing import TYPE_CHECKING, Annotated, Any, ClassVar, Self

from pydantic import ConfigDict, Field

from promptpotter.config.settings import PROMPT_STRING_FIELDS
from promptpotter.domain.l1_layout import L1_LAYOUT_SLOTS, VOLATILE_SLOT
from promptpotter.domain.pipeline_overlay import fold_output_contract
from promptpotter.domain.search_point import JobSearchPoint, SearchPoint, TaskDecomposition
from promptpotter.domain.strict_model import StrictModel
from promptpotter.shared.hashing import shapes_optimizer_prompt

if TYPE_CHECKING:
    from promptpotter.domain.pipeline_schema import PipelineSchema
    from promptpotter.domain.search_point import JobSearchPoint

__all__ = [
    "ORIGIN_SOURCE",
    "TEMPLATE_TOKEN_RE",
    "EvidenceGrounding",
    "FewShotExample",
    "IndividualLineage",
    "OptSearchPoint",
    "OptimizerPromptTemplate",
    "PromptTemplate",
    "node_source",
]


# The `{{token}}` shape `compile_prompt` substitutes — the ONE definition every
# reader of a template's token set shares (dispatch-hub fill/validate, the
# optimizer prompt port guard in `validators/l1_strict.py`).
TEMPLATE_TOKEN_RE: Annotated[re.Pattern[str], shapes_optimizer_prompt] = re.compile(
    r"\{\{(\w+)\}\}"
)


class FewShotExample(StrictModel):
    """An input/output pair used as a few-shot demonstration."""

    input: str
    output: str
    explanation: str | None = None


def _check_render_order(cls: type[PromptTemplate]) -> None:
    """A field the order omits renders nowhere — silently, in a prompt.

    Fired from ``__init_subclass__`` rather than against a hand-listed pair of classes at import:
    the pair only ever named the classes in THIS module, so a fourth rendering class defined
    anywhere else would have shipped its own order unchecked. Class creation is the one event every
    subclass has, wherever it lives."""
    if sorted(cls.RENDER_ORDER) != sorted(PROMPT_STRING_FIELDS):
        raise RuntimeError(
            f"{cls.__name__}.RENDER_ORDER must be a permutation of "
            f"PROMPT_STRING_FIELDS: an order chooses SEQUENCE, never membership."
        )


class PromptTemplate(SearchPoint):
    """The scheme shared by job + optimizer prompts: the six ``render()`` decomposition fields
    (``PROMPT_STRING_FIELDS``), plus ``few_shot_examples``, which renders separately."""

    def __init_subclass__(cls, **kwargs: Any) -> None:
        super().__init_subclass__(**kwargs)
        _check_render_order(cls)

    RENDER_ORDER: ClassVar[Annotated[tuple[str, ...], shapes_optimizer_prompt]] = tuple(
        PROMPT_STRING_FIELDS
    )
    """Order ``render()`` concatenates the decomposition fields in — the TARGET prompt's, so it sits
    inside the measurement archive's ``node_configs`` key and moving it re-cuts every banked cell.
    ``OptimizerPromptTemplate`` is the one class that orders otherwise."""

    persona: str = ""
    task_intent: str = ""
    problem_description: str = ""
    instruction: str = ""
    thinking_style: str = ""
    answer_format: str = ""
    few_shot_examples: list[FewShotExample] = Field(default_factory=list)

    @shapes_optimizer_prompt
    def _ordered_pairs(self, value_of: Callable[[str], str]) -> list[tuple[str, str]]:
        pairs = [(f, v) for f in type(self).RENDER_ORDER if (v := value_of(f))]
        if block := self._render_few_shot_block():
            pairs.append(("few_shot_examples", block))
        return pairs

    @shapes_optimizer_prompt
    def render_fields(self) -> list[tuple[str, str]]:
        """Each field's OWN value — never a campaign's framing, which ``target_fields`` splices."""
        return self._ordered_pairs(lambda name: getattr(self, name))

    @shapes_optimizer_prompt
    def render(self) -> str:
        return "\n\n".join(v for _, v in self.render_fields())

    @shapes_optimizer_prompt
    def _render_few_shot_block(self) -> str:
        if not self.few_shot_examples:
            return ""
        lines: list[str] = []
        for ex in self.few_shot_examples:
            lines.append(f"Input: {ex.input}\nOutput: {ex.output}")
            if ex.explanation:
                lines.append(f"Explanation: {ex.explanation}")
        return "\n".join(lines)

    @shapes_optimizer_prompt
    def compile_prompt(self, **kwargs: str | int) -> str:
        """Any ``{{…}}`` left after substitution stays LITERAL: an evolved node prompt echoed into an
        optimizer template carries the backend's own placeholders, which the backend fills, not us."""
        text = self.render()
        for key, value in kwargs.items():
            text = text.replace("{{" + key + "}}", str(value))
        return text

    def prompt_fields(self) -> dict[str, str]:
        """String-only projection (no few-shot) for L1 summaries + validator diffs."""
        return {f: v for f in PROMPT_STRING_FIELDS if (v := getattr(self, f))}

    def prompt_field_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = dict(self.prompt_fields())
        if self.few_shot_examples:
            d["few_shot_examples"] = [ex.model_dump() for ex in self.few_shot_examples]
        return d

    @classmethod
    def from_prompt_fields(cls, fields: dict[str, Any], **kwargs: Any) -> Self:
        fields = dict(fields)
        fse = fields.pop("few_shot_examples", [])
        if fse and isinstance(fse[0], dict):
            fse = [FewShotExample(**ex) for ex in fse]
        return cls(few_shot_examples=fse, **fields, **kwargs)


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
    """Ordered for the provider's prefix cache, apart from the target's order so shaping a cache
    prefix here cannot re-cut a banked measurement.

    ``problem_description`` renders LAST because it is where the evidence goes: it is
    `l1_layout.py::VOLATILE_SLOT`, the slot every `NODE_LAYOUTS` floor fills, so anything rendered
    after it would sit behind panels that change every round and could never be served off a
    provider's prefix cache.

    **The corollary binds the prompts, not just this tuple: a value that CHANGES between rounds
    belongs in ``problem_description``, never in a field ahead of it** — a moving menu substituted
    one slot early voids the stable prefix from inside a static template. Ordering the fields is
    half the contract; keeping the
    moving values behind the boundary is the other half, and the half nothing can assert: the
    layout axis addresses the earlier slots too, so `validate_l1_layout` REPORTS a panel placed
    ahead of the boundary (`l1_layout_voids_prefix`) rather than the order alone guaranteeing it.

    A constant ahead of the boundary is free, and the exemption is declared rather than assumed —
    `PREFIX_STABLE_PANELS`, whose one member is `task_context`; the shared prefix measurably
    survives all of `task_intent`."""


class EvidenceGrounding(StrictModel):
    """Panel field + citation L1 declares to justify a mutation.

    The set of citable panels is not declared anywhere: it is DERIVED per round from the
    node's live layout (``dispatch.injections.registry.citable_fields``), so L1 can only
    cite a panel it was actually shown."""

    model_config = ConfigDict(frozen=True)

    field: str = Field(description="A citable panel named in the prompt, or stall_exploration.")
    citation: str = Field(description="Short string naming the panel entry cited.")


ORIGIN_SOURCE = "origin"


def node_source(manifest: str, node: str) -> str:
    return f"{manifest}:{node}"


class IndividualLineage(StrictModel):
    """Identity + provenance — set once at creation, never mutated."""

    id: str = Field(default_factory=lambda: uuid.uuid4().hex)
    parent_ids: list[str] = Field(
        default_factory=list,
        description=(
            "Every individual this one derives from — one for a mutation, several for a "
            "crossover; empty at the origin. A tree view hangs it under `parent_ids[0]`."
        ),
    )
    changes_description: str = ""
    source: str = Field(
        default="",
        description=(
            "`{manifest}:{node}` of the node that proposed it (`potter:l1_generate`); "
            "`origin` for an individual the bench minted."
        ),
    )
    evidence_grounding: EvidenceGrounding | None = None


class OptSearchPoint(PromptTemplate):
    """The individual: prompt structure + lineage.

    The campaign's framing and every optimizer's working state ride elsewhere, so a derive can
    carry neither."""

    model_config = ConfigDict(extra="forbid")

    lineage: IndividualLineage = Field(default_factory=IndividualLineage)

    @shapes_optimizer_prompt
    def target_fields(self, framing: TaskDecomposition) -> list[tuple[str, str]]:
        """The campaign's framing spliced up/downstream of ``problem_description`` — which may be
        EMPTY, and the context still renders. This render, not ``render()``, is what is scored."""

        def value_of(name: str) -> str:
            v: str = getattr(self, name)
            if name != "problem_description":
                return v
            if not (framing.upstream_context or framing.downstream_context):
                return v
            parts = (framing.upstream_context, v, framing.downstream_context)
            return "\n\n".join(part for part in parts if part)

        return self._ordered_pairs(value_of)

    @shapes_optimizer_prompt
    def render_target(self, framing: TaskDecomposition) -> str:
        return "\n\n".join(v for _, v in self.target_fields(framing))

    def to_job_search_point(
        self,
        base_pipeline_params: dict[str, Any] | None = None,
        *,
        schema: PipelineSchema,
        framing: TaskDecomposition,
    ) -> JobSearchPoint:
        """*schema* is REQUIRED: without one this produced a valid-looking point carrying neither the
        rendered prompt nor ``steps``, and that point is scored and archived like any other."""

        pp = copy.deepcopy(base_pipeline_params or {})
        active_steps = schema.active_steps
        prompt_nodes = schema.prompt_node_names()
        prompt_node = prompt_nodes[0] if prompt_nodes else ""
        if active_steps:
            pp["steps"] = list(active_steps)
        rendered = self.render_target(framing)
        if rendered and prompt_node:
            pp.setdefault(prompt_node, {})["prompt"] = rendered

        # Resolve the structured-output contract onto the wire: fold the accumulated description
        # keys into each node's real `output_schema` prose and drop the virtual keys, and strip the
        # contract entirely where this point chose to answer in text.
        # `schema` resolves the registry-declared case (`schema_family`, no inline schema).
        fold_output_contract(pp, schema)

        pf: dict[str, Any] = {}
        if rendered and prompt_node:
            pf = {f: v for f, v in self.prompt_field_dict().items() if f != "few_shot_examples"}
            block = self._render_few_shot_block()
            if block:
                pf["few_shot_block"] = block

        return JobSearchPoint(
            pipeline_params=pp,
            prompt_fields=pf,
        )

    @classmethod
    def derive(
        cls,
        parents: Sequence[OptSearchPoint],
        *,
        source: str,
        changes_description: str = "",
        evidence_grounding: EvidenceGrounding | None = None,
        **changes: Any,
    ) -> OptSearchPoint:
        """``parents[0]`` supplies every field *changes* leaves unset — a crossover names its
        recombined fields explicitly."""
        base = parents[0]
        data: dict[str, Any] = {}
        for f in PROMPT_STRING_FIELDS:
            data[f] = changes.pop(f, getattr(base, f))
        fse = changes.pop("few_shot_examples", None)
        if fse is not None:
            if fse and isinstance(fse[0], dict):
                fse = [FewShotExample(**ex) for ex in fse]
            data["few_shot_examples"] = fse
        else:
            data["few_shot_examples"] = [ex.model_copy() for ex in base.few_shot_examples]
        data["lineage"] = IndividualLineage(
            parent_ids=[p.lineage.id for p in parents],
            changes_description=changes_description,
            source=source,
            evidence_grounding=evidence_grounding,
        )
        data.update(changes)
        return cls(**data)


_check_render_order(PromptTemplate)

# The prefix-cache half of the same contract, true of the optimizer prompt alone — the target's
# order answers to the archive key, not a cache.
#
# `l1_layout.py` cannot assert this itself (domain import direction: it is imported BY this
# module), so the reading lives on the importer. Two claims, both load-bearing: the volatile slot
# renders last, and the layout's slot sequence is the render sequence — without the second,
# `L1_LAYOUT_SLOTS[:-1]` is not "the slots ahead of the boundary" and `validate_l1_layout`'s
# prefix check reads the wrong ones.
_OPTIMIZER_ORDER = OptimizerPromptTemplate.RENDER_ORDER
assert _OPTIMIZER_ORDER[-1] == VOLATILE_SLOT, (
    f"the optimizer prompt must render {VOLATILE_SLOT!r} last — it is where every NODE_LAYOUTS "
    f"floor puts its evidence, so a field behind it can never sit in a provider's stable prefix."
)
assert [f for f in _OPTIMIZER_ORDER if f in L1_LAYOUT_SLOTS] == list(L1_LAYOUT_SLOTS), (
    f"L1_LAYOUT_SLOTS {L1_LAYOUT_SLOTS} must be a subsequence of RENDER_ORDER "
    f"{_OPTIMIZER_ORDER} — the layout is declared in render order so that "
    f"'ahead of the boundary' means the same thing in both modules."
)
