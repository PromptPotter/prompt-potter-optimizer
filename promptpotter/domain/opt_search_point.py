"""OptSearchPoint — the optimizer's full working state.

Inherits ``PromptTemplate`` (8 prompt fields) and adds lineage, L2/L3 state,
and optimization memory. ``to_job_search_point()`` projects into a frozen
``JobSearchPoint`` for scoring by rendering prompt fields into
``pipeline_params``.

Two-layer tracing:
  Target layer:    JobSearchPoint  → content_hash → library/measurements/
  Optimizer layer: OptSearchPoint  → model_dump() → campaigns/{id}/trial.json

ADR-8: Mutable (not frozen). Updated in-place during the feedback cycle,
serialized via ``model_dump()`` at checkpoint time.
"""

from __future__ import annotations

import copy
import re
import uuid
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any, ClassVar, Self

from pydantic import BaseModel, ConfigDict, Field, field_validator

from promptpotter.config.settings import PROMPT_STRING_FIELDS
from promptpotter.domain.analysis import FailureAnalysis, RuntimeFailure, ValidationFailure
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


class RoundSummary(BaseModel):
    """Compact per-round record persisted on ``OptSearchPoint.round_history``.

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

        Return type follows ``cls``. Pydantic auto-coerces a nested
        ``lineage: {...}`` dict into ``IndividualLineage`` when present.
        """
        fields = dict(fields)
        fse = fields.pop("few_shot_examples", [])
        if fse and isinstance(fse[0], dict):
            fse = [FewShotExample(**ex) for ex in fse]
        return cls(few_shot_examples=fse, **fields, **kwargs)


class IndividualLineage(BaseModel):
    """Identity and provenance of a single individual in the population.

    Groups the four fields that describe *what this individual is and where it
    came from*.  Set once at individual creation (``mutate`` and
    ``OptSearchPoint.__init__``); never mutated after that.
    """

    id: str = Field(default_factory=lambda: uuid.uuid4().hex)
    parent_id: str | None = None
    changes_description: str = ""
    created_at: str = Field(default_factory=lambda: datetime.now(UTC).isoformat())
    source: str = Field(
        default="",
        description="Origin of this individual: 'baseline' / 'l1_generate' / "
        "'l2_context' / 'l3_plan'.",
    )


class OptSearchPoint(PromptTemplate):
    """Optimizer-level search point — the optimizer's full working state.

    Inherits the 8 prompt fields and rendering from ``PromptTemplate``.
    Adds three top-level groups beyond the prompt fields, plus the
    flat optimizer-memory fields enumerated in :data:`MEMORY_FIELDS`:

    - ``lineage``        — identity + provenance (id, parent_id, ...)
    - ``optimizer_params`` — L2/L3 tuning knobs
    - ``task_context``  — structured domain understanding (TaskDecomposition)
    - memory fields     — see :data:`MEMORY_FIELDS` (preserved across L2/L3
      transitions via :meth:`copy_memory_to`).

    Persisted in trial checkpoints. Enables L4 to correlate optimizer
    configuration with target-pipeline scoring outcomes.
    """

    model_config = ConfigDict(extra="forbid")

    # -- Lineage -------------------------------------------------------------
    lineage: IndividualLineage = Field(default_factory=IndividualLineage)

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

    # -- Optimization memory (flattened) -----------------------------------
    # The fields below are bundled by MEMORY_FIELDS so they can be copied
    # atomically across L2/L3 transitions (see :meth:`copy_memory_to`).
    l1_critique_text: str = Field(
        default="",
        description="Latest L1 critique summary fed to L1 when no l2_directive "
        "is active. One-round window — cleared by clear_volatile() on "
        "improvement, same lifecycle as l2_directive.",
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
        "(e.g. max_tokens=150 classifying as 100%% reasoning_budget_exhausted "
        "on reasoning models). Populated AFTER the backend ran, not at parse "
        "time. Does not synthetic-0 — the real score stands — but flows to "
        "L2 as self-healing evidence so the next round's directive names the "
        "disallowed value range. Attached per candidate, so a losing "
        "candidate's runtime issues never disrupt the round winner.",
    )
    l2_output_failures: list[ValidatorOutcome] = Field(
        default_factory=list,
        description="Deterministic validator outcomes on L2's own output for "
        "this round. Populated post-parse by the L2-validator registry "
        "(cross-field duplication, verbatim self-repeat, catalogue "
        "redundancy). A non-empty list forces L3 escalation this round — "
        "L3 is the nurse-LLM that heals L2, analogue of how L2 heals L1 "
        "via validation_failures. See "
        "docs/developer/self-healing-internals.md.",
    )
    failure_analysis: FailureAnalysis | None = Field(
        default=None,
        description="Latest round's clustered failure analysis over the "
        "winner's results. Consumed by L1 as a dispatch_msg section; replaced "
        "each round. Carried across L2/L3 mutate + adopt_transition "
        "by copy_memory_to().",
    )
    round_history: list[RoundSummary] = Field(
        default_factory=list,
        description="Compact per-round trajectory ledger. Sufficient for "
        "``build_trajectory_report`` (accuracy). Full ``RoundResult`` "
        "objects with raw query results stay transient on ``Cycle.rounds``; "
        "this persisted mirror survives trial checkpoints.",
    )

    # -- L1-generate surface state (owned by L2 mutations) ----------------
    l1_section_overrides: dict[str, bool] = Field(
        default_factory=dict,
        description="Per-section visibility toggles for L1-generate. Keys are "
        'section names from ``LAYER_CONFIGS[Layer.L1_GENERATE].sections``; ``False`` gates a section off (renders ``""``); '
        "``True`` or absent renders normally. Set by L2 when it fires; "
        "persists round-over-round until L2 flips it.",
    )
    l1_section_overrides_text: dict[str, str] = Field(
        default_factory=dict,
        description="Per-section text overrides for L1-generate. Keys are "
        "section names from ``LAYER_CONFIGS[Layer.L1_GENERATE].sections``; the value replaces that section's rendered "
        "output. Set by L2 when it fires. Wins over default rendering, "
        "loses to a section being toggled off.",
    )
    l1_template_override: str = Field(
        default="",
        description="Whole-body override for L1-generate's ``problem_description`` "
        "template. When non-empty, this string replaces the default body and is "
        "compiled with the same named-hole substitutions. Set by L2 on a "
        "fundamental reframing fire. Should contain ``{{l2_directive}}`` so "
        "directives still flow through.",
    )

    MEMORY_FIELDS: ClassVar[tuple[str, ...]] = (
        "l1_critique_text",
        "escalation_journal",
        "warning_inventory",
        "l2_directive",
        "validation_failures",
        "runtime_failures",
        "l2_output_failures",
        "failure_analysis",
        "round_history",
        # L1-generate surface state owned by L2 — must survive L2→L3
        # adoption so a fundamental reframing (L3) inherits the in-flight
        # section toggles / text overrides / template body that L2 set.
        # Without these, L3 spawns a fresh OSP that drops the parent's
        # surface state, then ``apply_side_effects``'s ``{**existing, **new}``
        # merge silently keeps stale entries from the *prior* OSP that
        # was about to be defeated. See the audit in
        # ``docs/concepts/l1-generate-surface.md``.
        "l1_section_overrides",
        "l1_section_overrides_text",
        "l1_template_override",
    )
    """Names of the flat optimizer-memory fields preserved across L2/L3
    transitions. The contract that ``OptimizationMemory`` used to bundle —
    now a ClassVar so :meth:`copy_memory_to` can iterate."""

    # -- Memory helpers ----------------------------------------------------

    def clear_volatile(self) -> None:
        """Drop one-round windows after an improving round.

        Both fields share the same lifecycle: they are produced to steer
        *the next round only*, and once L1 succeeded the basis for that
        guidance is stale.

        * ``l2_directive``   — L2's action guidance for L1.
        * ``l1_critique_text`` — the prior round's L1 critique summary; mutex
          with ``l2_directive`` on L1, so a stale critique would otherwise
          bleed into the next L1 meta-prompt whenever L2 did not fire.
        """
        self.l2_directive = ""
        self.l1_critique_text = ""

    def append_escalation(self, entry: dict[str, Any]) -> None:
        """Append a journal entry; fill the previous entry's pending outcome.

        Pure memory operation — the caller shapes the dict (see
        ``application/optimization/layer_escalation.py::build_escalation_entry``).
        """
        journal = self.escalation_journal
        if journal and journal[-1]["outcome_degraded_rate"] is None:
            journal[-1]["outcome_degraded_rate"] = entry.get("degraded_rate", 0)
        journal.append(entry)

    def copy_memory_to(self, target: OptSearchPoint) -> None:
        """Deep-copy the :data:`MEMORY_FIELDS` from ``self`` onto *target* in place.

        Preserves the parent's accumulated optimizer memory (escalation
        journal, failure analyses, etc.) when L2/L3 adopts a transition.
        """
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
        """Project into a JobSearchPoint for target-layer scoring.

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

    def mutate(self, **changes: Any) -> OptSearchPoint:
        """Create a child OptSearchPoint with prompt field modifications.

        Sets lineage.parent_id to this instance's lineage.id. Generates a
        new id/timestamp via a fresh IndividualLineage. Copies prompt
        decomposition + L2/L3 state. ``memory`` is *not* copied — children
        start fresh and only inherit accumulated memory when L2/L3
        transitions adopt them via ``Cycle.apply_transition``.
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
        data["lineage"] = IndividualLineage(
            parent_id=self.lineage.id,
            changes_description=changes.pop("changes_description", ""),
            source=changes.pop("source", ""),
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
