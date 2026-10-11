"""Twins of constraints the wire schema declares: not every provider enforces structured output."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import TYPE_CHECKING, Any

from promptpotter.application import optimizers
from promptpotter.application.bench.children import DROPPED_MANDATORY_PLACEHOLDER
from promptpotter.application.optimizers.potter.dispatch import prompts as _opt_prompts
from promptpotter.application.optimizers.potter.dispatch.layout import (
    NODE_LAYOUTS,
    resolve_layout_override,
)
from promptpotter.application.pipeline_resolve import missing_template_vars
from promptpotter.config.prompt_blocks import prompt_blocks
from promptpotter.domain.opt_search_point import TEMPLATE_TOKEN_RE, PromptTemplate
from promptpotter.domain.pipeline_overlay import node_config_items
from promptpotter.domain.pipeline_schema import PipelineSchema
from promptpotter.domain.search_point import WHO_ANSWERS_KEYS
from promptpotter.domain.validators import LLMOutputValidator, ValidatorOutcome
from promptpotter.domain.wounds import RuntimeFailure, ValidationFailure

if TYPE_CHECKING:
    from promptpotter.application.optimizer_manifest import SelectedOptimizer
    from promptpotter.application.optimizers.potter.knobs import PromptBlockCatalogue

__all__ = [
    "L1_CONFIG_NOT_IN_RUNTIME_FAILURES",
    "L1_INNER_LAYOUT_APPLIES",
    "L1_INNER_PLACEHOLDERS_INTACT",
    "L1_INNER_STEER_IS_LEGAL",
    "L1_PROMPT_BLOCKS_IN_LIBRARY",
    "L1_PROMPT_FIELDS_OPEN",
    "L1_PROMPT_FIELD_NOT_GUTTED",
    "L1_SHOTS_IN_DEMO_POOL",
]


def _check_l1_prompt_blocks_in_library(
    source_output: Mapping[str, Any],
    *,
    prompt_block_catalogue: PromptBlockCatalogue,
    **_: Any,
) -> ValidatorOutcome | None:
    """Reads the DELTA: the parent's fields are the authored origin, so the merge rejects every round-1 arm."""
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


def _check_l1_prompt_fields_open(
    source_output: Mapping[str, Any],
    *,
    pipeline_schema: PipelineSchema,
    **_: Any,
) -> ValidatorOutcome | None:
    prompt_nodes = pipeline_schema.prompt_node_names()
    if not source_output or not prompt_nodes:
        return None
    open_fields = pipeline_schema.open_prompt_fields()
    failures = [
        ValidationFailure(
            axis=f"{prompt_nodes[0]}.{field}",
            value=str(value)[:300],
            allowed=open_fields,
            reason="forbidden_axis",
        )
        for field, value in source_output.items()
        if field not in open_fields
    ]
    if not failures:
        return None
    return ValidatorOutcome(
        validator_id=L1_PROMPT_FIELDS_OPEN.id,
        evidence={"failures": failures},
    )


L1_PROMPT_FIELDS_OPEN: LLMOutputValidator = LLMOutputValidator(
    id="l1_prompt_fields_open",
    check=_check_l1_prompt_fields_open,
)


def _check_l1_shots_in_demo_pool(
    source_output: Mapping[str, Any],
    *,
    demo_ids: frozenset[int],
    k_max: int,
    **_: Any,
) -> ValidatorOutcome | None:
    """An id outside the demo pool is a SCORED row pasted into the prompt as a worked answer."""
    shots: list[int] = source_output["shot_ids"]
    failures = [
        ValidationFailure(axis="shot_ids", value=str(i), allowed=[], reason="shot_not_in_demo_pool")
        for i in shots
        if i not in demo_ids
    ]
    if len(set(shots)) != len(shots):
        failures.append(
            ValidationFailure(axis="shot_ids", value=str(shots), allowed=[], reason="shot_repeated")
        )
    if len(shots) > k_max:
        failures.append(
            ValidationFailure(
                axis="shot_ids",
                value=str(len(shots)),
                allowed=[str(k_max)],
                reason="shots_over_k_max",
            )
        )
    if not failures:
        return None
    return ValidatorOutcome(
        validator_id=L1_SHOTS_IN_DEMO_POOL.id,
        evidence={"failures": failures},
    )


L1_SHOTS_IN_DEMO_POOL: LLMOutputValidator = LLMOutputValidator(
    id="l1_shots_in_demo_pool",
    check=_check_l1_shots_in_demo_pool,
)


def _check_l1_config_in_runtime_failures(
    source_output: Mapping[str, Any],
    *,
    runtime_failures: Sequence[RuntimeFailure],
    pipeline_params: Mapping[str, Any] | None = None,
    **_: Any,
) -> ValidatorOutcome | None:
    """A wound convicts a ``(responder, param, value)``: one endpoint's 400 says nothing of the next model's."""
    failures_list = list(runtime_failures)
    if not source_output or not failures_list:
        return None
    merged = pipeline_params or {}
    out_failures: list[ValidationFailure] = []
    for node_name, node_params in source_output.items():
        if not isinstance(node_params, dict):
            continue
        inherited = merged.get(node_name)
        effective = dict(inherited) if isinstance(inherited, dict) else {}
        effective.update(node_params)
        for param, value in node_params.items():
            for rf in failures_list:
                obs_cfg = rf.observed_config or {}
                if not all(
                    obs_cfg.get(k) == effective.get(k) for k in WHO_ANSWERS_KEYS if k in obs_cfg
                ):
                    continue
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


def _optimizer_template_failures(
    pipeline_params: dict[str, Any], inner: SelectedOptimizer
) -> list[ValidationFailure]:
    """Checks the MERGED params: a child inherits token-less prose without re-proposing it."""
    failures: list[ValidationFailure] = []
    for node_name, cfg in node_config_items(pipeline_params):
        prose = {
            k: v for k, v in cfg.items() if k in PromptTemplate.model_fields and isinstance(v, str)
        }
        if not prose:
            continue
        base = _opt_prompts.base_optimizer_template(inner, node_name)
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


def _check_l1_inner_placeholders_intact(
    source_output: Mapping[str, Any],
    *,
    inner_optimizer: SelectedOptimizer | None,
    **_: Any,
) -> ValidatorOutcome | None:
    if not isinstance(source_output, dict) or inner_optimizer is None:
        return None
    failures = _optimizer_template_failures(source_output, inner_optimizer)
    if not failures:
        return None
    return ValidatorOutcome(
        validator_id=L1_INNER_PLACEHOLDERS_INTACT.id,
        evidence={"failures": failures},
    )


L1_INNER_PLACEHOLDERS_INTACT: LLMOutputValidator = LLMOutputValidator(
    id="l1_inner_placeholders_intact",
    check=_check_l1_inner_placeholders_intact,
)


# PHRASES, never tokens: "stop proposing the same axis in consecutive rounds" is a legal steer.
_FORBIDDEN_INNER_STEERS: tuple[tuple[str, tuple[str, ...]], ...] = (
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
    # Quantified forms only: a seed IS one whole inner run, and bare "seed" is legal ("seed prompt").
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
    *,
    inner_optimizer: SelectedOptimizer | None,
    **_: Any,
) -> ValidatorOutcome | None:
    """A prompt that stops the loop early drops the flat tail out of ``mean_round_delta``'s average."""
    if not source_output or inner_optimizer is None:
        return None
    failures: list[ValidationFailure] = []
    for node_name, node_params in source_output.items():
        if not isinstance(node_params, dict):
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


_GUTTABLE_MIN_CHARS = 1000
_GUT_RATIO = 0.35


def _parent_field_text(
    inner: SelectedOptimizer, node: str, field: str, pipeline_params: Mapping[str, Any] | None
) -> str:
    parent = (pipeline_params or {}).get(node)
    if isinstance(parent, Mapping):
        inherited = parent.get(field)
        if isinstance(inherited, str) and inherited:
            return inherited
    template = _opt_prompts.base_optimizer_template(inner, node)
    current = getattr(template, field, "")
    return current if isinstance(current, str) else ""


def _check_l1_prompt_field_not_gutted(
    source_output: Mapping[str, Any],
    *,
    inner_optimizer: SelectedOptimizer | None,
    pipeline_params: Mapping[str, Any] | None = None,
    **_: Any,
) -> ValidatorOutcome | None:
    """An override REPLACES its field whole, so a short one deletes the contracts the parent's prose carried."""
    if not source_output or inner_optimizer is None:
        return None
    failures: list[ValidationFailure] = []
    for node_name, node_params in source_output.items():
        if not isinstance(node_params, dict):
            continue
        for field, value in node_params.items():
            if field not in PromptTemplate.model_fields or not isinstance(value, str):
                continue
            parent = _parent_field_text(inner_optimizer, node_name, field, pipeline_params)
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


def _check_l1_inner_layout_applies(
    source_output: Mapping[str, Any],
    **_: Any,
) -> ValidatorOutcome | None:
    """Convicted on the PROPOSAL: the apply site sits inside the inner task, below every wound channel."""
    if not source_output:
        return None
    failures: list[ValidationFailure] = []
    for node_name, node_params in source_output.items():
        spec = NODE_LAYOUTS.get(node_name)
        if spec is None or not isinstance(node_params, dict) or "layout" not in node_params:
            continue
        axis, edit = f"{node_name}.layout", node_params["layout"]
        offered = {n for n in NODE_LAYOUTS if "layout" in optimizers.llm_nodes()[n].outer_levers}
        if node_name not in offered:
            # Convicted rather than raised: it is a proposal like any other.
            failures.append(
                ValidationFailure(
                    axis=axis,
                    value=str(edit)[:80],
                    allowed=sorted(offered),
                    reason="layout_node_not_l4_editable",
                )
            )
            continue
        for outcome in resolve_layout_override(node_name, edit)[1]:
            named = [str(v) for vs in outcome.evidence.values() if isinstance(vs, list) for v in vs]
            failures.append(
                ValidationFailure(
                    axis=axis,
                    value=", ".join(sorted(named)),
                    allowed=sorted(spec.possible),
                    reason=outcome.validator_id,
                )
            )
    if not failures:
        return None
    return ValidatorOutcome(
        validator_id=L1_INNER_LAYOUT_APPLIES.id,
        evidence={"failures": failures},
    )


L1_INNER_LAYOUT_APPLIES: LLMOutputValidator = LLMOutputValidator(
    id="l1_inner_layout_applies",
    check=_check_l1_inner_layout_applies,
)
