"""Pydantic response models for every optimizer LLM node.

**Every class docstring below is PROMPT TEXT.** It reaches the model as the JSON Schema
``description``, so editing one is a prompt edit: regenerate with
``scripts/build_optimizer_schemas.py`` and flag the commit.

Pydantic is the SoT; ``datasets/_optimizer/resolved_schemas.json`` is a
regenerated export for non-Python consumers. ``extra="forbid"`` is load-bearing — it is
what emits ``additionalProperties: false``, without which the SDK silently falls back to
lenient parsing. :class:`L1Variant`'s three override slots are separate so the LLM cannot
conflate the buckets, which is what makes the shape statically validatable.
"""

from __future__ import annotations

import functools
from collections.abc import Callable, Mapping
from typing import Annotated, Any

from pydantic import BaseModel, BeforeValidator, Field, create_model, model_validator

from promptpotter.domain.strict_model import StrictModel


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


def _truncate_marked(max_len: int) -> Callable[[Any], Any]:
    """Word-boundary truncation with a visible ``…`` marker — for the fields whose
    tail is load-bearing prose (a quoted failure pattern), where ``_truncate``'s
    silent mid-quote cut reads as a complete-but-wrong steer downstream (run
    b786e9: a candidate faithfully implemented a priority_fix cut mid-sentence).
    """

    def _v(value: Any) -> Any:
        if isinstance(value, str) and len(value) > max_len:
            return value[: max_len - 1].rsplit(" ", 1)[0] + "…"
        return value

    return _v


__all__ = [
    "OPTIMIZER_RESPONSE_MODELS",
    "CheckinOutput",
    "CheckinTaskContext",
    "L1CritiqueOutput",
    "L1GenerateOutput",
    "L1Variant",
    "L2ContextOutput",
    "L3PlanOutput",
    "VariantEvidenceGrounding",
    "build_l1_response_model",
]


# ---------------------------------------------------------------------------
# l1_generate — proposes candidate variants for the next round.
# ---------------------------------------------------------------------------


class VariantEvidenceGrounding(StrictModel):
    """One row of evidence justifying a variant's chosen mutation.

    ``field`` names the panel entry the LLM is grounding on. The allowed values are
    per-round — the evidence panels the node's live layout renders, plus the stall
    escape hatch — so the ``enum`` is grafted onto the wire schema at build time by
    :func:`l1_strict.build_l1_response_schema` from ``registry.citable_fields``, not
    frozen here. A model-level enum could only restate a set this model cannot see, and
    would go stale the moment a layout edit changed what L1 is shown.

    The parse boundary stays permissive — providers like Groq don't honor the enum and a
    single off-script value would otherwise crash the round. The canonical enforcement is
    the ``evidence_grounding_present`` behavior check (see :mod:`l1_behavior`).
    """

    field: str = Field(description="A citable panel named in the prompt, or stall_exploration.")
    citation: str


class L1Variant(StrictModel):
    """One candidate variant proposed by ``l1_generate``.

    Three override slots, each carrying a different layer's mutations:

    - ``pipeline_params_override`` — per-node tunables, shape ``{node:
      {param: value}}``. Inner shape is grafted at runtime by
      :func:`l1_validators.build_l1_response_schema` from the active
      ``PipelineSchema``; the keys at this level remain ``dict[str, dict]``
      so the Pydantic parse never fails on a legitimately backend-specific
      node name.
    - ``prompt_fields_override`` — top-level prompt fields, shape
      ``{field_name: string}``. Keys constrained to ``PROMPT_STRING_FIELDS``
      by the runtime-built JSON Schema.
    - ``task_context_override`` — pipeline-context strings, shape
      ``{field_name: string}``. Keys constrained to ``TASK_CONTEXT_OVERRIDES``
      by the runtime-built JSON Schema.

    At least one of the three must be non-empty — enforced HERE, at parse
    (:meth:`_reject_empty_mutation`), so an all-empty variant re-asks the provider
    (`openai_compat`'s one-shot schema repair, which feeds the error text back) instead
    of costing a candidate slot. :func:`detect_invariants` keeps the parent-RELATIVE
    check — a variant whose overrides merely restate the parent's own values — which
    this model cannot see.
    """

    # FIELD ORDER IS GENERATION ORDER — evidence precedes the decision it justifies, and the
    # decision is the MUTATION, not the prose about it. `changes_description` trails the three
    # override slots so it can only ever REPORT a mutation already emitted. Ahead of them it was
    # a promise the model could make and then break, and it did: the required set asked for a
    # name and a paragraph while marking the payload optional, so ~10% of live variants arrived
    # narrated-but-empty, each one a no-op candidate that dragged the round's diversity and
    # cleanliness down. `evidence_grounding` stays optional at parse time so a provider omitting
    # the citation on one variant doesn't crash the round — `evidence_grounding_present` is its
    # canonical enforcement point and records misses as wounds without burning an LLM call.
    evidence_grounding: VariantEvidenceGrounding | None = None
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
    changes_description: str

    @model_validator(mode="after")
    def _reject_empty_mutation(self) -> L1Variant:
        if not (
            self.pipeline_params_override
            or self.prompt_fields_override
            or self.task_context_override
        ):
            raise ValueError(
                "this variant mutates nothing: at least one of pipeline_params_override, "
                "prompt_fields_override or task_context_override must be non-empty. "
                "Describing a change in changes_description is not making one — emit the "
                "override that carries it."
            )
        return self


class L1GenerateOutput(StrictModel):
    """Top-level shape returned by the ``l1_generate`` meta-prompt."""

    variants: list[L1Variant]


def build_l1_response_model(field_names: Mapping[str, str]) -> type[L1GenerateOutput]:
    """``L1GenerateOutput`` whose variant fields validate through renamed wire keys.

    The un-rename half of the field-NAME lever (`docs/specs/schema-description-axis.md`
    § Unlocking the name). ``field_names`` maps a real :class:`L1Variant` field to the wire
    key the emitted JSON Schema advertises; a ``validation_alias`` accepts that key and binds
    it back onto the field. Every downstream reader keeps seeing ``changes_description``.

    **The old name stops working, deliberately.** ``populate_by_name`` is left off, so a model
    that ignores the rename and emits the original key fails validation → zero candidates →
    the round is charged ``problem_rate = 1.0``. That is the whole safety story: a rename the
    model cannot honour is self-penalising, not silently half-applied.

    Empty map → :class:`L1GenerateOutput` itself, so the non-renamed path allocates nothing.
    """
    if not field_names:
        return L1GenerateOutput
    return _build_l1_response_model(tuple(sorted(field_names.items())))


@functools.lru_cache(maxsize=16)
def _build_l1_response_model(items: tuple[tuple[str, str], ...]) -> type[L1GenerateOutput]:
    overrides: dict[str, Any] = {}
    for field, wire in items:
        info = L1Variant.model_fields[field]
        kwargs: dict[str, Any] = {
            "validation_alias": wire,
            "serialization_alias": wire,
            "description": info.description,
        }
        if info.default_factory is not None:
            kwargs["default_factory"] = info.default_factory
        elif not info.is_required():
            kwargs["default"] = info.default
        # No default and no default_factory ⇒ Field() stays required, matching the source field.
        overrides[field] = (info.annotation, Field(**kwargs))
    variant = create_model("L1Variant", __base__=L1Variant, **overrides)
    suffix = "_".join(f"{f}2{w}" for f, w in items)
    return create_model(
        f"L1GenerateOutput__{suffix}",
        __base__=L1GenerateOutput,
        # `variant` is built by `create_model` above, so it is a value at type-check time
        # and a class only at runtime. That is the intended construction here, not a mistake.
        variants=(list[variant], ...),
    )


# ---------------------------------------------------------------------------
# l1_critique — round-end analysis, feeds the next round's L1.
# ---------------------------------------------------------------------------


class L1CritiqueOutput(StrictModel):
    """Critique returned at round-end. Three load-bearing fields only:
    ``priority_fix`` (the headline steer), ``failure_highlights`` (per-
    sample evidence quotes), ``suggested_axes`` (for L2's axis pick).
    Prose ``summary`` + ``positive_critique`` + ``negative_critique``
    were dropped — priority_fix already names axis+change, origin_strengths
    panel carries the preserve signal, failure_highlights enumerates the
    other open clusters."""

    # First field on purpose: generation order = schema order, so the tail truncates first.
    # failure_highlights carries the per-sample evidence quotes the NEXT round's L1 differentiates
    # on — losing them to a long-output clip is the costliest drop, so it generates before the
    # steers. The answer_format prose states this same order; the two must stay in lock-step.
    failure_highlights: Annotated[list[str], BeforeValidator(_truncate(3))] = Field(
        default_factory=list,
        max_length=3,
        description=(
            "≤3 failure diagnoses quoting the decisive transcript evidence — claim, "
            "broken reasoning step, predicted vs GT. Each item ≤320 chars (downstream truncates)."
        ),
    )
    # 320 (not 200): the mandated format `<axis>: <change> - addresses <quoted
    # pattern>` cannot hold a real verbatim quote in 200c — observed truncating
    # mid-quote, and the clipped steer still drove candidates (b786e9).
    priority_fix: Annotated[str, BeforeValidator(_truncate_marked(320))] = Field(
        default="", max_length=320
    )
    suggested_axes: Annotated[list[str], BeforeValidator(_truncate(4))] = Field(
        default_factory=list,
        max_length=4,
        description="≤4 short axis names. Each item ≤40 chars (downstream truncates).",
    )


# ---------------------------------------------------------------------------
# Fork proposal — emitted by L2 or L3 to rewind the search to an earlier round.
# ---------------------------------------------------------------------------


class ForkProposal(StrictModel):
    """L2/L3-emitted proposal to rewind the search to an earlier round.

    **The layer decides WHETHER; UCB decides WHERE.** There is deliberately no
    ``round_offset`` here. The rewind target is selected by
    ``mask/backprop.py::select_rewind_round`` — UCB1 over the backpropagated lineage
    (each ancestor's mean θ, traded off against how little it has been explored). The
    layer supplies the one judgment a rule makes badly ("this subtree is spent") and is
    relieved of the one it made blindly: no panel ever enumerated the ancestor rounds
    and their fitness, so a free-form offset was an unanchored guess carrying the most
    expensive decision in the loop.

    When emitted, the runner mints a sibling via ``_mint_fork(L{2,3}_REBASE,
    fork_from_round=<UCB pick>)``, exits with ``StopReason.REBASED``, and auto-continues
    on the new fork (capped at ``MAX_AUTO_REBASES`` per CLI invocation).

    ``unlock_schema_field_rename`` is the layer's one search-policy request. It is
    a bool and not a ``ConfigOverrides`` object on purpose: handed the whole delta
    the layer could move its own spend ceiling. Unlocking widens the search space,
    which is why it can only ride a rewind — the parent cycle keeps its frozen
    config, so its measurements stay comparable.
    """

    reason: str = ""
    unlock_schema_field_rename: bool = Field(
        False,
        description=(
            "Let the fork's L1 rename a field on the inner l1_generate's output schema "
            "(it can only describe fields today). Set ONLY when the panels show the search "
            "stalling on what a field is FOR rather than on what it says — a field whose "
            "name misdescribes its content. Default false."
        ),
    )


# ---------------------------------------------------------------------------
# Terminate proposal — emitted by L2 or L3 to STOP the cycle when the failure
# is unrecoverable through any framing/plan move (e.g. a starved evidence node).
# ---------------------------------------------------------------------------


class TerminateProposal(StrictModel):
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

    reason: str = ""


# ---------------------------------------------------------------------------
# l2_context — refine task framing + optional layout/runtime knobs.
# ---------------------------------------------------------------------------


class L2ContextOutput(StrictModel):
    """L2 refinement. All fields optional — the LLM sets only what it
    wants to change.

    There is deliberately **no ``task_context``**: the framing fields are operator-authored
    and frozen for the run (``TaskDecomposition.FRAMING_FIELDS``). L2 steers by choosing
    what L1 *looks at* (``l1_layout``) and how hard it explores (``l1_overrides``) — never
    by rewriting what the operator wrote about the task. ``l1_layout`` is a flexible
    ``{slot: [placeholder, ...]}`` map validated downstream by :func:`validate_l1_layout`.

    There is also no ``action``: L2 cannot request a probe round. See
    ``optimization/CLAUDE.md`` § *Probe rounds — deferred* for what a probe round is meant
    to be and why the lever is not wired.
    """

    axis_targeted: str = ""
    l1_layout: dict[str, list[str]] = Field(default_factory=dict)
    l1_overrides: dict[str, Any] = Field(default_factory=dict)
    rationale: str = ""
    fork_proposal: ForkProposal | None = None
    terminate_proposal: TerminateProposal | None = None


# ---------------------------------------------------------------------------
# l3_plan — strategic replan with optional sticky pointer to L2.
# ---------------------------------------------------------------------------


class L3PlanOutput(StrictModel):
    """L3 strategic replan."""

    plan: str
    note: str = ""
    rationale: str = ""
    fork_proposal: ForkProposal | None = None
    terminate_proposal: TerminateProposal | None = None


# ---------------------------------------------------------------------------
# checkin — one-time decomposition of user context into Layer-1 fields.
# ---------------------------------------------------------------------------


class CheckinTaskContext(StrictModel):
    """Domain-context sub-object inside the checkin output.

    Every field renders into the ``task_context`` panel of EVERY optimizer prompt, VERBATIM —
    nothing truncates it, and nothing in the loop rewrites it afterwards. It is frozen for the
    run, so what is written here is what every optimizer call reads on every round.

    Each is ONE tight sentence, never a paragraph. That is a real budget, not a style note:
    it is checked once at mint (``TaskDecomposition.check_budget``, ``FRAMING_VALUE_BUDGET``)
    and an over-budget field REFUSES the campaign rather than being clipped. Terse framing also
    keeps the prompt short enough that the reasoning model does not over-reason. Detail that
    does not fit belongs in ``task_description.md``, which is preserved whole and unbudgeted."""

    domain: str = Field(
        "", description="One noun phrase — the task family, e.g. 'competition mathematics'."
    )
    pipeline_purpose: str = Field(
        "", description="One sentence: what this campaign produces, for an outside reader."
    )
    data_characteristics: str = Field(
        "",
        description="One sentence (<=40 words): the sample properties L1 must account for — "
        "length, modality, distribution skew, known bias.",
    )
    optimization_goals: str = Field(
        "",
        description="One sentence (<=40 words): what we optimise for, in operator vocabulary.",
    )
    key_challenges: str = Field(
        "",
        description="One sentence (<=40 words): the 1-2 dominant failure patterns to defend "
        "against THIS round — a single current challenge, never a growing list.",
    )
    upstream_context: str = Field(
        "", description="Short framing prepended around problem_description, or empty."
    )
    downstream_context: str = Field(
        "", description="Short framing appended around problem_description, or empty."
    )


class OriginFinding(StrictModel):
    """One origin-readiness field the resolver proposes a value for.

    ``evidence`` is mandatory in spirit — a finding citing nothing from the
    input (a header name, a sample value, an operator statement) is rejected by
    the apply loop, mirroring the ``evidence_grounding`` contract on
    ``l1_generate``. Only ``confidence == "high"`` auto-confirms; ``"low"``
    lands the field PROPOSED and waits for an operator click."""

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


class OriginQuestion(StrictModel):
    """One operator-facing question on a ``kind='ask'`` turn.

    ``field`` names the checklist field the answer resolves so the panel can
    apply it as a confirmed patch directly (closing the resolver→operator
    loop), rather than the operator hunting for the matching control.
    ``options``, when non-empty, is a closed set the answer must come from
    (rendered as a picker); empty means free text."""

    field: str = Field(
        default="", description="Checklist field id the answer resolves, e.g. 'column.query'."
    )
    prompt: str = Field(default="", description="Short operator-facing question.")
    options: list[str] = Field(
        default_factory=list,
        description="Optional closed set of acceptable answers; empty = free text.",
    )


class OriginNextAction(StrictModel):
    """What the resolver wants to happen next. The deterministic checklist —
    not this field — decides completeness; a false ``ready`` is re-checked and
    rejected."""

    kind: str = Field(
        default="propose",
        description="'ask' (need operator input), 'propose' (findings applied), or 'ready' (resolver believes origin complete — re-checked).",
    )
    questions: list[OriginQuestion] = Field(
        default_factory=list,
        description="For kind='ask': operator-facing questions, each naming the field it resolves so the answer applies directly.",
    )


class CheckinOutput(StrictModel):
    """Output of the checkin prompt. Two modes share one shape:

    * **Task decomposition** (CLI ``new``) — raw context → the Layer-1 prompt
      fields + ``task_context`` sub-object. The origin block stays empty.
    * **Origin resolution** (web ingest check-in) — a draft-campaign origin →
      ``assessment`` + ``findings`` (proposed field values, each evidence-cited)
      + ``next_action``, plus a ``task_description`` framing carried on the
      relevant finding. The Layer-1 fields are ALSO authored here (decomposed
      from the proposed task_description) to seed the campaign's starting prompt.
    """

    persona: str = ""
    task_intent: str = ""
    problem_description: str = ""
    instruction: str = ""
    thinking_style: str = ""
    answer_format: str = ""
    task_context: CheckinTaskContext = Field(default_factory=CheckinTaskContext)
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
