"""OptSearchPoint — the optimizer's full working state.

Inherits ``PromptTemplate`` (8 prompt fields) and adds lineage, L2/L3 state,
and optimization memory. ``to_job_search_point()`` projects into a frozen
``JobSearchPoint`` for evaluation by rendering prompt fields into
``pipeline_params``.

Two-layer tracing:
  Target layer:    JobSearchPoint  → content_hash → dataset_runs/
  Optimizer layer: OptSearchPoint  → model_dump() → campaigns/{id}/trial.json

ADR-8: Mutable (not frozen). Updated in-place during the feedback cycle,
serialized via ``model_dump()`` at checkpoint time.
"""

from __future__ import annotations

import copy
import re
import uuid
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any, Self

from pydantic import BaseModel, ConfigDict, Field, field_validator

from promptpotter.domain.analysis import FailureAnalysis, RuntimeFailure, ValidationFailure
from promptpotter.domain.search_point import SearchPoint, TaskDecomposition
from promptpotter.shared.constants import PROMPT_STRING_FIELDS

if TYPE_CHECKING:
    from promptpotter.domain.pipeline_schema import PipelineSchema
    from promptpotter.domain.search_point import JobSearchPoint


class FewShotExample(BaseModel):
    """An input/output pair used as a few-shot demonstration."""

    input: str
    output: str
    explanation: str | None = None


class RoundSummary(BaseModel):
    """Compact per-round record persisted on ``OptimizationMemory.round_history``.

    Mirrors the trajectory-relevant fields of ``RoundResult`` — enough for
    ``build_trajectory_report`` (needs ``.accuracy``). Drops raw per-query
    results and per-candidate results, which remain on the transient
    ``Cycle.rounds`` list.
    """

    model_config = ConfigDict(arbitrary_types_allowed=True)

    round: int
    accuracy: float
    composite: float = 0.0
    improved: bool = False
    degraded_queries: int = 0
    pipeline_params: dict | None = None
    candidate_scores: list[dict] = Field(default_factory=list)


class PromptTemplate(SearchPoint):
    """The 8-field prompt decomposition scheme.

    Shared by job prompts (the prompt being optimized) and optimizer
    meta-prompts (L1/L2/L3/Critique templates). Each prompt uses the
    fields it needs; empty fields are skipped by ``render()``.

    Optimizer templates are loaded via ``load_optimizer_prompt()`` which
    returns ``PromptTemplate`` — callers use ``compile_prompt()`` to
    substitute ``{{variable}}`` placeholders, then pass to ``llm_call()``.
    """

    # -- Prompt decomposition fields ---------------------------------------
    persona: str = ""
    task_intent: str = ""
    problem_description: str = ""
    instruction: str = ""
    thinking_style: str = ""
    answer_format: str = ""
    few_shot_examples: list[FewShotExample] = Field(default_factory=list)

    # -- L3 state (rendered at end of prompt) ------------------------------
    plan: str = ""

    # -- SearchPoint interface ---------------------------------------------

    def render(self) -> str:
        """Assemble prompt decomposition fields into a prompt string.

        Skips empty fields. Sections separated by double newlines.
        Few-shot examples formatted as Input/Output pairs. Subclasses
        may override ``_field_value()`` to transform specific fields
        at render time without duplicating this loop.
        """
        parts = [v for f in PROMPT_STRING_FIELDS if (v := self._field_value(f))]
        block = self._render_few_shot_block()
        if block:
            parts.append(block)
        if self.plan:
            parts.append(self.plan)
        return "\n\n".join(parts)

    # -- Rendering helpers -------------------------------------------------

    def _field_value(self, name: str) -> str:
        """Return the value to render for a decomposition field.

        Subclass override point — used by ``OptSearchPoint`` to splice
        ``task_context.upstream_context`` / ``downstream_context`` around
        ``problem_description`` without re-implementing the full render loop.
        """
        return getattr(self, name)

    def _render_few_shot_block(self) -> str:
        """Format few-shot examples into a text block (empty string if none)."""
        if not self.few_shot_examples:
            return ""
        lines: list[str] = []
        for ex in self.few_shot_examples:
            lines.append(f"Input: {ex.input}\nOutput: {ex.output}")
            if ex.explanation:
                lines.append(f"Explanation: {ex.explanation}")
        return "\n".join(lines)

    def compile_prompt(self, **kwargs: str | int) -> str:
        """Render and substitute ``{{variable}}`` placeholders.

        Uses Langfuse-compatible double-brace syntax. Raises KeyError if
        template-defined variables remain unsubstituted.
        """
        text = self.render()
        expected = set(re.findall(r"\{\{(\w+)\}\}", text))
        for key, value in kwargs.items():
            text = text.replace("{{" + key + "}}", str(value))
        missing = expected - set(kwargs.keys())
        if missing:
            raise KeyError(f"Unsubstituted template variables: {sorted(missing)}")
        return text

    # -- Field extraction --------------------------------------------------

    def prompt_field_dict(self) -> dict[str, Any]:
        """Return prompt decomposition fields as a dict (for L1 candidate generation)."""
        d: dict[str, Any] = {}
        for f in PROMPT_STRING_FIELDS:
            v = getattr(self, f)
            if v:
                d[f] = v
        if self.few_shot_examples:
            d["few_shot_examples"] = [ex.model_dump() for ex in self.few_shot_examples]
        return d

    @classmethod
    def from_prompt_fields(cls, fields: dict[str, Any], **kwargs: Any) -> Self:
        """Create an instance from a dict of prompt fields + optional extra state.

        Return type follows ``cls`` — ``PromptTemplate.from_prompt_fields()``
        returns ``PromptTemplate``; ``OptSearchPoint.from_prompt_fields()``
        returns ``OptSearchPoint``.
        """
        fields = dict(fields)  # don't mutate caller's dict
        fse = fields.pop("few_shot_examples", [])
        if fse and isinstance(fse[0], dict):
            fse = [FewShotExample(**ex) for ex in fse]
        return cls(few_shot_examples=fse, **fields, **kwargs)


class OptimizationMemory(BaseModel):
    """Cross-round optimizer memory — the working set L1/L2/L3 build on.

    Bundled together because every field shares the same lifecycle:
    preserved across L2/L3 transitions (``OptSearchPoint`` adopts the
    parent's memory after derive_candidate dropped it), checkpointed as
    one nested object, and reset only by ``clear_volatile()`` on round
    improvement.
    """

    l1_critique_text: str = Field(
        default="",
        description="Latest L1 critique summary fed to L1 when no l2_directive "
        "is active. One-round window — cleared by clear_volatile() on "
        "improvement, same lifecycle as l2_directive.",
    )
    thinking_styles: list[str] = Field(
        default_factory=list,
        description="3 thinking styles sampled at each round's critique phase "
        "for injection into the L1 meta-prompt. One-round window — cleared by "
        "clear_volatile() on improvement.",
    )
    escalation_journal: list[dict[str, Any]] = Field(
        default_factory=list,
        description="Cross-round degradation investigation memory.",
    )
    warning_inventory: dict[str, dict[str, Any]] = Field(
        default_factory=dict,
        description="Per-query warning inventory across rounds.",
    )
    l2_directive: str = Field(
        default="",
        description="L2's diagnostic reasoning + action guidance for L1. "
        "One-round window — cleared by clear_volatile() on improvement.",
    )
    degradation_reset_count: int = Field(
        0,
        description="How many times L2/L3 patience exhausted during degradation.",
    )
    backend_warning_emitted: bool = Field(
        False,
        description="One-shot flag — True after backend warning has been emitted.",
    )
    validation_failures: list[ValidationFailure] = Field(
        default_factory=list,
        description="Parse-time invariant violations on this candidate "
        "(e.g. proposed `model: gpt-4o` when the user-declared allowed "
        "set is `[openai/gpt-oss-120b]`). A non-empty list makes the "
        "SearchPoint structurally invalid; score_search_point() short-"
        "circuits to a synthetic 0 instead of running the backend. See "
        "docs/developer/self-healing-internals.md.",
    )
    runtime_failures: list[RuntimeFailure] = Field(
        default_factory=list,
        description="Runtime-observed health failures on this candidate "
        "(e.g. max_tokens=150 producing 100%% empty_content_reasoning_fallback "
        "on reasoning models). Sibling of validation_failures on the self-"
        "healing rail but populated AFTER the backend ran, not at parse time. "
        "Does not synthetic-0 — the real score stands — but flows to L2 as "
        "self-healing evidence so the next round's directive names the "
        "disallowed value range. Attached per candidate, so a losing "
        "candidate's runtime issues never disrupt the round winner.",
    )
    failure_analysis: FailureAnalysis | None = Field(
        default=None,
        description="Latest round's clustered failure analysis over the "
        "winner's results. Consumed by L1 as an inbox section; replaced "
        "each round. Lives on memory so L2/L3 derive_candidate + "
        "adopt_transition carry it forward via model_copy(deep=True).",
    )
    round_history: list[RoundSummary] = Field(
        default_factory=list,
        description="Compact per-round trajectory ledger. Sufficient for "
        "``build_trajectory_report`` (accuracy). Full ``RoundResult`` "
        "objects with raw query results stay transient on ``Cycle.rounds``; "
        "this persisted mirror survives trial checkpoints.",
    )

    def clear_volatile(self) -> None:
        """Drop one-round windows after an improving round.

        All three fields share the same lifecycle: they are produced to
        steer *the next round only*, and once L1 succeeded the basis for
        that guidance is stale.

        * ``l2_directive``   — L2's action guidance for L1.
        * ``l1_critique_text`` — the prior round's L1 critique summary; mutex
          with ``l2_directive`` on L1, so a stale critique would otherwise
          bleed into the next L1 meta-prompt whenever L2 did not fire.
        * ``thinking_styles`` — 3 samples drawn at each round's L1 critique
          phase; tied to the same steering window.
        """
        self.l2_directive = ""
        self.l1_critique_text = ""
        self.thinking_styles = []

    def append_escalation(self, entry: dict[str, Any]) -> None:
        """Append a journal entry; fill the previous entry's pending outcome.

        Pure memory operation — the caller shapes the dict (see
        ``application/optimization/nodes/escalation.py::build_escalation_entry``).
        """
        journal = self.escalation_journal
        if journal and journal[-1]["outcome_degraded_rate"] is None:
            journal[-1]["outcome_degraded_rate"] = entry.get("degraded_rate", 0)
        journal.append(entry)


class OptSearchPoint(PromptTemplate):
    """Optimizer-level search point — the optimizer's full working state.

    Inherits the 8 prompt fields and rendering from ``PromptTemplate``.
    Adds lineage tracking, L2/L3 optimizer state, and optimization memory.

    Persisted in trial checkpoints. Enables L4 to correlate optimizer
    configuration with target-pipeline evaluation outcomes.
    """

    # -- Lineage -------------------------------------------------------------
    id: str = Field(default_factory=lambda: uuid.uuid4().hex)
    parent_id: str | None = None
    changes_description: str = ""
    created_at: str = Field(default_factory=lambda: datetime.now(UTC).isoformat())

    # -- L2 state ----------------------------------------------------------
    optimizer_params: dict[str, Any] = Field(default_factory=dict)
    task_context: TaskDecomposition = Field(
        default_factory=TaskDecomposition,
        description="Structured domain context (domain, pipeline_purpose, "
        "data_characteristics, optimization_goals, key_challenges). "
        "Set from TASK_DESCRIPTION decomposition, refinable by L2.",
    )

    @field_validator("task_context", mode="before")
    @classmethod
    def _coerce_task_context(cls, v: Any) -> TaskDecomposition:
        if isinstance(v, TaskDecomposition):
            return v
        if isinstance(v, dict):
            return TaskDecomposition.from_dict(v)
        return TaskDecomposition()

    # -- Optimization memory -----------------------------------------------
    memory: OptimizationMemory = Field(default_factory=lambda: OptimizationMemory())

    # -- Render seam: splice task context around problem_description ------

    def _field_value(self, name: str) -> str:
        """Wrap ``problem_description`` with ``task_context`` up/downstream context."""
        v = getattr(self, name)
        if name != "problem_description" or not v:
            return v
        tc = self.task_context
        if not (tc.upstream_context or tc.downstream_context):
            return v
        return "\n\n".join(p for p in (tc.upstream_context, v, tc.downstream_context) if p)

    # -- Projection to target layer ----------------------------------------

    def to_job_search_point(
        self,
        base_pipeline_params: dict | None = None,
        *,
        schema: PipelineSchema | None = None,
    ) -> JobSearchPoint:
        """Project into a JobSearchPoint for target-layer evaluation.

        Renders prompt fields → injects into pipeline_params → creates
        a frozen JobSearchPoint with ``prompt_fields`` populated for
        variant derivation.

        When *schema* is provided, derives ``active_steps`` and
        ``prompt_node`` from it — pipeline composition is immutable per
        campaign and must not depend on what ``base_pipeline_params``
        carries.
        """
        from promptpotter.domain.search_point import JobSearchPoint

        pp = copy.deepcopy(base_pipeline_params or {})
        active_steps = schema.active_steps if schema else ()
        prompt_nodes = schema.prompt_node_names() if schema else []
        prompt_node = prompt_nodes[0] if prompt_nodes else ""
        if active_steps:
            pp["steps"] = list(active_steps)
        rendered = self.render()
        if rendered and prompt_node:
            pp.setdefault(prompt_node, {})["prompt"] = rendered

        # Build prompt_fields for variant derivation in JobSearchPoint.
        # Only meaningful when a prompt node exists in pipeline_params —
        # otherwise derive() can't inject rendered prompts.
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

    # -- Candidate derivation ------------------------------------------------

    def derive_candidate(self, **changes: Any) -> OptSearchPoint:
        """Create a child OptSearchPoint with prompt field modifications.

        Sets parent_id to this instance's id. Generates a new id/timestamp.
        Copies prompt decomposition + L2/L3 state. ``memory`` is *not*
        copied — children start fresh and only inherit accumulated memory
        when L2/L3 transitions adopt them via ``Cycle.apply_transition``.
        """
        data: dict[str, Any] = {}
        # Copy prompt decomposition fields
        for f in PROMPT_STRING_FIELDS:
            data[f] = changes.pop(f, getattr(self, f))
        # few_shot_examples
        fse = changes.pop("few_shot_examples", None)
        if fse is not None:
            if fse and isinstance(fse[0], dict):
                fse = [FewShotExample(**ex) for ex in fse]
            data["few_shot_examples"] = fse
        else:
            data["few_shot_examples"] = [ex.model_copy() for ex in self.few_shot_examples]
        # L2/L3 state
        data["optimizer_params"] = changes.pop("optimizer_params", dict(self.optimizer_params))
        data["task_context"] = changes.pop("task_context", self.task_context.to_dict())
        data["plan"] = changes.pop("plan", self.plan)
        # Lineage
        data["parent_id"] = self.id
        data["changes_description"] = changes.pop("changes_description", "")
        # Any remaining changes
        data.update(changes)
        return OptSearchPoint(**data)
