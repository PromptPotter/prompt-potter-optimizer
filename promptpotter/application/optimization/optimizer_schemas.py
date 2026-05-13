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

``pipeline_params_override`` on :class:`L1Variant` is intentionally
permissive (``dict[str, Any]``) because its inner shape is derived from
the active backend ``PipelineSchema`` at runtime (see
``l1_validators.build_l1_output_schema``). Per-node param validation
runs downstream through ``validate_overrides`` — that's not a duplicate
parse, it's a different contract (operator-locked axes, allowed-value
enums per backend).
"""

from __future__ import annotations

from typing import Annotated, Any, Literal

from pydantic import BaseModel, BeforeValidator, ConfigDict, Field


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

EvidenceGroundingField = Literal[
    "parent_panel",
    "sibling_yield",
    "axis_memory",
    "escalation_panel",
    "task_context",
    "plan",
    "stall_exploration",
]


class VariantEvidenceGrounding(BaseModel):
    """One row of evidence justifying a variant's chosen mutation.

    ``field`` names the panel entry the LLM is grounding on;
    ``stall_exploration`` is the escape hatch and is only valid when
    ``escalation_panel.exploration_budget`` is ``normal`` or ``wide``
    (enforced downstream in :mod:`l1_behavior_checks`, not here — that's
    a cycle-state check, not a parse check).
    """

    model_config = ConfigDict(extra="forbid")

    field: EvidenceGroundingField
    citation: str


class L1Variant(BaseModel):
    """One candidate variant proposed by ``l1_generate``.

    ``pipeline_params_override`` is dynamically shaped per backend
    pipeline (see :func:`l1_validators.build_l1_output_schema`); we keep
    it as ``dict[str, Any]`` so a Pydantic parse never fails on a
    legitimately backend-specific override key. The per-backend shape is
    enforced downstream by ``validate_overrides`` against the active
    ``PipelineSchema``.

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
    pipeline_params_override: dict[str, Any] = Field(
        default_factory=dict,
        description=(
            "Flat or per-node mutations the variant introduces. Empty means "
            "clone-of-parent and is rejected downstream by detect_invariants."
        ),
    )
    target_axis: str = ""
    reasoning: str = ""
    evidence_grounding: VariantEvidenceGrounding


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
    """

    model_config = ConfigDict(extra="forbid")

    task_context: dict[str, Any] = Field(default_factory=dict)
    action: OptimizerAction = "normal_round"
    axis_targeted: str = ""
    l1_layout: dict[str, list[str]] = Field(default_factory=dict)
    l1_overrides: dict[str, Any] = Field(default_factory=dict)
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
