"""The REJECT posture over ``l1_generate``'s output: every check here answers a ``ValidatorOutcome``
that routes back up as a ``ValidationFailure`` and the layer heals (`../CLAUDE.md` § A validator
either REJECTS or SCORES). Deterministic twins of constraints the emitted schema already declares —
both layers run because not every provider enforces structured output with full fidelity.

The schema those constraints are declared in is emitted one package over
(`dispatch/l1_wire_schema.py`); the round-local collapse gates that reject a candidate without
consulting the schema at all are `l1_invariants.py`."""

from __future__ import annotations

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
    "L1_PROMPT_BLOCKS_IN_LIBRARY",
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
            # ``l1/score/candidate.py`` lets the candidate's real edits score): ``l1_wounds``
            # + the ``validation_failure_rate`` evaluator. The node-name twin of
            # ``validate_l1_layout``'s unknown-placeholder wound.
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
    source_output: Any,
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
    source_output: Any,
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
    source_output: Any,
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
    source_output: Any,
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
