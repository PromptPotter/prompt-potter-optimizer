"""The REJECT posture over ``l1_generate``'s output: every check here answers a ``ValidatorOutcome``
that routes back up as a ``ValidationFailure`` and the layer heals (`../CLAUDE.md` § A validator
either REJECTS or SCORES). Deterministic twins of constraints the emitted schema already declares —
both layers run because not every provider enforces structured output with full fidelity.

The schema those constraints are declared in is emitted one package over
(`dispatch/l1_wire_schema.py`); the round-local collapse gates that reject a candidate without
consulting the schema at all are `l1_invariants.py`."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from promptpotter.application.optimization.dispatch.llm_call import prompts as _opt_prompts
from promptpotter.application.pipeline_resolve import missing_template_vars
from promptpotter.config.prompt_blocks import prompt_blocks
from promptpotter.domain.escalation_signals import ValidationFailure
from promptpotter.domain.l1_layout import NODE_LAYOUTS
from promptpotter.domain.opt_search_point import TEMPLATE_TOKEN_RE, OptSearchPoint, PromptTemplate
from promptpotter.domain.pipeline_overlay import node_config_items
from promptpotter.domain.pipeline_schema import SCHEMA_OWNED_FIELDS, PipelineSchema
from promptpotter.domain.search_point import PARAM_FORBIDDEN_KEYS
from promptpotter.domain.validators import LLMOutputValidator, ValidatorOutcome

__all__ = [
    "DROPPED_MANDATORY_PLACEHOLDER",
    "L1_CONFIG_NOT_IN_RUNTIME_FAILURES",
    "L1_INNER_STEER_IS_LEGAL",
    "L1_PROMPT_BLOCKS_IN_LIBRARY",
    "L1_PROMPT_FIELD_NOT_GUTTED",
    "L1_PROMPT_PLACEHOLDERS_INTACT",
    "L1_SCHEMA_COMPLIANCE",
    "validate_overrides",
]

# A dropped mandatory backend placeholder is structural, not a tunable miss: the round
# loop reads this reason off the candidate reports to fire L2 immediately (patience 0),
# rather than burning l1_patience rounds re-dropping it. Single-sourced so the producer
# (the validator below) and the consumer (`runner/round.py`) never drift.
DROPPED_MANDATORY_PLACEHOLDER = "dropped_mandatory_placeholder"


_JSON_TYPE_TO_PY: dict[str, tuple[type, ...]] = {
    "string": (str,),
    "integer": (int,),
    "number": (int, float),
    "boolean": (bool,),
    "object": (dict,),
    "array": (list,),
}


def _matches_declared_type(value: Any, declared: str) -> bool:
    py_types = _JSON_TYPE_TO_PY.get(declared)
    if py_types is None:
        return True  # type we don't model → unconstrained here (schema gates structure)
    # JSON Schema: booleans are NOT integers/numbers, even though Python
    # says `isinstance(True, int)`. Treat bool as exclusive to "boolean".
    if isinstance(value, bool):
        return declared == "boolean"
    return isinstance(value, py_types)


def validate_overrides(
    pipeline_params_override: dict[str, dict[str, Any]],
    pipeline_schema: PipelineSchema,
) -> list[ValidationFailure]:
    """The deterministic twin of the emitted schema's constraints: both layers run because not
    every provider enforces structured output with full fidelity."""
    failures: list[ValidationFailure] = []
    allowed_models = list(pipeline_schema.available_models)
    emittable = pipeline_schema.node_param_keys()
    for node_name, node_params in pipeline_params_override.items():
        if not isinstance(node_params, dict):
            continue
        node = pipeline_schema.get_node(node_name)
        if node is None:
            # L1 named a node absent from the active schema. The wire schema declares the
            # node-name properties + ``additionalProperties: false``, but the client sends
            # ``strict=False``, so the provider is never FORCED to honour them — this is the
            # deterministic backstop. ``merge_pipeline_params`` drops nodes outside
            # ``active_steps``, so recording it ROUTES the signal without changing what runs,
            # and it is NON-FATAL (the reason-aware synthetic-0 gate in
            # ``l1/score/candidate.py`` lets the candidate's real edits score) and rides
            # ``l1_wounds``. The node-name twin of ``validate_l1_layout``'s
            # unknown-placeholder wound.
            failures.append(
                ValidationFailure(
                    axis=node_name,
                    value=node_name,
                    allowed=sorted(pipeline_schema.active_steps),
                    reason="hallucinated_node",
                )
            )
            continue
        node_allowed = node.param_allowed_values
        node_types = node.param_types
        node_emittable = emittable.get(node_name, set())
        for param, value in node_params.items():
            if param in SCHEMA_OWNED_FIELDS or param in PARAM_FORBIDDEN_KEYS:
                failures.append(
                    ValidationFailure(
                        axis=f"{node_name}.{param}",
                        value=str(value),
                        allowed=[],
                        reason="forbidden_axis",
                    )
                )
                continue
            # After the locked axes, so a leaked `model` keeps its specific reason.
            if param not in node_emittable:
                failures.append(
                    ValidationFailure(
                        axis=f"{node_name}.{param}",
                        value=str(value),
                        allowed=sorted(node_emittable),
                        reason="unknown_param",
                    )
                )
                continue
            declared_type = node_types.get(param)
            if (
                declared_type
                and value is not None
                and not _matches_declared_type(value, declared_type)
            ):
                failures.append(
                    ValidationFailure(
                        axis=f"{node_name}.{param}",
                        value=f"{value!r} ({type(value).__name__})",
                        allowed=[declared_type],
                        reason="type_mismatch",
                    )
                )
                continue
            if param == "model" and allowed_models:
                if value is not None and value not in allowed_models:
                    failures.append(
                        ValidationFailure(
                            axis=f"{node_name}.model",
                            value=str(value),
                            allowed=allowed_models,
                            reason="not_in_available_models",
                        )
                    )
            elif (allowed := node_allowed.get(param)) and value not in allowed:
                failures.append(
                    ValidationFailure(
                        axis=f"{node_name}.{param}",
                        value=str(value),
                        allowed=list(allowed),
                        reason="not_in_param_allowed_values",
                    )
                )
    return failures


def _check_l1_schema_compliance(
    source_output: dict[str, dict[str, Any]],
    *,
    pipeline_schema: PipelineSchema,
    **_: Any,
) -> ValidatorOutcome | None:
    if not source_output or not pipeline_schema:
        return None
    failures = validate_overrides(source_output, pipeline_schema)
    if not failures:
        return None
    return ValidatorOutcome(
        validator_id=L1_SCHEMA_COMPLIANCE.id,
        evidence={"failures": failures},
    )


L1_SCHEMA_COMPLIANCE: LLMOutputValidator = LLMOutputValidator(
    id="l1_schema_compliance",
    check=_check_l1_schema_compliance,
)


def _check_l1_prompt_blocks_in_library(
    source_output: Mapping[str, Any],
    *,
    prompt_block_catalogue: str = "guidance",
    **_: Any,
) -> ValidatorOutcome | None:
    """Reads the round's ``prompt_fields_override`` — the DELTA, not the resulting OSP: the parent's
    fields are the dataset's authored origin, so checking the merge rejects every round-1 candidate."""
    if prompt_block_catalogue != "restrict" or not source_output:
        return None
    library = prompt_blocks()
    failures = [
        ValidationFailure(
            axis=field,
            value=str(value),
            allowed=list(blocks),
            reason="not_in_prompt_block_library",
        )
        for field, value in source_output.items()
        if (blocks := library.get(field))
        and str(value).strip()
        and str(value).strip() not in blocks
    ]
    if not failures:
        return None
    return ValidatorOutcome(
        validator_id=L1_PROMPT_BLOCKS_IN_LIBRARY.id,
        evidence={"failures": failures},
    )


L1_PROMPT_BLOCKS_IN_LIBRARY: LLMOutputValidator = LLMOutputValidator(
    id="l1_prompt_blocks_in_library",
    check=_check_l1_prompt_blocks_in_library,
)


def _check_l1_config_in_runtime_failures(
    source_output: Mapping[str, Any],
    *,
    opt_sp: OptSearchPoint | None = None,
    **_: Any,
) -> ValidatorOutcome | None:
    """Sibling-fork inheritance populates ``runtime_failures`` from prior cycles' terminal wounds,
    so this fires even on round 1 of a fresh fork."""
    if not source_output or opt_sp is None:
        return None
    failures_list = list(opt_sp.memory.wounds.runtime_failures)
    if not failures_list:
        return None
    out_failures: list[ValidationFailure] = []
    for node_name, node_params in source_output.items():
        if not isinstance(node_params, dict):
            continue
        for param, value in node_params.items():
            for rf in failures_list:
                obs_cfg = rf.observed_config or {}
                if param in obs_cfg and obs_cfg[param] == value:
                    out_failures.append(
                        ValidationFailure(
                            axis=f"{node_name}.{param}",
                            value=str(value),
                            allowed=[],
                            reason="reproposes_known_failing_config",
                        )
                    )
                    break
    if not out_failures:
        return None
    return ValidatorOutcome(
        validator_id=L1_CONFIG_NOT_IN_RUNTIME_FAILURES.id,
        evidence={"failures": out_failures},
    )


L1_CONFIG_NOT_IN_RUNTIME_FAILURES: LLMOutputValidator = LLMOutputValidator(
    id="l1_config_not_in_runtime_failures",
    check=_check_l1_config_in_runtime_failures,
)


def _optimizer_template_failures(pipeline_params: dict[str, Any]) -> list[ValidationFailure]:
    """PERMANENT, not transitional: these ports sit mid-sentence, so they can never move to the
    layout channel. Checks the MERGED params — a child inherits token-less prose without re-proposing it."""
    failures: list[ValidationFailure] = []
    for node_name, cfg in node_config_items(pipeline_params):
        if node_name not in NODE_LAYOUTS:
            continue
        prose = {
            k: v for k, v in cfg.items() if k in PromptTemplate.model_fields and isinstance(v, str)
        }
        if not prose:
            continue
        base = _opt_prompts.base_optimizer_template(node_name)
        declared = sorted(set(TEMPLATE_TOKEN_RE.findall(base.render())))
        if not declared:
            continue
        merged = base.model_copy(update=prose)
        missing = missing_template_vars(merged.render(), declared)
        if missing:
            failures.append(
                ValidationFailure(
                    axis=f"{node_name}.prompt",
                    value="dropped:" + ",".join(missing),
                    allowed=declared,
                    reason=DROPPED_MANDATORY_PLACEHOLDER,
                )
            )
    return failures


def _check_l1_prompt_placeholders_intact(
    source_output: Mapping[str, Any],
    *,
    opt_sp: OptSearchPoint | None = None,
    pipeline_schema: PipelineSchema | None = None,
    **_: Any,
) -> ValidatorOutcome | None:
    """A mutation may DEGRADE what flows through a channel (measurable — the proxy goes negative),
    never delete the channel itself (unmeasurable). Mint's ``missing_template_vars`` in-loop twin."""
    if opt_sp is None or pipeline_schema is None:
        return None
    failures: list[ValidationFailure] = []
    prompt_nodes = pipeline_schema.prompt_node_names()
    if prompt_nodes:
        node = pipeline_schema.get_node(prompt_nodes[0])
        if node is not None and node.prompt_info is not None:
            declared = node.prompt_info.template_variables
            missing = missing_template_vars(opt_sp.render(), declared) if declared else []
            if missing:
                failures.append(
                    ValidationFailure(
                        axis=f"{prompt_nodes[0]}.prompt",
                        value="dropped:" + ",".join(missing),
                        allowed=list(declared),
                        reason=DROPPED_MANDATORY_PLACEHOLDER,
                    )
                )
    if isinstance(source_output, dict):
        failures.extend(_optimizer_template_failures(source_output))
    if not failures:
        return None
    return ValidatorOutcome(
        validator_id=L1_PROMPT_PLACEHOLDERS_INTACT.id,
        evidence={"failures": failures},
    )


L1_PROMPT_PLACEHOLDERS_INTACT: LLMOutputValidator = LLMOutputValidator(
    id="l1_prompt_placeholders_intact",
    check=_check_l1_prompt_placeholders_intact,
)


# The steers an L4 override may not make, as (reason, phrases). PHRASE lists, and deliberately not
# token lists: the thing being matched is free prose an LLM wrote, so there is no typed predicate to
# ask instead, and single tokens misfire on legitimate steers — "stop proposing the same axis in
# consecutive rounds" is exactly the edit this loop wants and carries both a stop word and a round
# word. A table rather than a validator apiece because the finding is one finding: the override
# reached outside the inner node's own level or its own job, and each new family is a row.
_FORBIDDEN_INNER_STEERS: tuple[tuple[str, tuple[str, ...]], ...] = (
    # Ends the loop instead of searching it better. Every entry names the loop itself as the
    # thing being ended, so a stop word aimed at anything smaller passes.
    (
        "steers_inner_stopping",
        (
            "early stop",
            "stop early",
            "stops early",
            "stopping early",
            "terminate early",
            "stop the loop",
            "stop the run",
            "stop the search",
            "stop the campaign",
            "halt the loop",
            "halt the run",
            "exit the loop",
            "end the run",
            "end the search",
            "abort the run",
            "no further rounds",
            "skip the remaining rounds",
            "stop optimizing",
            "stop iterating",
        ),
    ),
    # Reasons one level up from where it runs. A seed IS one whole inner run, so an inner node can
    # no more iterate seeds than it can iterate tokens: the quantifier names a collection that
    # exists only in the OUTER loop's view, and the rule is inert wherever the edit is pasted.
    # Quantified forms only — the bare word "seed" is legitimate prose one level up and in
    # "seed prompt", and matching it would convict the sentence that explains the level.
    (
        "steers_across_seeds",
        (
            "each seed",
            "every seed",
            "per seed",
            "per-seed",
            "all seeds",
            "across seeds",
            "seed-level",
            "each inner run",
            "every inner run",
        ),
    ),
)


def _check_l1_inner_steer_is_legal(
    source_output: Mapping[str, Any],
    **_: Any,
) -> ValidatorOutcome | None:
    """An edit may make the inner loop search BETTER or WORSE — measurable either way. It may not
    change how many rounds that loop runs, nor address a level the node it lands in cannot see;
    neither is a search move at all.

    Stopping: ``mean_round_delta`` is the mean improvement ACROSS the inner rounds, so a prompt that
    stops the loop as soon as gains thin scores higher by dropping the flat tail out of the average
    — with no better search behind it. That is the measurand's denominator being edited rather than
    the thing it measures, and it outscores real work every time. Round budget is the OUTER loop's
    (``max_rounds`` / ``lives`` / the spend ceiling), the same way ``model`` is the operator's.

    Level: an edit keyed to the seed panel renders nothing one level down, so it costs a candidate
    and measures the parent. It is not wrong the way a bad hypothesis is wrong — it is unrunnable,
    which the round has no way to report as anything but a flat result.

    The sibling of ``_check_l1_prompt_placeholders_intact``: that one forbids DELETING a channel,
    this one forbids writing prose no channel can carry. Reads the DELTA, never the merge — a child
    inheriting a parent's prose has proposed nothing, and checking the merge would convict it for
    its ancestor. Scoped to ``NODE_LAYOUTS``, so it reaches only overrides that ARE inner optimizer
    prompts: on an ordinary campaign the same words in a target prompt steer a task rather than a
    loop, and mean nothing here."""
    if not source_output:
        return None
    failures: list[ValidationFailure] = []
    for node_name, node_params in source_output.items():
        if node_name not in NODE_LAYOUTS or not isinstance(node_params, dict):
            continue
        for field, value in node_params.items():
            if field not in PromptTemplate.model_fields or not isinstance(value, str):
                continue
            prose = " ".join(value.lower().split())
            for reason, phrases in _FORBIDDEN_INNER_STEERS:
                hit = next((p for p in phrases if p in prose), None)
                if hit is not None:
                    failures.append(
                        ValidationFailure(
                            axis=f"{node_name}.{field}",
                            value=hit,
                            allowed=[],
                            reason=reason,
                        )
                    )
    if not failures:
        return None
    return ValidatorOutcome(
        validator_id=L1_INNER_STEER_IS_LEGAL.id,
        evidence={"failures": failures},
    )


L1_INNER_STEER_IS_LEGAL: LLMOutputValidator = LLMOutputValidator(
    id="l1_inner_steer_is_legal",
    check=_check_l1_inner_steer_is_legal,
)


# An override REPLACES its field whole, so a short replacement for a long parent is a deletion
# of every contract the parent carried — and the generator is told to carry them forward, or to
# say in `changes_description` what it dropped and why. Nothing can read that prose, so the
# measurable half is the collapse itself. Both constants are first estimates off the only gutted
# candidates on disk (two at ~14% of their parent); a genuine tightening lands far above them,
# and the floor keeps short fields — a 132-char `thinking_style` — out of reach entirely.
_GUTTABLE_MIN_CHARS = 1000
_GUT_RATIO = 0.35


def _parent_field_text(node: str, field: str, pipeline_params: Mapping[str, Any] | None) -> str:
    """What this field says BEFORE the candidate's edit — the parent's own override where it made
    one, else the manifest template. The same two-step the run resolves, so the length compared
    against is the text the generator was shown as CURRENT INNER OPTIMIZER PROMPTS."""
    parent = (pipeline_params or {}).get(node)
    if isinstance(parent, Mapping):
        inherited = parent.get(field)
        if isinstance(inherited, str) and inherited:
            return inherited
    template = _opt_prompts.base_optimizer_template(node)
    current = getattr(template, field, "")
    return current if isinstance(current, str) else ""


def _check_l1_prompt_field_not_gutted(
    source_output: Mapping[str, Any],
    *,
    pipeline_params: Mapping[str, Any] | None = None,
    **_: Any,
) -> ValidatorOutcome | None:
    """Reject a replacement so much shorter than what it replaces that it cannot have carried the
    parent's contracts forward. Distinct from ``_check_l1_prompt_placeholders_intact``, which
    catches only the contracts spelled as ``{{slots}}``: everything else an inner optimizer prompt
    declares — the output shape, the forbidden moves, the evidence it must ground on — is ordinary
    prose, and deleting it raises nothing and reads as a bold edit.

    Scoped to ``NODE_LAYOUTS`` and to the DELTA for the same reasons as the steer table above."""
    if not source_output:
        return None
    failures: list[ValidationFailure] = []
    for node_name, node_params in source_output.items():
        if node_name not in NODE_LAYOUTS or not isinstance(node_params, dict):
            continue
        for field, value in node_params.items():
            if field not in PromptTemplate.model_fields or not isinstance(value, str):
                continue
            parent = _parent_field_text(node_name, field, pipeline_params)
            if len(parent) < _GUTTABLE_MIN_CHARS:
                continue
            if len(value) >= _GUT_RATIO * len(parent):
                continue
            failures.append(
                ValidationFailure(
                    axis=f"{node_name}.{field}",
                    value=f"{len(value)}B replaces {len(parent)}B",
                    allowed=[],
                    reason="guts_inherited_contract",
                )
            )
    if not failures:
        return None
    return ValidatorOutcome(
        validator_id=L1_PROMPT_FIELD_NOT_GUTTED.id,
        evidence={"failures": failures},
    )


L1_PROMPT_FIELD_NOT_GUTTED: LLMOutputValidator = LLMOutputValidator(
    id="l1_prompt_field_not_gutted",
    check=_check_l1_prompt_field_not_gutted,
)
