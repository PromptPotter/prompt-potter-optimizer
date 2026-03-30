"""OAT sensitivity scan — one-at-a-time perturbation scanning.

Evaluates each axis value against the baseline, holding all other axes at
their baseline values.  Returns per-variant results and axis profiles
sorted by sensitivity.
"""
from __future__ import annotations

import copy
import logging
import random
from collections.abc import Callable
from typing import TYPE_CHECKING

from api.models.eval_context import EvalContext
from api.models.opt_search_point import PROMPT_STRING_FIELDS, OptSearchPoint
from api.models.search_point import JobSearchPoint
from api.services.prompt_eval import _most_common_error_category, eval_search_point
from api.services.search.preview import preview as _preview
from api.services.search.smart_search import (
    ScanEvent,
    _profiles_from_rows,
)

if TYPE_CHECKING:
    import pandas as pd

    from api.models.pipeline_schema import PipelineSchema
    from api.services.backend_client import BackendClient
    from api.services.project_store import ProjectStore

logger = logging.getLogger(__name__)


async def sensitivity_scan(
    baseline: JobSearchPoint,
    scan_variants: dict[str, list],
    eval_data: list,
    backend_client: BackendClient,
    *,
    baseline_opt: OptSearchPoint | None = None,
    sample_size: int = 0,
    store: ProjectStore | None = None,
    backend_id: str = "",
    pipeline_schema: PipelineSchema | None = None,
    progress_cb: Callable[[ScanEvent], None] | None = None,
    on_result: Callable | None = None,
    experiment_id: str = "",
) -> tuple[pd.DataFrame, list[dict]]:
    """OAT perturbation scan over all axes.

    Evaluates each axis value one-at-a-time against the baseline, holding
    all other axes at their baseline values. Returns per-variant results
    and an axis profile sorted by sensitivity.

    Args:
        baseline: Baseline JobSearchPoint (pipeline_params).
        scan_variants: Flat dict mapping axis names to value lists.
            Prompt fields (in ``PROMPT_STRING_FIELDS``) and pipeline
            params are auto-detected.
        eval_data: Full evaluation dataset.
        backend_client: Backend client for evaluation.
        baseline_opt: OptSearchPoint for prompt-field perturbation. Required
            when scan_variants contains prompt_field axes.
        sample_size: If >0, subsample eval_data to this many queries
            (deterministic seed=42). 0 means use all.
        store: Optional ProjectStore for caching.
        backend_id: Backend identifier.
        pipeline_schema: Optional PipelineSchema for composite scoring.
        progress_cb: Optional callback for progress events.
        on_result: Optional per-result callback.

    Returns:
        Tuple of (per_variant_df, axis_profiles).
    """
    import pandas as pd

    _cb = progress_cb or (lambda _e: None)

    # Subsample eval_data if requested
    if sample_size > 0 and sample_size < len(eval_data):
        eval_data = random.Random(42).sample(eval_data, sample_size)

    # Build EvalContext once for all scan evaluations
    scan_ctx = EvalContext(
        backend_client=backend_client,
        store=store,
        backend_id=backend_id,
        pipeline_schema=pipeline_schema,
        source="sensitivity_scan",
        experiment_id=experiment_id,
    )

    # Classify axes: prompt_field vs pipeline_param
    axes: list[tuple[str, str, list]] = []
    for name, values in scan_variants.items():
        if len(values) <= 1:
            continue
        axis_type = "prompt_field" if name in PROMPT_STRING_FIELDS else "pipeline_param"
        axes.append((name, axis_type, values))

    # Evaluate baseline
    baseline_results, baseline_scores, baseline_cached = await eval_search_point(
        baseline, eval_data, scan_ctx,
        label="scan",
        on_result=on_result,
    )
    baseline_acc = baseline_scores["accuracy"]
    baseline_composite = baseline_scores.get("composite", baseline_acc)
    _cb({
        "type": "baseline_done",
        "accuracy": baseline_acc,
        "hits": baseline_scores["hits"],
        "total": baseline_scores["total"],
        "results": baseline_results,
        "cached": baseline_cached,
    })

    # Circuit breaker: abort if baseline eval is all-errors
    baseline_errors = baseline_scores.get("errors", 0)
    if baseline_errors == baseline_scores["total"] > 0:
        dominant = _most_common_error_category(baseline_results)
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

    for ai, (axis_name, axis_type, values) in enumerate(axes):
        _cb({
            "type": "axis_start",
            "axis": axis_name, "axis_type": axis_type,
            "cardinality": len(values),
            "axis_index": ai, "total_axes": len(axes),
        })

        for vi, value in enumerate(values):
            # Detect baseline value for this axis
            if axis_type == "prompt_field":
                current_val = getattr(baseline_opt, axis_name, "") if baseline_opt else ""
            else:
                current_val = (baseline.pipeline_params or {}).get(axis_name)

            if value == current_val:
                rows.append({
                    "axis": axis_name, "axis_type": axis_type,
                    "value_idx": vi,
                    "value_preview": _preview(value),
                    "hits": baseline_scores["hits"],
                    "total": baseline_scores["total"],
                    "accuracy": baseline_acc, "delta": 0.0,
                    "errors": baseline_errors,
                })
                _cb({
                    "type": "variant_done",
                    "axis": axis_name, "value_idx": vi,
                    "value_preview": _preview(value),
                    "is_baseline_value": True,
                    "accuracy": baseline_acc, "delta": 0.0,
                    "hits": baseline_scores["hits"],
                    "total": baseline_scores["total"],
                    "results": [], "cached": False,
                })
                continue

            # Derive perturbed JobSearchPoint
            if axis_type == "prompt_field":
                assert baseline_opt is not None, (
                    "baseline_opt required for prompt_field perturbation"
                )
                perturbed = baseline_opt.derive_candidate(
                    **{axis_name: value},
                ).to_job_search_point(
                    base_pipeline_params=baseline.pipeline_params,
                )
            else:
                # Resolve flat param name → nested node dict
                pp = copy.deepcopy(baseline.pipeline_params or {})
                if pipeline_schema:
                    resolved = pipeline_schema.resolve_flat_param(axis_name)
                    if resolved:
                        node, wire_key = resolved
                        pp.setdefault(node, {})[wire_key] = value
                    else:
                        pp[axis_name] = value
                else:
                    pp[axis_name] = value
                perturbed = baseline.derive(pipeline_params=pp)

            results, scores, cached = await eval_search_point(
                perturbed, eval_data, scan_ctx,
                label="scan",
                on_result=on_result,
            )

            acc = scores["accuracy"]
            composite = scores.get("composite", acc)
            delta = composite - baseline_composite
            variant_errors = scores.get("errors", 0)
            rows.append({
                "axis": axis_name, "axis_type": axis_type,
                "value_idx": vi,
                "value_preview": _preview(value),
                "hits": scores["hits"],
                "total": scores["total"],
                "accuracy": acc, "delta": delta,
                "composite": composite,
                "errors": variant_errors,
            })
            _cb({
                "type": "variant_done",
                "axis": axis_name, "value_idx": vi,
                "value_preview": _preview(value),
                "is_baseline_value": False,
                "accuracy": acc, "delta": delta,
                "composite": composite,
                "hits": scores["hits"], "total": scores["total"],
                "results": results,
                "cached": cached,
            })

            # Circuit breaker: track consecutive non-cached all-error evals
            if not cached and variant_errors == scores["total"] > 0:
                _consecutive_all_error += 1
                if _consecutive_all_error >= 2:
                    dominant = _most_common_error_category(results)
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
                    profiles = _profiles_from_rows(rows, axes, len(eval_data))
                    for profile in profiles:
                        _cb({"type": "axis_done", **profile})
                    _cb({"type": "scan_aborted", "reason": reason})
                    return pd.DataFrame(rows), profiles
            else:
                _consecutive_all_error = 0

    # Build axis profiles
    profiles = _profiles_from_rows(rows, axes, len(eval_data))
    for profile in profiles:
        _cb({"type": "axis_done", **profile})

    return pd.DataFrame(rows), profiles
