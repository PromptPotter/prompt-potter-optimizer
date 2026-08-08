"""The optimizer's working state and the prompt scheme under it — models only.

The ``pipeline_params`` SHAPE lives in ``pipeline_overlay.py`` and the delta / idea views in
``candidate_diff.py``; neither references a model here, and their consumers are disjoint from
this file's (connectors and the dispatcher take the overlay, validators and renderers take the
diff). One edge crosses back: ``to_job_search_point`` folds schema descriptions."""

from __future__ import annotations

import copy
import re
import uuid
from typing import TYPE_CHECKING, Any, Self

from pydantic import ConfigDict, Field, field_validator

from promptpotter.config.settings import PROMPT_STRING_FIELDS
from promptpotter.domain.escalation_signals import RuntimeFailure, ValidationFailure
from promptpotter.domain.l1_layout import L1Layout, default_l1_layout
from promptpotter.domain.pipeline_overlay import fold_schema_descriptions
from promptpotter.domain.search_point import SearchPoint, TaskDecomposition
from promptpotter.domain.strict_model import StrictModel
from promptpotter.domain.validators import ValidatorOutcome

if TYPE_CHECKING:
    from promptpotter.domain.pipeline_schema import PipelineSchema
    from promptpotter.domain.search_point import JobSearchPoint

__all__ = [
    "TEMPLATE_TOKEN_RE",
    "EvidenceGrounding",
    "FewShotExample",
    "IndividualLineage",
    "L2L3Memory",
    "OptSearchPoint",
    "PromptTemplate",
    "WoundChannels",
]


# The `{{token}}` shape `compile_prompt` substitutes — the ONE definition every
# reader of a template's token set shares (dispatch-hub fill/validate, the
# optimizer prompt port guard in `validators/l1_strict.py`).
TEMPLATE_TOKEN_RE = re.compile(r"\{\{(\w+)\}\}")


class FewShotExample(StrictModel):
    """An input/output pair used as a few-shot demonstration."""

    input: str
    output: str
    explanation: str | None = None


class PromptTemplate(SearchPoint):
    """The scheme shared by job + optimizer prompts: the six ``render()`` decomposition fields
    (``PROMPT_STRING_FIELDS``), plus ``few_shot_examples`` and ``plan``, which render separately."""

    persona: str = ""
    task_intent: str = ""
    problem_description: str = ""
    instruction: str = ""
    thinking_style: str = ""
    answer_format: str = ""
    few_shot_examples: list[FewShotExample] = Field(default_factory=list)
    plan: str = Field(
        default="",
        description=(
            "Strategic frame written by ``l3_plan`` and read by every layer "
            "next round; persistent until the next L3 fire. Empty until L3 "
            "fires for the first time."
        ),
    )

    def render_fields(self) -> list[tuple[str, str]]:
        pairs = [(f, v) for f in PROMPT_STRING_FIELDS if (v := self._field_value(f))]
        if block := self._render_few_shot_block():
            pairs.append(("few_shot_examples", block))
        return pairs

    def render(self) -> str:
        return "\n\n".join(v for _, v in self.render_fields())

    def _field_value(self, name: str) -> str:
        """Subclass override point — see ``OptSearchPoint`` for task-context splicing."""
        value: str = getattr(self, name)
        return value

    def _render_few_shot_block(self) -> str:
        if not self.few_shot_examples:
            return ""
        lines: list[str] = []
        for ex in self.few_shot_examples:
            lines.append(f"Input: {ex.input}\nOutput: {ex.output}")
            if ex.explanation:
                lines.append(f"Explanation: {ex.explanation}")
        return "\n".join(lines)

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
        """``plan`` rides here — and restores through ``from_prompt_fields`` — so a seed or fork inherits
        the L3 frame. It is not in ``render()``, so carrying it leaves render identity untouched."""
        d: dict[str, Any] = dict(self.prompt_fields())
        if self.few_shot_examples:
            d["few_shot_examples"] = [ex.model_dump() for ex in self.few_shot_examples]
        if self.plan:
            d["plan"] = self.plan
        return d

    @classmethod
    def from_prompt_fields(cls, fields: dict[str, Any], **kwargs: Any) -> Self:
        fields = dict(fields)
        fse = fields.pop("few_shot_examples", [])
        if fse and isinstance(fse[0], dict):
            fse = [FewShotExample(**ex) for ex in fse]
        return cls(few_shot_examples=fse, **fields, **kwargs)


class EvidenceGrounding(StrictModel):
    """Panel field + citation L1 declares to justify a mutation.

    The set of citable panels is not declared anywhere: it is DERIVED per round from the
    node's live layout (``dispatch.injections.registry.citable_fields``), so L1 can only
    cite a panel it was actually shown."""

    model_config = ConfigDict(frozen=True)

    field: str = Field(description="A citable panel named in the prompt, or stall_exploration.")
    citation: str = Field(description="Short string naming the panel entry cited.")


class WoundChannels(StrictModel):
    """Four wound streams + sticky L3 note; rendered by dispatch-hub injections."""

    l3_note: str = ""
    validation_failures: list[ValidationFailure] = Field(default_factory=list)
    runtime_failures: list[RuntimeFailure] = Field(default_factory=list)
    l2_guard_breaches: list[ValidatorOutcome] = Field(default_factory=list)
    l3_guard_breaches: list[ValidatorOutcome] = Field(default_factory=list)


class IndividualLineage(StrictModel):
    """Identity + provenance — set once at creation, never mutated."""

    id: str = Field(default_factory=lambda: uuid.uuid4().hex)
    parent_id: str | None = None
    changes_description: str = ""
    source: str = Field(
        default="",
        description=(
            "'origin' / 'l1_generate' / 'l2_context' / 'l3_plan' / 'fork_seed' / 'campaign_origin'."
        ),
    )
    evidence_grounding: EvidenceGrounding | None = None


class L2L3Memory(StrictModel):
    """L2/L3-authored state that travels with the candidate.

    Bundled together because all four are authored by the escalation layers
    (L2 writes most; L3 writes ``wounds.l3_note`` + ``wounds.l3_guard_breaches``)
    and consumed by the dispatch-hub injections that compose the four
    optimizer prompts. ``OptSearchPoint.copy_memory_to`` deep-copies the
    whole bundle on L2/L3 adopt; ``OptSearchPoint.mutate`` (L1 child)
    inherits ``task_context`` + ``l1_overrides`` and resets the other two
    to defaults — the propagation asymmetry lives in those two methods.
    """

    wounds: WoundChannels = Field(
        default_factory=WoundChannels,
        description=(
            "Four wound streams (validation/runtime/l2-guard/l3-guard) + "
            "sticky L3 note. Rendered by dispatch-hub injections; absorbed "
            "by L2 next round."
        ),
    )
    l1_layout: L1Layout = Field(
        default_factory=default_l1_layout,
        description=(
            "L2-authored ordered list of injection slots that "
            "``DispatchHub.fill`` walks to compose the L1 optimizer prompt. "
            "L2's primary lever for changing what evidence L1 sees."
        ),
    )
    l1_overrides: dict[str, Any] = Field(
        default_factory=dict,
        description=(
            "Per-individual L1 optimizer prompt overrides keyed by the surface "
            "field name (``persona``, ``instruction``, …). L2 writes here "
            "to nudge L1 without rewriting the shared optimizer prompt."
        ),
    )
    task_context: TaskDecomposition = Field(
        default_factory=TaskDecomposition,
        description=(
            "Persistent task-framing dict refined by ``l2_context`` and "
            "spliced around ``problem_description`` at render time. "
            "Accumulative: each L2 fire merges deltas rather than "
            "rewriting wholesale."
        ),
    )

    @field_validator("task_context", mode="before")
    @classmethod
    def _coerce_task_context(cls, v: Any) -> TaskDecomposition:
        return TaskDecomposition.coerce(v)


class OptSearchPoint(PromptTemplate):
    """Optimizer working state: prompt fields + lineage + L2/L3 memory."""

    model_config = ConfigDict(extra="forbid")

    lineage: IndividualLineage = Field(default_factory=IndividualLineage)
    memory: L2L3Memory = Field(default_factory=L2L3Memory)

    def copy_memory_to(self, target: OptSearchPoint) -> None:
        """Deep-copy the L2/L3 memory onto *target* for L2/L3 adopt."""
        target.memory = self.memory.model_copy(deep=True)

    def _field_value(self, name: str) -> str:
        """Splice ``task_context`` up/downstream context around ``problem_description`` — which may
        be EMPTY, and they still render; they are mutable because they reach the target prompt."""
        v: str = getattr(self, name)
        if name != "problem_description":
            return v
        tc = self.memory.task_context
        if not (tc.upstream_context or tc.downstream_context):
            return v
        return "\n\n".join(p for p in (tc.upstream_context, v, tc.downstream_context) if p)

    def to_job_search_point(
        self,
        base_pipeline_params: dict[str, Any] | None = None,
        *,
        schema: PipelineSchema,
    ) -> JobSearchPoint:
        """*schema* is REQUIRED: without one this produced a valid-looking point carrying neither the
        rendered prompt nor ``steps``, and that point is scored and archived like any other."""
        from promptpotter.domain.search_point import JobSearchPoint

        pp = copy.deepcopy(base_pipeline_params or {})
        active_steps = schema.active_steps
        prompt_nodes = schema.prompt_node_names()
        prompt_node = prompt_nodes[0] if prompt_nodes else ""
        if active_steps:
            pp["steps"] = list(active_steps)
        rendered = self.render()
        if rendered and prompt_node:
            pp.setdefault(prompt_node, {})["prompt"] = rendered

        # Fold the accumulated `output_schema_descriptions` into each node's real
        # `output_schema` prose and drop the virtual key — the wire carries a valid schema.
        # `schema` resolves the registry-declared case (`schema_family`, no inline schema).
        fold_schema_descriptions(pp, schema)

        pf = None
        if rendered and prompt_node:
            pf = {f: v for f, v in self.prompt_field_dict().items() if f != "few_shot_examples"}
            block = self._render_few_shot_block()
            if block:
                pf["few_shot_block"] = block

        return JobSearchPoint(
            pipeline_params=pp,
            prompt_fields=pf or None,
        )

    def mutate(self, **changes: Any) -> OptSearchPoint:
        """Prompt fields, ``task_context`` and ``l1_overrides`` inherit; ``wounds`` / ``l1_layout`` reset.
        Those two flow on L2/L3 adopt through ``copy_memory_to`` instead."""
        data: dict[str, Any] = {}
        for f in PROMPT_STRING_FIELDS:
            data[f] = changes.pop(f, getattr(self, f))
        fse = changes.pop("few_shot_examples", None)
        if fse is not None:
            if fse and isinstance(fse[0], dict):
                fse = [FewShotExample(**ex) for ex in fse]
            data["few_shot_examples"] = fse
        else:
            data["few_shot_examples"] = [ex.model_copy() for ex in self.few_shot_examples]
        data["memory"] = L2L3Memory(
            l1_overrides=changes.pop("l1_overrides", dict(self.memory.l1_overrides)),
            task_context=changes.pop("task_context", self.memory.task_context.to_dict()),
        )
        data["plan"] = changes.pop("plan", self.plan)
        data["lineage"] = IndividualLineage(
            parent_id=self.lineage.id,
            changes_description=changes.pop("changes_description", ""),
            source=changes.pop("source", ""),
            evidence_grounding=changes.pop("evidence_grounding", None),
        )
        data.update(changes)
        return OptSearchPoint(**data)
