"""Twins of constraints the wire schema declares: not every provider enforces structured output."""

from __future__ import annotations

from collections.abc import Collection, Mapping, Sequence
from typing import TYPE_CHECKING, Any

from promptpotter.application import optimizers
from promptpotter.application.bench.children import DROPPED_MANDATORY_PLACEHOLDER
from promptpotter.application.optimizers.potter.dispatch.layout import (
    NODE_LAYOUTS,
    resolve_layout_override,
)
from promptpotter.application.optimizers.potter.dispatch.prompts import inner_template
from promptpotter.domain.opt_search_point import (
    TEMPLATE_TOKEN_RE,
    Locus,
    OptimizerPromptTemplate,
)
from promptpotter.domain.pipeline_overlay import node_config_items
from promptpotter.domain.pipeline_schema import PipelineSchema
from promptpotter.domain.search_point import SHOTS_KEY, WHO_ANSWERS_KEYS, missing_template_vars
from promptpotter.domain.wounds import RuntimeFailure, ValidationFailure

if TYPE_CHECKING:
    from promptpotter.application.runner.inner.tasks import InnerCells


def l1_blocks_in_pool(
    updates: Mapping[str, Mapping[str, str]], *, offered: Mapping[str, Collection[str]]
) -> list[ValidationFailure]:
    """Under ``closed`` every value is an id of its field's active entries: free text, or a field the pool holds nothing for, is no entry."""
    return [
        ValidationFailure(
            axis=Locus(node, field).name,
            value=value[:300],
            allowed=list(offered.get(field, ())),
            reason="not_in_block_pool",
        )
        for node, fields in updates.items()
        for field, value in fields.items()
        if value not in offered.get(field, ())
    ]


def l1_prompt_fields_open(
    prompt_edit: Mapping[Locus, str], *, pipeline_schema: PipelineSchema
) -> list[ValidationFailure]:
    """Per node, against ITS open fields: a node the roster bears no prompt on opens none."""
    open_by_node = {n.name: n.open_prompt_fields for n in pipeline_schema.prompt_nodes()}
    return [
        ValidationFailure(
            axis=locus.name,
            value=value[:300],
            allowed=open_by_node.get(locus.node, []),
            reason="forbidden_axis",
        )
        for locus, value in prompt_edit.items()
        if locus.key not in open_by_node.get(locus.node, [])
    ]


def l1_shots_in_demo_pool(
    shots_by_node: Mapping[str, Sequence[int]], *, demo_ids: Collection[int], k_max: int
) -> list[ValidationFailure]:
    """An id outside the demo pool is a SCORED row pasted into the prompt as a worked answer; a node bearing no prompt is the bench's admission."""
    failures: list[ValidationFailure] = []
    for node, shots in shots_by_node.items():
        axis = Locus(node, SHOTS_KEY).name
        failures += [
            ValidationFailure(axis=axis, value=str(i), allowed=[], reason="shot_not_in_demo_pool")
            for i in shots
            if i not in demo_ids
        ]
        if len(set(shots)) != len(shots):
            failures.append(
                ValidationFailure(
                    axis=axis, value=str(list(shots)), allowed=[], reason="shot_repeated"
                )
            )
        if len(shots) > k_max:
            failures.append(
                ValidationFailure(
                    axis=axis,
                    value=str(len(shots)),
                    allowed=[str(k_max)],
                    reason="shots_over_cap",
                )
            )
    return failures


def l1_config_not_in_runtime_failures(
    overlay: Mapping[str, Any],
    *,
    runtime_failures: Sequence[RuntimeFailure],
    pipeline_params: Mapping[str, Any] | None,
) -> list[ValidationFailure]:
    """A wound convicts a ``(responder, param, value)``: one endpoint's 400 says nothing of the next model's."""
    merged = pipeline_params or {}
    out_failures: list[ValidationFailure] = []
    for node_name, node_params in overlay.items():
        if not isinstance(node_params, dict):
            continue
        inherited = merged.get(node_name)
        effective = dict(inherited) if isinstance(inherited, dict) else {}
        effective.update(node_params)
        for param, value in node_params.items():
            for rf in runtime_failures:
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
    return out_failures


def l1_inner_placeholders_intact(
    pipeline_params: dict[str, Any], *, inner_cells: InnerCells | None
) -> list[ValidationFailure]:
    """Checks the MERGED params: a child inherits token-less prose without re-proposing it."""
    if inner_cells is None:
        return []
    failures: list[ValidationFailure] = []
    for node_name, cfg in node_config_items(pipeline_params):
        prose = {
            k: v
            for k, v in cfg.items()
            if k in OptimizerPromptTemplate.model_fields and isinstance(v, str)
        }
        base = inner_template(inner_cells, node_name) if prose else None
        if base is None:
            continue
        declared = sorted(set(TEMPLATE_TOKEN_RE.findall(base.render())))
        if not declared:
            continue
        merged = base.model_copy(update=prose)
        missing = missing_template_vars(
            merged.render(), declared, fields=OptimizerPromptTemplate.model_fields
        )
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


def l1_inner_steer_is_legal(
    overlay: Mapping[str, Any], *, inner_nodes: Collection[str]
) -> list[ValidationFailure]:
    """A prompt that stops the loop early edits the frame the measurand averages over, not the search
    it measures. Judged on the inner optimizer's nodes alone: elsewhere the phrase is target prose."""
    failures: list[ValidationFailure] = []
    for node_name, node_params in overlay.items():
        if node_name not in inner_nodes or not isinstance(node_params, dict):
            continue
        for field, value in node_params.items():
            if field not in OptimizerPromptTemplate.model_fields or not isinstance(value, str):
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
    return failures


_GUTTABLE_MIN_CHARS = 1000
_GUT_RATIO = 0.35


def _parent_field_text(
    cells: InnerCells, node: str, field: str, pipeline_params: Mapping[str, Any] | None
) -> str:
    parent = (pipeline_params or {}).get(node)
    if isinstance(parent, Mapping):
        inherited = parent.get(field)
        if isinstance(inherited, str) and inherited:
            return inherited
    template = inner_template(cells, node)
    current = getattr(template, field, "") if template is not None else ""
    return current if isinstance(current, str) else ""


def l1_prompt_field_not_gutted(
    overlay: Mapping[str, Any],
    *,
    inner_cells: InnerCells | None,
    pipeline_params: Mapping[str, Any] | None,
) -> list[ValidationFailure]:
    """An override REPLACES its field whole, so a short one deletes the contracts the parent's prose carried."""
    if inner_cells is None:
        return []
    failures: list[ValidationFailure] = []
    for node_name, node_params in overlay.items():
        if not isinstance(node_params, dict):
            continue
        for field, value in node_params.items():
            if field not in OptimizerPromptTemplate.model_fields or not isinstance(value, str):
                continue
            parent = _parent_field_text(inner_cells, node_name, field, pipeline_params)
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
    return failures


def l1_inner_layout_applies(overlay: Mapping[str, Any]) -> list[ValidationFailure]:
    """Convicted on the PROPOSAL: the apply site sits inside the inner task, below every wound channel."""
    failures: list[ValidationFailure] = []
    for node_name, node_params in overlay.items():
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
    return failures
