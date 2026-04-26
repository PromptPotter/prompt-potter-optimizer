"""DORMANT — do not maintain. See ./README.md.

OAT sensitivity scan — one-at-a-time perturbation scanning.
Evaluates each axis value against the baseline, holding all other axes at
their baseline values. Returns per-variant results and axis profiles
sorted by sensitivity.
"""

from __future__ import annotations

import copy
import logging
import random
from collections.abc import Callable
from typing import TYPE_CHECKING

from promptpotter.application.campaign.campaign_setup import Session
from promptpotter.application.recon.adaptive_recon import (
    ReconEvent,
    build_axis_profiles,
)
from promptpotter.application.recon.failure_groups import preview as _preview
from promptpotter.application.scoring.search_point_scorer import score_search_point
from promptpotter.domain.opt_search_point import OptSearchPoint
from promptpotter.domain.search_point import JobSearchPoint
from promptpotter.shared.constants import PROMPT_STRING_FIELDS
from promptpotter.shared.errors import most_common_error_category
from promptpotter.shared.statistics import wilson_ci

if TYPE_CHECKING:
    import pandas as pd

    from promptpotter.domain.pipeline_schema import PipelineSchema

logger = logging.getLogger(__name__)


async def run_recon(
    baseline: JobSearchPoint,
    recon_variants: dict[str, list | dict],
    dataset: list,
    session: Session,
    *,
    baseline_opt: OptSearchPoint | None = None,
    sample_size: int = 0,
    pipeline_schema: PipelineSchema | None = None,
    progress_cb: Callable[[ReconEvent], None] | None = None,
    on_result: Callable | None = None,
    experiment_id: str = "",
    pruning_enabled: bool = True,
    scoring_formula: str | None = None,
) -> tuple[pd.DataFrame, list[dict]]:
    """OAT perturbation scan over all axes.

    Evaluates each axis value one-at-a-time against the baseline, holding
    all other axes at their baseline values. Returns per-variant results
    and an axis profile sorted by sensitivity.

    Args:
        baseline: Baseline JobSearchPoint (pipeline_params).
        recon_variants: Dict mapping axes to value lists.  Two formats:
            - Prompt fields: ``{"thinking_style": ["a", "b"]}`` (list → prompt)
            - Pipeline params: ``{"web_search": {"max_sites": [3, 5]}}`` (dict → node)
        dataset: Full evaluation dataset.
        session: Session bundling backend_client, store, backend_id.
        baseline_opt: OptSearchPoint for prompt-field perturbation. Required
            when recon_variants contains prompt_field axes.
        sample_size: If >0, subsample dataset to this many queries
            (deterministic seed=42). 0 means use all.
        pipeline_schema: Override PipelineSchema (defaults to session's).
        progress_cb: Optional callback for progress events.
        on_result: Optional per-result callback.

    Returns:
        Tuple of (per_variant_df, axis_profiles).
    """
    import pandas as pd

    # Unpack session
    pipeline_schema = pipeline_schema or session.pipeline_schema

    _cb = progress_cb or (lambda _e: None)

    # Subsample dataset if requested
    if sample_size > 0 and sample_size < len(dataset):
        dataset = random.Random(42).sample(dataset, sample_size)

    # Populate the session's scoring block for this recon pass.
    from promptpotter.shared.scoring import compile_scorer

    scan_session = session
    scan_session.pipeline_schema = pipeline_schema
    scan_session.source = "run_recon"
    scan_session.experiment_id = experiment_id
    scan_session.scorer = compile_scorer(scoring_formula)

    # Resolve active prompt node — only if the prompt node is in active steps
    _prompt_node = ""
    _has_prompt_node = False
    if pipeline_schema:
        for pn in pipeline_schema.prompt_node_names():
            if pipeline_schema.has_node(pn):
                _prompt_node = pn
                _has_prompt_node = True
                break
    else:
        # No schema — infer prompt node from baseline pipeline_params
        for node_name, node_cfg in (baseline.pipeline_params or {}).items():
            if isinstance(node_cfg, dict) and "prompt" in node_cfg:
                _prompt_node = node_name
                _has_prompt_node = True
                break

    # Classify axes: prompt_field vs pipeline_param
    # Axis tuple: (param_name, axis_type, values, node_name | None)
    axes: list[tuple[str, str, list, str | None]] = []
    for key, spec in recon_variants.items():
        if key in PROMPT_STRING_FIELDS and isinstance(spec, list):
            if len(spec) <= 1:
                continue
            if not _has_prompt_node:
                logger.debug("Skipping prompt_field axis %r: no active prompt node", key)
                continue
            axes.append((key, "prompt_field", spec, None))
        elif isinstance(spec, dict):
            # Node param group — expand per-param
            for param, values in spec.items():
                if not isinstance(values, list) or len(values) <= 1:
                    continue
                axes.append((param, "pipeline_param", values, key))
        elif isinstance(spec, list):
            # Bare list but not a prompt field — skip with warning
            logger.warning(
                "recon_variants: bare list for %r is not a prompt field; "
                'nest under node name: {"node": {"%s": [...]}}',
                key,
                key,
            )

    # Sort axes by pipeline node order (prompt fields under their owning node)
    if pipeline_schema:
        _prompt_nodes = pipeline_schema.prompt_node_names()
        _n_nodes = len(pipeline_schema.nodes)
        _prompt_pos = (
            pipeline_schema.node_position(_prompt_nodes[0])
            if _prompt_nodes and pipeline_schema.has_node(_prompt_nodes[0])
            else _n_nodes
        )

        def _axis_sort_key(axis: tuple[str, str, list, str | None]) -> int:
            _name, axis_type, _, node_name = axis
            if axis_type == "prompt_field":
                return _prompt_pos
            if node_name is None:
                return _n_nodes
            try:
                return pipeline_schema.node_position(node_name)
            except KeyError:
                return _n_nodes

        axes.sort(key=_axis_sort_key)

    # Evaluate baseline
    baseline_results, baseline_scores, baseline_cached, _ = await score_search_point(
        baseline,
        dataset,
        scan_session,
        label="scan",
        on_result=on_result,
    )
    baseline_acc = baseline_scores["accuracy"]
    baseline_composite = baseline_scores.get("composite", baseline_acc)
    _cb(
        {
            "type": "baseline_done",
            "accuracy": baseline_acc,
            "hits": baseline_scores["hits"],
            "total": baseline_scores["total"],
            "results": baseline_results,
            "cached": baseline_cached,
        }
    )

    # Circuit breaker: abort if baseline eval is all-errors
    baseline_errors = baseline_scores.get("errors", 0)
    if baseline_errors == baseline_scores["total"] > 0:
        dominant = most_common_error_category(baseline_results)
        if dominant == "CLIENT":
            reason = (
                f"Baseline eval failed: all {baseline_scores['total']} queries "
                "returned client errors (HTTP 4xx). "
                "Check pipeline configuration and request parameters."
            )
        elif dominant == "CONNECTION":
            reason = (
                f"Baseline eval failed: all {baseline_scores['total']} queries "
                "failed to connect. Backend may be down or unreachable."
            )
        else:
            reason = (
                f"Baseline eval failed: all {baseline_scores['total']} queries "
                "errored. Backend may be experiencing issues."
            )
        logger.error("Aborting scan: %s", reason)
        _cb({"type": "scan_aborted", "reason": reason})
        return pd.DataFrame(), []

    rows: list[dict] = []
    _consecutive_all_error = 0

    # Baseline CI for early pruning (Wave 2b)
    _baseline_ci: tuple[float, float] | None = None
    if pruning_enabled:
        _baseline_ci = wilson_ci(baseline_scores["hits"], baseline_scores["total"])

    _pruned_axes: set[str] = set()

    for ai, (axis_name, axis_type, values, axis_node) in enumerate(axes):
        _cb(
            {
                "type": "axis_start",
                "axis": axis_name,
                "axis_type": axis_type,
                "cardinality": len(values),
                "axis_index": ai,
                "total_axes": len(axes),
            }
        )

        # Per-axis pruning state (Wave 2b)
        _axis_variant_cis: list[tuple[float, float]] = []
        _axis_pruned = False

        for vi, value in enumerate(values):
            # Detect baseline value for this axis
            if axis_type == "prompt_field":
                current_val = getattr(baseline_opt, axis_name, "") if baseline_opt else ""
            elif axis_node:
                current_val = (baseline.pipeline_params or {}).get(axis_node, {}).get(axis_name)
            else:
                current_val = None

            if value == current_val:
                _bl_pq_hits = {
                    r.get("query", ""): bool(r.get("hit"))
                    for r in baseline_results
                    if r.get("query")
                }
                rows.append(
                    {
                        "axis": axis_name,
                        "axis_type": axis_type,
                        "value_idx": vi,
                        "value_preview": _preview(value),
                        "hits": baseline_scores["hits"],
                        "total": baseline_scores["total"],
                        "accuracy": baseline_acc,
                        "delta": 0.0,
                        "errors": baseline_errors,
                        "per_query_hits": _bl_pq_hits,
                    }
                )
                _cb(
                    {
                        "type": "variant_done",
                        "axis": axis_name,
                        "value_idx": vi,
                        "value_preview": _preview(value),
                        "is_baseline_value": True,
                        "accuracy": baseline_acc,
                        "delta": 0.0,
                        "hits": baseline_scores["hits"],
                        "total": baseline_scores["total"],
                        "results": [],
                        "cached": False,
                    }
                )
                continue

            # Derive perturbed JobSearchPoint
            if axis_type == "prompt_field":
                assert baseline_opt is not None, (
                    "baseline_opt required for prompt_field perturbation"
                )
                perturbed = baseline_opt.mutate(
                    **{axis_name: value},
                ).to_job_search_point(
                    base_pipeline_params=baseline.pipeline_params,
                    schema=pipeline_schema,
                )
            else:
                pp = copy.deepcopy(baseline.pipeline_params or {})
                if axis_node:
                    pp.setdefault(axis_node, {})[axis_name] = value
                perturbed = baseline.derive(pipeline_params=pp)

            results, scores, cached, _ = await score_search_point(
                perturbed,
                dataset,
                scan_session,
                label="scan",
                on_result=on_result,
            )

            acc = scores["accuracy"]
            composite = scores.get("composite", acc)
            delta = composite - baseline_composite
            variant_errors = scores.get("errors", 0)
            # Per-query hit map for failure group sensitivity (Wave 3e)
            _pq_hits = {r.get("query", ""): bool(r.get("hit")) for r in results if r.get("query")}
            rows.append(
                {
                    "axis": axis_name,
                    "axis_type": axis_type,
                    "value_idx": vi,
                    "value_preview": _preview(value),
                    "hits": scores["hits"],
                    "total": scores["total"],
                    "accuracy": acc,
                    "delta": delta,
                    "composite": composite,
                    "errors": variant_errors,
                    "per_query_hits": _pq_hits,
                }
            )
            _cb(
                {
                    "type": "variant_done",
                    "axis": axis_name,
                    "value_idx": vi,
                    "value_preview": _preview(value),
                    "is_baseline_value": False,
                    "accuracy": acc,
                    "delta": delta,
                    "composite": composite,
                    "hits": scores["hits"],
                    "total": scores["total"],
                    "results": results,
                    "cached": cached,
                }
            )

            # Per-axis early pruning (Wave 2b)
            if _baseline_ci is not None and not _axis_pruned:
                var_ci = wilson_ci(scores["hits"], scores["total"])
                _axis_variant_cis.append(var_ci)
                # Check if ALL variant CIs overlap with baseline
                if len(_axis_variant_cis) >= 2:
                    all_overlap = all(
                        vc[0] <= _baseline_ci[1] and _baseline_ci[0] <= vc[1]
                        for vc in _axis_variant_cis
                    )
                    if all_overlap:
                        _axis_pruned = True
                        remaining = len(values) - vi - 1
                        if remaining > 0:
                            _pruned_axes.add(axis_name)
                            logger.info(
                                "Pruning axis '%s': all %d variants overlap "
                                "with baseline CI — skipping %d remaining values",
                                axis_name,
                                len(_axis_variant_cis),
                                remaining,
                            )
                            _cb(
                                {
                                    "type": "axis_pruned",
                                    "axis": axis_name,
                                    "variants_tested": len(_axis_variant_cis),
                                    "remaining_skipped": remaining,
                                }
                            )
                            break

            # Circuit breaker: track consecutive non-cached all-error evals
            if not cached and variant_errors == scores["total"] > 0:
                _consecutive_all_error += 1
                if _consecutive_all_error >= 2:
                    dominant = most_common_error_category(results)
                    if dominant == "CLIENT":
                        detail = "Client errors (HTTP 4xx). Check pipeline configuration."
                    elif dominant == "CONNECTION":
                        detail = "Backend may be down or unreachable."
                    else:
                        detail = "Backend may be experiencing issues."
                    reason = (
                        f"Aborting scan: {_consecutive_all_error} consecutive "
                        f"variant evals returned all errors. {detail}"
                    )
                    logger.error(reason)
                    profiles = build_axis_profiles(rows, axes, len(dataset))
                    for profile in profiles:
                        _cb({"type": "axis_done", **profile})
                    _cb({"type": "scan_aborted", "reason": reason})
                    return pd.DataFrame(rows), profiles
            else:
                _consecutive_all_error = 0

    # Build axis profiles and annotate pruned axes (Wave 2b)
    profiles = build_axis_profiles(rows, axes, len(dataset))
    for profile in profiles:
        profile["pruned"] = profile["axis"] in _pruned_axes
        _cb({"type": "axis_done", **profile})

    return pd.DataFrame(rows), profiles
