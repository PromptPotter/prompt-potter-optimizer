"""OptSearchPoint — the optimizer's full working state.

Inherits ``PromptTemplate`` (8 prompt fields) and adds lineage, L2/L3 state,
and optimization memory. ``to_job_search_point()`` projects into a frozen
``JobSearchPoint`` for scoring by rendering prompt fields into
``pipeline_params``.

Two-layer tracing:
  Target layer:    JobSearchPoint  → content_hash → archive/measurements/
  Optimizer layer: OptSearchPoint  → model_dump() → campaigns/{id}/round_data.json

ADR-8: Mutable (not frozen). Updated in-place during the feedback cycle,
serialized via ``model_dump()`` at checkpoint time.
"""

from __future__ import annotations

import copy
import re
import uuid
from typing import TYPE_CHECKING, Any, ClassVar, Self

from pydantic import BaseModel, ConfigDict, Field, field_validator

from promptpotter.config.settings import PROMPT_STRING_FIELDS
from promptpotter.domain.escalation_signals import RuntimeFailure, ValidationFailure
from promptpotter.domain.l1_layout import L1Layout, default_l1_layout
from promptpotter.domain.search_point import SearchPoint, TaskDecomposition
from promptpotter.domain.validators import ValidatorOutcome

if TYPE_CHECKING:
    from promptpotter.domain.pipeline_schema import PipelineSchema
    from promptpotter.domain.search_point import JobSearchPoint


class FewShotExample(BaseModel):
    """An input/output pair used as a few-shot demonstration."""

    input: str
    output: str
    explanation: str | None = None


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

    def prompt_fields(self) -> dict[str, str]:
        """Non-empty ``PROMPT_STRING_FIELDS`` as ``{name: value}`` (no few-shot).

        The string-only projection used by L1 candidate summaries + the
        validator's parent/child diff. Distinct from ``prompt_field_dict()``
        which adds ``few_shot_examples`` for the L1-generation roundtrip.
        """
        return {f: v for f in PROMPT_STRING_FIELDS if (v := getattr(self, f))}

    def prompt_field_dict(self) -> dict[str, Any]:
        """Return prompt decomposition fields as a dict (for L1 candidate generation)."""
        d: dict[str, Any] = dict(self.prompt_fields())
        if self.few_shot_examples:
            d["few_shot_examples"] = [ex.model_dump() for ex in self.few_shot_examples]
        return d

    @classmethod
    def from_prompt_fields(cls, fields: dict[str, Any], **kwargs: Any) -> Self:
        """Create an instance from a dict of prompt fields + optional extra state.

        Return type follows ``cls``. Pydantic auto-coerces a nested
        ``lineage: {...}`` dict into ``IndividualLineage`` when present.
        """
        fields = dict(fields)
        fse = fields.pop("few_shot_examples", [])
        if fse and isinstance(fse[0], dict):
            fse = [FewShotExample(**ex) for ex in fse]
        return cls(few_shot_examples=fse, **fields, **kwargs)


EVIDENCE_GROUNDING_FIELDS: frozenset[str] = frozenset(
    {
        "parent_panel",
        "sibling_yield",
        "axis_memory",
        "escalation_panel",
        "task_context",
        "plan",
        "critique",
        "stall_exploration",
    }
)


class EvidenceGrounding(BaseModel):
    """Panel field cited by L1 to justify a candidate's mutation.

    Per ``promptpotter/CLAUDE.md`` L1 contract: *no data justifying a choice
    ⇒ do not gamble*. Each L1-emitted variant declares which evidence panel
    the mutation was anchored on, plus a short citation pointing at the
    specific entry. ``stall_exploration`` is the escape hatch — only valid
    when ``escalation_panel.exploration_budget != "tight"``.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    field: str = Field(description="One of EVIDENCE_GROUNDING_FIELDS.")
    citation: str = Field(description="Short string naming the panel entry cited.")


class IndividualLineage(BaseModel):
    """Identity and provenance of a single individual in the population.

    Groups the fields that describe *what this individual is and where it
    came from*.  Set once at individual creation (``mutate`` and
    ``OptSearchPoint.__init__``); never mutated after that.
    """

    id: str = Field(default_factory=lambda: uuid.uuid4().hex)
    parent_id: str | None = None
    changes_description: str = ""
    source: str = Field(
        default="",
        description="Origin of this individual: 'origin' / 'l1_generate' / "
        "'l2_context' / 'l3_plan'.",
    )
    evidence_grounding: EvidenceGrounding | None = Field(
        default=None,
        description="L1-declared panel evidence for this individual's mutation. "
        "None for origin / L2 / L3 individuals; populated for L1 children.",
    )


class OptSearchPoint(PromptTemplate):
    """Optimizer-level search point — the optimizer's full working state.

    Inherits the 8 prompt fields and rendering from ``PromptTemplate``.
    Adds three top-level groups beyond the prompt fields, plus the
    flat optimizer-memory fields enumerated in :data:`MEMORY_FIELDS`:

    - ``lineage``        — identity + provenance (id, parent_id, ...)
    - ``l1_overrides`` — L1 runtime knobs (``n_variants``, ``creativity``)
      set by L2; ``n_variants`` flows into L1's prompt as the
      ``{{n_variants}}`` caller extra, ``creativity`` sets L1's LLM-call
      temperature outside the prompt
    - ``task_context``  — structured domain understanding (TaskDecomposition)
    - memory fields     — see :data:`MEMORY_FIELDS` (preserved across L2/L3
      transitions via :meth:`copy_memory_to`).

    Persisted in round_data checkpoints. Enables L4 to correlate optimizer
    configuration with target-pipeline scoring outcomes. Per-round
    trajectory and pipeline-step escalation events live on the ledger
    (``CycleEventLog``) — projections derive on demand.
    """

    model_config = ConfigDict(extra="forbid")

    # -- Lineage -------------------------------------------------------------
    lineage: IndividualLineage = Field(default_factory=IndividualLineage)

    # -- L2 state ----------------------------------------------------------
    l1_overrides: dict[str, Any] = Field(default_factory=dict)
    task_context: TaskDecomposition = Field(default_factory=TaskDecomposition)

    @field_validator("task_context", mode="before")
    @classmethod
    def _coerce_task_context(cls, v: Any) -> TaskDecomposition:
        if isinstance(v, TaskDecomposition):
            return v
        if isinstance(v, dict):
            return TaskDecomposition.from_dict(v)
        return TaskDecomposition()

    # -- Optimization memory (flat; bundled by MEMORY_FIELDS for copy_memory_to) --
    # Sticky L3→L2 channel: L3 writes, L2 reads via the ``l3_to_l2_note``
    # signal; never surfaced to L1 (absent from ``L1_POSSIBLE``). Persists
    # across L2 fires via :data:`MEMORY_FIELDS`; replaced wholesale on each
    # L3 fire (``_apply_l3``).
    l3_note: str = ""
    validation_failures: list[ValidationFailure] = Field(default_factory=list)
    runtime_failures: list[RuntimeFailure] = Field(default_factory=list)
    l2_guard_breaches: list[ValidatorOutcome] = Field(default_factory=list)
    l3_guard_breaches: list[ValidatorOutcome] = Field(default_factory=list)

    # -- L1-generate surface state (owned by L2 mutations) ----------------
    # Per-slot list of placeholder names L2 picks from `L1_POSSIBLE`. The
    # dispatch hub fills L1's PromptTemplate by walking this layout at
    # render time. Default covers the mandatory placeholders without
    # forcing L2 to write a layout on every fire.
    l1_layout: L1Layout = Field(default_factory=default_l1_layout)

    # Fields preserved across L2/L3 transitions via copy_memory_to.
    # L1's layout + L1 runtime overrides MUST be in here so L3-spawned
    # children inherit in-flight L2 surface edits instead of being
    # silently merged from a stale OSP.
    MEMORY_FIELDS: ClassVar[tuple[str, ...]] = (
        "l3_note",
        "validation_failures",
        "runtime_failures",
        "l2_guard_breaches",
        "l3_guard_breaches",
        "l1_layout",
        "l1_overrides",
    )

    def copy_memory_to(self, target: OptSearchPoint) -> None:
        """Deep-copy MEMORY_FIELDS onto *target* (in place) for L2/L3 adopt."""
        for f in self.MEMORY_FIELDS:
            setattr(target, f, copy.deepcopy(getattr(self, f)))

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
        """Render → inject into pipeline_params → return frozen JobSearchPoint.

        Pipeline composition (active_steps, prompt_node) is read from *schema*
        when provided — it's immutable per campaign and must not be inferred
        from whatever ``base_pipeline_params`` happens to carry.
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

    def mutate(self, **changes: Any) -> OptSearchPoint:
        """Child OSP with prompt + L2/L3 state copied; memory NOT copied.

        Memory only flows on L2/L3 adopt (Cycle.apply_transition →
        copy_memory_to). Children start with empty memory.
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
        data["l1_overrides"] = changes.pop("l1_overrides", dict(self.l1_overrides))
        data["task_context"] = changes.pop("task_context", self.task_context.to_dict())
        data["plan"] = changes.pop("plan", self.plan)
        # Lineage
        data["lineage"] = IndividualLineage(
            parent_id=self.lineage.id,
            changes_description=changes.pop("changes_description", ""),
            source=changes.pop("source", ""),
            evidence_grounding=changes.pop("evidence_grounding", None),
        )
        # Any remaining changes
        data.update(changes)
        return OptSearchPoint(**data)


# ---------------------------------------------------------------------------
# Search-point diff helpers — pure shape munging, consumed by the views layer.
# ---------------------------------------------------------------------------


def _fmt_pp_val(v: object) -> str:
    if isinstance(v, float):
        return f"{v:g}"
    return str(v)


def flatten_sp_summary(pp: dict | None) -> dict[str, str]:
    """Flatten ``{node: {param: value}}`` pipeline_params into a ``node.param`` display dict."""
    flat: dict[str, str] = {}
    for k, v in (pp or {}).items():
        if k == "steps" or not isinstance(v, dict):
            continue
        for sub_k, sub_v in v.items():
            flat[f"{k}.{sub_k}"] = _fmt_pp_val(sub_v)
    return flat


def build_candidate_flat(parent: dict[str, str], candidate_meta: dict) -> dict[str, str]:
    """Merge candidate overrides onto parent; pipeline_params and prompt_fields layer into disjoint keyspaces."""
    flat = parent.copy()
    if pp := candidate_meta.get("pipeline_params_override"):
        flat.update(flatten_sp_summary(pp))
    for field_name, value in (candidate_meta.get("prompt_fields") or {}).items():
        if value:
            flat[field_name] = str(value)
    return flat


def group_diff_keys(
    diff_keys: list[str],
    node_param_keys: dict[str, list[str]] | None,
) -> list[tuple[str, list[str]]]:
    """Group ``node.param`` diff keys by node in execution order; prompt fields land in the ``""`` group."""
    if not node_param_keys:
        return [("", diff_keys)]
    groups: dict[str, list[str]] = {sname: [] for sname in node_param_keys}
    groups[""] = []
    for k in diff_keys:
        prefix = k.split(".", 1)[0]
        groups[prefix if prefix in groups else ""].append(k)
    return [(sname, sorted(keys)) for sname, keys in groups.items() if keys]
