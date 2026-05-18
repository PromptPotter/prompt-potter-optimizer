"""Pydantic response models for every optimizer LLM node.

One Pydantic model per node in ``optimizer_pipeline.json::nodes``. These are
the source of truth for what each ``l1_generate`` / ``l1_critique`` /
``l2_context`` / ``l3_plan`` / ``restructure`` LLM call is allowed to
return. The provider validates server-side via the JSON Schema emitted by
``Model.model_json_schema()``; the SDK populates the typed instance on
``response.choices[0].message.parsed`` — no hand-rolled regex repair on
the hot path.

The JSON Schemas embedded in ``optimizer_pipeline.json::resolved_schemas``
are a regenerated export of these models (run
``scripts/build_optimizer_schemas.py`` after editing a model). Pydantic is
the SoT; the JSON file is for any non-Python consumer (Langfuse, webapp).

``model_config = ConfigDict(extra="forbid")`` is load-bearing — OpenAI's
structured-output requires ``additionalProperties: false`` for strict
mode, and ``extra="forbid"`` emits exactly that. Without it the SDK
silently falls back to lenient parsing.

:class:`L1Variant` is **three-slot by contract**:

- ``pipeline_params_override`` — per-node tunables (e.g. ``{"llm_only":
  {"temperature": 0.7}}``). Inner shape is grafted at runtime by
  :func:`l1_validators.build_l1_output_schema` from the active
  ``PipelineSchema``.
- ``prompt_fields_override`` — the six top-level prompt fields (``persona``,
  ``task_intent``, ``problem_description``, ``instruction``,
  ``thinking_style``, ``answer_format``). Each is a string. Constrained
  by ``PROMPT_STRING_FIELDS``.
- ``task_context_override`` — the two pipeline-context strings
  (``upstream_context``, ``downstream_context``). Constrained by
  ``TASK_CONTEXT_OVERRIDES``.

Splitting these into distinct slots is what makes the shape statically
validatable — before the split, prompt fields kept landing inside
``pipeline_params_override.llm_only`` and the runner had to un-nest them
on every round. With distinct slots the LLM cannot conflate the buckets.
"""

from __future__ import annotations

from typing import Annotated, Any, Literal

from pydantic import BaseModel, BeforeValidator, ConfigDict, Field

from promptpotter.domain.opt_search_point import (
    EVIDENCE_GROUNDING_FIELDS,
    L1SituationalExample,
    L1SupplementalRule,
)


def _truncate(max_len: int):
    """Silent string truncation for LLM outputs that overshoot a Field max_length.

    Pydantic's max_length raises ValidationError on overflow, which would
    discard the *entire* critique on a single overrun field. The schema
    cap stays (it tells the LLM the budget via JSON Schema export); this
    BeforeValidator drops the tail and keeps the head.
    """

    def _v(value: Any) -> Any:
        if isinstance(value, str) and len(value) > max_len:
            return value[:max_len]
        return value

    return _v


__all__ = [
    "OPTIMIZER_RESPONSE_MODELS",
    "L1CritiqueOutput",
    "L1GenerateOutput",
    "L1SituationalExample",
    "L1SupplementalRule",
    "L1Variant",
    "L2ContextOutput",
    "L3PlanOutput",
    "RestructureOutput",
    "RestructureTaskContext",
    "VariantEvidenceGrounding",
]


# ---------------------------------------------------------------------------
# l1_generate — proposes candidate variants for the next round.
# ---------------------------------------------------------------------------


class VariantEvidenceGrounding(BaseModel):
    """One row of evidence justifying a variant's chosen mutation.

    ``field`` names the panel entry the LLM is grounding on. The JSON
    Schema exported to the provider lists the allowed values as an
    ``enum`` hint so structured-output-aware models stay in-set, but the
    parse boundary is permissive — providers like Groq don't honor the
    enum and a single off-script value would otherwise crash the round.
    The canonical enforcement is the ``evidence_grounding_present``
    behavior check (see :mod:`l1_behavior`); ``stall_exploration`` is the
    escape hatch and is only valid when
    ``escalation_panel.exploration_budget`` is ``normal`` or ``wide``.
    """

    model_config = ConfigDict(extra="forbid")

    field: str = Field(
        description="One of EVIDENCE_GROUNDING_FIELDS — panel entry cited.",
        json_schema_extra={"enum": sorted(EVIDENCE_GROUNDING_FIELDS)},
    )
    citation: str


class L1Variant(BaseModel):
    """One candidate variant proposed by ``l1_generate``.

    Three override slots, each carrying a different layer's mutations:

    - ``pipeline_params_override`` — per-node tunables, shape ``{node:
      {param: value}}``. Inner shape is grafted at runtime by
      :func:`l1_validators.build_l1_output_schema` from the active
      ``PipelineSchema``; the keys at this level remain ``dict[str, dict]``
      so the Pydantic parse never fails on a legitimately backend-specific
      node name.
    - ``prompt_fields_override`` — top-level prompt fields, shape
      ``{field_name: string}``. Keys constrained to ``PROMPT_STRING_FIELDS``
      by the runtime-built JSON Schema.
    - ``task_context_override`` — pipeline-context strings, shape
      ``{field_name: string}``. Keys constrained to ``TASK_CONTEXT_OVERRIDES``
      by the runtime-built JSON Schema.

    At least one of the three must be non-empty — an all-empty variant is
    a no-op and is rejected by :func:`detect_invariants` downstream with
    ``reason="no_op_variant"``.

    ``target_axis`` and ``reasoning`` are LLM-side reasoning aids — the
    ``l1_generate`` prompt's ``answer_format`` instructs the model to
    emit them so its candidate selection is grounded. PromptPotter
    persists them in the audit trail but doesn't read them at runtime;
    they're recorded so a human reviewing ``round_NNNN.json`` can see
    the model's stated rationale for each candidate.
    """

    model_config = ConfigDict(extra="forbid")

    variant_name: str
    changes_description: str
    pipeline_params_override: dict[str, dict[str, Any]] = Field(
        default_factory=dict,
        description=(
            "Per-node tunables, shape {node_name: {param: value}}. Inner per-node "
            "properties are grafted from the active PipelineSchema at runtime."
        ),
    )
    prompt_fields_override: dict[str, str] = Field(
        default_factory=dict,
        description=(
            "Top-level prompt-template fields; keys must be one of "
            "{persona, task_intent, problem_description, instruction, "
            "thinking_style, answer_format}."
        ),
    )
    task_context_override: dict[str, str] = Field(
        default_factory=dict,
        description=(
            "Pipeline-context strings; keys must be one of {upstream_context, downstream_context}."
        ),
    )
    target_axis: str = ""
    reasoning: str = ""
    # Optional at parse time so providers that omit it on a single variant
    # don't crash the entire round — the ``evidence_grounding_present`` behavior
    # check is the canonical enforcement point and flags missing/malformed
    # entries as wounds without burning the LLM call.
    evidence_grounding: VariantEvidenceGrounding | None = None


class L1GenerateOutput(BaseModel):
    """Top-level shape returned by the ``l1_generate`` meta-prompt."""

    model_config = ConfigDict(extra="forbid")

    variants: list[L1Variant]


# ---------------------------------------------------------------------------
# l1_critique — round-end analysis, feeds the next round's L1.
# ---------------------------------------------------------------------------


class L1CritiqueOutput(BaseModel):
    """Critique returned at round-end; the only required field is
    ``summary`` — the others are best-effort enrichment the next round's
    L1 may or may not get."""

    model_config = ConfigDict(extra="forbid")

    summary: Annotated[str, BeforeValidator(_truncate(400))] = Field(max_length=400)
    positive_critique: Annotated[str, BeforeValidator(_truncate(300))] = Field(
        default="", max_length=300
    )
    negative_critique: Annotated[str, BeforeValidator(_truncate(400))] = Field(
        default="", max_length=400
    )
    priority_fix: Annotated[str, BeforeValidator(_truncate(200))] = Field(
        default="", max_length=200
    )
    suggested_axes: list[str] = Field(
        default_factory=list,
        max_length=4,
        description="≤4 short axis names. Each item ≤40 chars (downstream truncates).",
    )
    failure_highlights: list[str] = Field(
        default_factory=list,
        max_length=3,
        description="≤3 short failure-line excerpts. Each item ≤140 chars (downstream truncates).",
    )


# ---------------------------------------------------------------------------
# l2_context — refine task framing + optional layout/runtime knobs.
# ---------------------------------------------------------------------------

OptimizerAction = Literal["normal_round", "probe_round"]


class L2ContextOutput(BaseModel):
    """L2 refinement. All fields optional — the LLM sets only what it
    wants to change.

    ``task_context`` is a free-form refinement dict (keys: domain,
    pipeline_purpose, data_characteristics, optimization_goals,
    key_challenges) — left as ``dict[str, Any]`` because the cycle's
    :class:`TaskDecomposition` owns the precise key set and merges via
    :meth:`TaskDecomposition.merge`. ``l1_layout`` is a similarly
    flexible ``{slot: [placeholder, ...]}`` map validated downstream by
    :func:`validate_l1_layout`.

    ``l1_supplemental_rules`` + ``l1_situational_examples`` are
    full-replace layers that L2 re-authors every fire. Auto-triggered
    rules (PEAKED, runtime_failure, chain_bind, continuous_envelope,
    latex_repair, l2_stall_diversity) render independently via the
    dispatch hub's auto-rules registry — L2 only adds rules that
    auto-triggers don't already cover.
    """

    model_config = ConfigDict(extra="forbid")

    task_context: dict[str, Any] = Field(default_factory=dict)
    action: OptimizerAction = "normal_round"
    axis_targeted: str = ""
    l1_layout: dict[str, list[str]] = Field(default_factory=dict)
    l1_overrides: dict[str, Any] = Field(default_factory=dict)
    l1_supplemental_rules: list[L1SupplementalRule] = Field(default_factory=list, max_length=4)
    l1_situational_examples: list[L1SituationalExample] = Field(default_factory=list, max_length=4)
    rationale: str = ""


# ---------------------------------------------------------------------------
# l3_plan — strategic replan with optional sticky pointer to L2.
# ---------------------------------------------------------------------------


class L3PlanOutput(BaseModel):
    """L3 strategic replan."""

    model_config = ConfigDict(extra="forbid")

    plan: str
    note: str = ""
    rationale: str = ""


# ---------------------------------------------------------------------------
# restructure — one-time decomposition of user context into Layer-1 fields.
# ---------------------------------------------------------------------------


class RestructureTaskContext(BaseModel):
    """Domain-context sub-object inside the restructure output."""

    model_config = ConfigDict(extra="forbid")

    domain: str = ""
    pipeline_purpose: str = ""
    data_characteristics: str = ""
    optimization_goals: str = ""
    key_challenges: str = ""
    upstream_context: str = ""
    downstream_context: str = ""


class RestructureOutput(BaseModel):
    """Output of the one-shot restructure prompt — Layer-1 prompt fields
    plus a domain-context sub-object."""

    model_config = ConfigDict(extra="forbid")

    persona: str = ""
    task_intent: str = ""
    problem_description: str = ""
    instruction: str = ""
    thinking_style: str = ""
    answer_format: str = ""
    task_context: RestructureTaskContext = Field(default_factory=RestructureTaskContext)
    consultation: str = ""


# ---------------------------------------------------------------------------
# Node → model registry.
# ---------------------------------------------------------------------------


OPTIMIZER_RESPONSE_MODELS: dict[str, type[BaseModel]] = {
    "l1_generate": L1GenerateOutput,
    "l1_critique": L1CritiqueOutput,
    "l2_context": L2ContextOutput,
    "l3_plan": L3PlanOutput,
    "restructure": RestructureOutput,
}
