"""Pydantic response models for every optimizer LLM node.

One Pydantic model per node in ``datasets/_optimizer/pipeline.json::nodes``. These are
the source of truth for what each ``l1_generate`` / ``l1_critique`` /
``l2_context`` / ``l3_plan`` / ``checkin`` LLM call is allowed to
return. The provider validates server-side via the JSON Schema emitted by
``Model.model_json_schema()``; the SDK populates the typed instance on
``response.choices[0].message.parsed`` — no hand-rolled regex repair on
the hot path.

The JSON Schemas embedded in ``datasets/_optimizer/pipeline.json::resolved_schemas``
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

from collections.abc import Callable
from typing import Annotated, Any, Literal, cast

from pydantic import BaseModel, BeforeValidator, ConfigDict, Field

from promptpotter.domain.opt_search_point import (
    EVIDENCE_GROUNDING_FIELDS,
    L1SituationalExample,
    L1SupplementalRule,
)


def _truncate(max_len: int) -> Callable[[Any], Any]:
    """Silent head-keeping truncation for LLM outputs that overshoot a Field cap — for both
    string length and list count.

    Pydantic's max_length raises ValidationError on overflow, which would discard the *entire*
    critique on a single overrun field AND force a full (paid) schema-repair round-trip. The
    schema cap stays (it tells the LLM the budget via JSON Schema export); this BeforeValidator
    drops the tail and keeps the head so a 5th axis or 4th highlight clips instead of hard-failing.
    """

    def _v(value: Any) -> Any:
        if isinstance(value, (str, list)) and len(value) > max_len:
            return value[:max_len]
        return value

    return _v


__all__ = [
    "OPTIMIZER_RESPONSE_MODELS",
    "CheckinOutput",
    "CheckinTaskContext",
    "L1CritiqueOutput",
    "L1GenerateOutput",
    "L1SituationalExample",
    "L1SupplementalRule",
    "L1Variant",
    "L2ContextOutput",
    "L3PlanOutput",
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
        # dict[str, Any]: json_schema_extra is freeform schema, not a strict
        # JsonValue tree, and a bare list[str] literal trips list-invariance.
        json_schema_extra=cast("dict[str, Any]", {"enum": sorted(EVIDENCE_GROUNDING_FIELDS)}),
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
    """Critique returned at round-end. Three load-bearing fields only:
    ``priority_fix`` (the headline steer), ``failure_highlights`` (per-
    sample evidence quotes), ``suggested_axes`` (for L2's axis pick).
    Prose ``summary`` + ``positive_critique`` + ``negative_critique``
    were dropped — priority_fix already names axis+change, origin_strengths
    panel carries the preserve signal, failure_highlights enumerates the
    other open clusters."""

    model_config = ConfigDict(extra="forbid")

    priority_fix: Annotated[str, BeforeValidator(_truncate(200))] = Field(
        default="", max_length=200
    )
    suggested_axes: Annotated[list[str], BeforeValidator(_truncate(4))] = Field(
        default_factory=list,
        max_length=4,
        description="≤4 short axis names. Each item ≤40 chars (downstream truncates).",
    )
    failure_highlights: Annotated[list[str], BeforeValidator(_truncate(3))] = Field(
        default_factory=list,
        max_length=3,
        description=(
            "≤3 failure diagnoses quoting the decisive transcript evidence — claim, "
            "broken reasoning step, predicted vs GT. Each item ≤320 chars (downstream truncates)."
        ),
    )


# ---------------------------------------------------------------------------
# Fork proposal — emitted by L2 or L3 to rewind the search to an earlier round.
# ---------------------------------------------------------------------------


class ForkProposal(BaseModel):
    """L2/L3-emitted proposal to rewind the search to an earlier round.

    ``round_offset`` is relative to the current round and typically
    negative (rewind). When set, the runner mints a sibling cycle via
    ``_mint_fork(L{2,3}_REBASE, fork_from_round=current + round_offset)``,
    exits the current cycle with ``StopReason.REBASED``, and
    auto-continues optimization on the new fork (capped at
    ``MAX_AUTO_REBASES`` per CLI invocation).
    """

    model_config = ConfigDict(extra="forbid")

    round_offset: int
    reason: str = ""


# ---------------------------------------------------------------------------
# Terminate proposal — emitted by L2 or L3 to STOP the cycle when the failure
# is unrecoverable through any framing/plan move (e.g. a starved evidence node).
# ---------------------------------------------------------------------------


class TerminateProposal(BaseModel):
    """L2/L3-emitted decision to terminate the cycle — the intelligent-tier
    counterpart to the deterministic stop conditions.

    Mirrors :class:`ForkProposal` (a layer-emitted control output), but instead of
    rewinding it ends the run: the layer has judged the fault unrecoverable through
    any framing refinement or replan (the canonical first user is an evidence-starved
    enricher — a node failing across the round because its backend quota is exhausted —
    which no prompt change can fix). When set, ``_run_transition`` raises
    ``StopLoop(StopReason.ABORT)`` after adopting the layer's output; the cycle finalizes
    HALTED on the current cycle_id. ``reason`` carries the operator-facing specifics
    ("L2: web_search dead 2 rounds, no framing recovers it — fix the backend + resume").
    A deterministic rule cannot judge "is this recoverable?"; the LLM tier can (R-48)."""

    model_config = ConfigDict(extra="forbid")

    reason: str = ""


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
    fork_proposal: ForkProposal | None = None
    terminate_proposal: TerminateProposal | None = None


# ---------------------------------------------------------------------------
# l3_plan — strategic replan with optional sticky pointer to L2.
# ---------------------------------------------------------------------------


class L3PlanOutput(BaseModel):
    """L3 strategic replan."""

    model_config = ConfigDict(extra="forbid")

    plan: str
    note: str = ""
    rationale: str = ""
    fork_proposal: ForkProposal | None = None
    terminate_proposal: TerminateProposal | None = None


# ---------------------------------------------------------------------------
# checkin — one-time decomposition of user context into Layer-1 fields.
# ---------------------------------------------------------------------------


class CheckinTaskContext(BaseModel):
    """Domain-context sub-object inside the checkin output."""

    model_config = ConfigDict(extra="forbid")

    domain: str = ""
    pipeline_purpose: str = ""
    data_characteristics: str = ""
    optimization_goals: str = ""
    key_challenges: str = ""
    upstream_context: str = ""
    downstream_context: str = ""


class OriginFinding(BaseModel):
    """One origin-readiness field the resolver proposes a value for.

    ``evidence`` is mandatory in spirit — a finding citing nothing from the
    input (a header name, a sample value, an operator statement) is rejected by
    the apply loop, mirroring the ``evidence_grounding`` contract on
    ``l1_generate``. Only ``confidence == "high"`` auto-confirms; ``"low"``
    lands the field PROPOSED and waits for an operator click."""

    model_config = ConfigDict(extra="forbid")

    field: str = Field(
        default="", description="Checklist field id, e.g. 'task_description', 'column.query'."
    )
    proposed_value: str = Field(default="", description="The value proposed for this field.")
    confidence: str = Field(
        default="low", description="'high' or 'low'. Only 'high' auto-confirms."
    )
    evidence: str = Field(
        default="",
        description="What in the input supports this — a header name, a sample value, or a stated operator preference. Findings with no evidence are rejected.",
    )


class OriginQuestion(BaseModel):
    """One operator-facing question on a ``kind='ask'`` turn.

    ``field`` names the checklist field the answer resolves so the panel can
    apply it as a confirmed patch directly (closing the resolver→operator
    loop), rather than the operator hunting for the matching control.
    ``options``, when non-empty, is a closed set the answer must come from
    (rendered as a picker); empty means free text."""

    model_config = ConfigDict(extra="forbid")

    field: str = Field(
        default="", description="Checklist field id the answer resolves, e.g. 'column.query'."
    )
    prompt: str = Field(default="", description="Short operator-facing question.")
    options: list[str] = Field(
        default_factory=list,
        description="Optional closed set of acceptable answers; empty = free text.",
    )


class OriginNextAction(BaseModel):
    """What the resolver wants to happen next. The deterministic checklist —
    not this field — decides completeness; a false ``ready`` is re-checked and
    rejected."""

    model_config = ConfigDict(extra="forbid")

    kind: str = Field(
        default="propose",
        description="'ask' (need operator input), 'propose' (findings applied), or 'ready' (resolver believes origin complete — re-checked).",
    )
    questions: list[OriginQuestion] = Field(
        default_factory=list,
        description="For kind='ask': operator-facing questions, each naming the field it resolves so the answer applies directly.",
    )


class CheckinOutput(BaseModel):
    """Output of the checkin prompt. Two modes share one shape:

    * **Task decomposition** (CLI ``new``) — raw context → the Layer-1 prompt
      fields + ``task_context`` sub-object. The origin block stays empty.
    * **Origin resolution** (web ingest check-in) — a draft-campaign origin →
      ``assessment`` + ``findings`` (proposed field values, each evidence-cited)
      + ``next_action``, plus a ``task_description`` framing carried on the
      relevant finding. The Layer-1 fields are ALSO authored here (decomposed
      from the proposed task_description) to seed the campaign's starting prompt.
    """

    model_config = ConfigDict(extra="forbid")

    persona: str = ""
    task_intent: str = ""
    problem_description: str = ""
    instruction: str = ""
    thinking_style: str = ""
    answer_format: str = ""
    task_context: CheckinTaskContext = Field(default_factory=CheckinTaskContext)
    consultation: str = ""
    # Origin-resolution block — populated only on the web ingest check-in path.
    assessment: str = Field(default="", description="One-line read of the current origin state.")
    findings: list[OriginFinding] = Field(default_factory=list)
    next_action: OriginNextAction = Field(default_factory=OriginNextAction)
    recap: str = Field(
        default="",
        description="On a 'ready' turn: a jargon-free paragraph restating what the campaign will do, for the operator to confirm intent.",
    )


# ---------------------------------------------------------------------------
# Node → model registry.
# ---------------------------------------------------------------------------


OPTIMIZER_RESPONSE_MODELS: dict[str, type[BaseModel]] = {
    "l1_generate": L1GenerateOutput,
    "l1_critique": L1CritiqueOutput,
    "l2_context": L2ContextOutput,
    "l3_plan": L3PlanOutput,
    "checkin": CheckinOutput,
}
