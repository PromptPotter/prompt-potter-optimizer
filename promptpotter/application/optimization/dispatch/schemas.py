"""**``Field(description=)`` is the only model-facing text here; a class docstring is NOT** — the emitted schema
drops those. A field edit IS a prompt edit: regenerate via ``scripts/build_optimizer_schemas.py``."""

from __future__ import annotations

import functools
from collections.abc import Callable, Mapping
from typing import Annotated, Any

from pydantic import (
    BaseModel,
    BeforeValidator,
    ConfigDict,
    Field,
    WithJsonSchema,
    create_model,
    model_validator,
)

from promptpotter.domain.l1_layout import NODE_LAYOUTS, layout_json_schema
from promptpotter.domain.strict_model import StrictModel


def _truncate(max_len: int) -> Callable[[Any], Any]:
    """Silent head-keeping truncation for an over-cap LLM field. Pydantic's ``max_length`` would discard the ENTIRE
    critique on one overrun and force a paid repair round-trip; the schema cap stays, to state the budget."""

    def _v(value: Any) -> Any:
        if isinstance(value, (str, list)) and len(value) > max_len:
            return value[:max_len]
        return value

    return _v


# Per-variant prose ceiling for `l1_generate` — `changes_description` and
# `evidence_grounding.citation`. 320 is this file's established prose cap (`l1_critique`'s
# `priority_fix`, sized so a mandated format can still hold a verbatim quote), and it sits ~29%
# above the measured 249-chars-per-variant, so it binds the tail and leaves the median untouched.
VARIANT_PROSE_MAX = 320

# The only two keys anything reads off `l1_overrides` (`l1/candidate_source.py`). Filtered at parse
# because `_parse_l2` MERGES this LLM-written dict forward on every fire: an invented key was
# never read, never pruned, and rendered uncapped for the rest of the campaign.
L1_OVERRIDE_KEYS: frozenset[str] = frozenset({"creativity", "n_variants"})


def _keep_known_keys(allowed: frozenset[str]) -> Callable[[Any], Any]:
    def _v(value: Any) -> Any:
        if isinstance(value, dict):
            return {k: v for k, v in value.items() if k in allowed}
        return value

    return _v


def _truncate_marked(max_len: int) -> Callable[[Any], Any]:
    """Word-boundary truncation with a VISIBLE marker — for fields whose tail is load-bearing prose, where a silent
    mid-quote cut reads downstream as a complete-but-wrong steer."""

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
    "OptimizerResponseModel",
    "VariantEvidenceGrounding",
    "build_l1_response_model",
]


def _drop_class_description(schema: dict[str, Any], _model: type[BaseModel]) -> None:
    """Strip the class docstring Pydantic hoists into a model's JSON-Schema ``description``."""
    schema.pop("description", None)


class OptimizerResponseModel(StrictModel):
    """Base for every model whose JSON Schema goes on the wire; class descriptions are dropped. Config is inherited and
    MERGED, so nested models are covered without restating ``extra="forbid"``."""

    model_config = ConfigDict(json_schema_extra=_drop_class_description)


# ---------------------------------------------------------------------------
# l1_generate — proposes candidate variants for the next round.
# ---------------------------------------------------------------------------


class VariantEvidenceGrounding(OptimizerResponseModel):
    """One row of evidence justifying a variant's mutation. ``field``'s per-round ``enum`` is grafted onto the wire schema,
    but the parse boundary stays PERMISSIVE because not every provider honours it — the validator is the enforcement."""

    field: str = Field(description="A citable panel named in the prompt, or stall_exploration.")
    # WIRE-strict, PARSE-permissive — the same split `evidence_grounding` itself is built on one
    # class down. `maxLength` states the budget where the model reads it; the parse boundary must
    # NOT truncate, because `_citation_in_prompt` (`validators/l1_behavior.py`) substring-matches
    # this against the rendered prompt, and a truncation marker would fail every real quote.
    citation: Annotated[str, WithJsonSchema({"type": "string", "maxLength": VARIANT_PROSE_MAX})]


class L1Variant(OptimizerResponseModel):
    """One candidate variant. Each override slot's INNER shape is grafted at runtime from the active schema; the keys stay
    loose so a backend-specific node name never fails the parse. An all-empty variant re-asks instead of costing a slot."""

    # FIELD ORDER IS GENERATION ORDER — evidence precedes the decision it justifies, and the
    # decision is the MUTATION, not the prose about it. `changes_description` trails the three
    # override slots so it can only ever REPORT a mutation already emitted. Ahead of them it was
    # a promise the model could make and then break, and it did: the required set asked for a
    # name and a paragraph while marking the payload optional, so ~10% of live variants arrived
    # narrated-but-empty, each one a no-op candidate that dragged the round's diversity and
    # cleanliness down. `evidence_grounding` stays optional HERE, at the parse boundary, so a
    # provider omitting the citation on one variant doesn't crash the round —
    # `evidence_grounding_present` is its canonical enforcement point and records misses as wounds
    # without burning an LLM call. It is REQUIRED on the wire (`l1_wire_schema.py`), and the split
    # is the point: tolerating an omission is not the same as offering one. While the emitted
    # schema also carried the `| None`, `null` was a legal answer to a mandatory question, and 2 of
    # 19 live rounds gave it — for every variant in the call, one response being one decision.
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
    # Capped like every other node's prose. `l1_generate` was the ONLY optimizer node with no
    # length bound on any output field, while it is the most-fired and the one whose answer can
    # run into `max_tokens` — a truncated response is not a short answer, it is a ZERO-CANDIDATE
    # ROUND (`l1/generate.py` classifies it `L1_PARSE_FAILURE_MALFORMED`). Measured over 133
    # banked rounds, this field plus `citation` are 36% of the answer JSON at ~249 chars each,
    # so the cap binds only the tail. Marked truncation, not silent: the tail is a steer a
    # reader would otherwise take as complete.
    changes_description: Annotated[str, BeforeValidator(_truncate_marked(VARIANT_PROSE_MAX))] = (
        Field(max_length=VARIANT_PROSE_MAX)
    )

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


class L1GenerateOutput(OptimizerResponseModel):
    variants: list[L1Variant]


def build_l1_response_model(field_names: Mapping[str, str]) -> type[L1GenerateOutput]:
    """``L1GenerateOutput`` validating through renamed wire keys. ``populate_by_name`` is left OFF deliberately: a model
    emitting the original key fails validation and self-penalises, rather than the rename silently half-applying."""
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


class L1CritiqueOutput(OptimizerResponseModel):
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


class ForkProposal(OptimizerResponseModel):
    """L2/L3-emitted proposal to rewind the search. It carries no round offset — see ``optimization/CLAUDE.md``."""

    reason: Annotated[str, BeforeValidator(_truncate_marked(150))] = Field(
        default="",
        max_length=150,
        description="1-2 sentences naming the cause and what the operator must fix.",
    )
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


class TerminateProposal(OptimizerResponseModel):
    """L2/L3-emitted decision to terminate the cycle — the HITL stop, peer of :class:`ForkProposal`."""

    reason: Annotated[str, BeforeValidator(_truncate_marked(150))] = Field(
        default="",
        max_length=150,
        description="1-2 sentences naming the cause and what the operator must fix.",
    )


# ---------------------------------------------------------------------------
# l2_context — refine task framing + optional layout/runtime knobs.
# ---------------------------------------------------------------------------


class L2ContextOutput(OptimizerResponseModel):
    """L2 refinement. The LEVERS are optional — set only what you change; the REASON
    (``axis_targeted`` + ``rationale``) rides every fire, because it is what the behaviour checks grade and the only
    thing separating a steer from a guess. It deliberately carries neither ``task_context`` (operator-authored,
    frozen for the run) nor ``action``."""

    # Sized off 70 live fires (output 582 median / 2187 max; `rationale` peaked at 745,
    # `axis_targeted` at 14), so a normal call is untouched. `l1_layout` needs no char bound —
    # its vocabulary is the placeholder registry, which the schema now DECLARES instead of leaving
    # `validate_l1_layout` to discover it was ignored. Enforcing a vocabulary is not stating one.
    axis_targeted: Annotated[str, BeforeValidator(_truncate_marked(30))] = Field(
        default="",
        max_length=30,
        description=(
            "The L1 axis the failure cluster routes to — a prompt field for a semantic failure, "
            "a param for a quantitative one. Name it on every fire; it is the anchor the lever "
            "is judged against, not a label for the lever."
        ),
    )
    # PARSED as a plain dict on purpose. Typing the slots would make one off-enum signal fail the
    # whole model, taking `axis_targeted`, `rationale` and both control proposals down with the
    # layout edit — and the breach must still reach L2's next fire as a `ValidatorOutcome`, which
    # is a thing only `validate_l1_layout` can emit. The schema teaches; the validator judges.
    l1_layout: Annotated[
        dict[str, list[str]], WithJsonSchema(layout_json_schema(NODE_LAYOUTS["l1_generate"]))
    ] = Field(default_factory=dict)
    l1_overrides: Annotated[dict[str, Any], BeforeValidator(_keep_known_keys(L1_OVERRIDE_KEYS))] = (
        Field(
            default_factory=dict,
            description="Optional runtime knobs; only 'creativity' and 'n_variants' are read.",
        )
    )
    # Optional at PARSE time only, so a fire that omits it still lands its layout edit rather
    # than taking the whole model down. It is not optional to WRITE: two behaviour checks read
    # it (`l2_rationale_substantive` against a 40-char floor, `l2_evidence_anchored` for the
    # cited number), and neither floor was stated anywhere the model could see. Undescribed and
    # sitting under an "set only what you change" header, it read as a lever to skip — 3 of 14
    # live fires returned it empty, failing both checks at once.
    rationale: Annotated[str, BeforeValidator(_truncate_marked(400))] = Field(
        default="",
        max_length=400,
        description=(
            "Why this fire, in 1-2 sentences: the failure cluster you are attacking and the "
            "named data point behind it — a sample id, a count, a yield number. Required on "
            "every fire, including one that pulls no lever; a lever with no diagnosis behind "
            "it cannot be told from a guess."
        ),
    )
    fork_proposal: ForkProposal | None = None
    terminate_proposal: TerminateProposal | None = None


# ---------------------------------------------------------------------------
# l3_plan — strategic replan with optional sticky pointer to L2.
# ---------------------------------------------------------------------------


class L3PlanOutput(OptimizerResponseModel):
    # `plan` rides EVERY downstream prompt until the next replan, so its length is a standing
    # tax — and it was the only unbounded output. Asking for the budget in `answer_format`
    # cannot work (a model cannot count the characters it emits), so it is judged here and
    # MARKED: a plan cut mid-bullet must not read downstream as a complete strategy.
    plan: Annotated[str, BeforeValidator(_truncate_marked(800))] = Field(max_length=800)
    note: Annotated[str, BeforeValidator(_truncate_marked(150))] = Field(default="", max_length=150)
    rationale: Annotated[str, BeforeValidator(_truncate_marked(125))] = Field(
        default="", max_length=125
    )
    fork_proposal: ForkProposal | None = None
    terminate_proposal: TerminateProposal | None = None


# ---------------------------------------------------------------------------
# checkin — one-time decomposition of user context into Layer-1 fields.
# ---------------------------------------------------------------------------


class CheckinTaskContext(OptimizerResponseModel):
    """Domain context inside the checkin output. Every field renders VERBATIM into every optimizer prompt and is frozen for
    the run, so an over-budget one is REFUSED at mint rather than clipped by a renderer. Overflow belongs elsewhere."""

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


class OriginFinding(OptimizerResponseModel):
    """One origin-readiness field the resolver proposes a value for. An UNCITED finding is rejected by the apply loop, and
    only ``confidence == "high"`` auto-confirms."""

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


class OriginQuestion(OptimizerResponseModel):
    """One operator-facing question on an ``ask`` turn. ``field`` names the checklist field the answer resolves, so the
    panel applies it as a confirmed patch rather than the operator hunting for the control."""

    field: str = Field(
        default="", description="Checklist field id the answer resolves, e.g. 'column.query'."
    )
    prompt: str = Field(default="", description="Short operator-facing question.")
    options: list[str] = Field(
        default_factory=list,
        description="Optional closed set of acceptable answers; empty = free text.",
    )


class OriginNextAction(OptimizerResponseModel):
    """What the resolver wants next. The deterministic checklist — not this field — decides
    completeness, so a false ``ready`` is re-checked and rejected."""

    kind: str = Field(
        default="propose",
        description="'ask' (need operator input), 'propose' (findings applied), or 'ready' (resolver believes origin complete — re-checked).",
    )
    questions: list[OriginQuestion] = Field(
        default_factory=list,
        description="For kind='ask': operator-facing questions, each naming the field it resolves so the answer applies directly.",
    )


class CheckinOutput(OptimizerResponseModel):
    """Output of the checkin prompt. Two modes share one shape: task decomposition leaves the origin block empty, origin
    resolution fills it AND the Layer-1 fields — which seed the campaign's starting prompt either way."""

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


# Fail import if a model here — or one nested under it — still carries its class docstring as
# a schema `description`. A test is the wrong home: a new model on plain `StrictModel`
# typechecks and the drift gate passes a faithfully-regenerated docstring, so the failure is
# silent. The PROPERTY is asserted; inheriting the base is one way to satisfy it.
_leaky = sorted(
    title
    for _model in OPTIMIZER_RESPONSE_MODELS.values()
    for _schema in (_model.model_json_schema(),)
    for block in (_schema, *(_schema.get("$defs") or {}).values())
    if block.get("description") and (title := str(block.get("title", _model.__name__)))
)
if _leaky:
    raise RuntimeError(
        "Optimizer response schemas carry a class-level `description` — a class docstring "
        f"hoisted onto the wire, read by the model on every call: {_leaky}. Inherit "
        "`OptimizerResponseModel`; put model-facing text in `Field(description=)`."
    )
del _leaky
