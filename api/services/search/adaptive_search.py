"""Adaptive search — importance-weighted coordinate descent.

Iterates over active axes sorted by sensitivity, trying all variant values
for each.  Axes are resolved when they produce no improvement.
"""
from __future__ import annotations

import logging
from collections.abc import Callable
from typing import TYPE_CHECKING

from api.models.eval_context import EvalContext
from api.models.opt_search_point import OptSearchPoint
from api.services.search.preview import preview as _preview
from api.services.search.smart_search import (
    ScanEvent,
    _make_eval_fn,
    filter_variant_library,
)

if TYPE_CHECKING:
    import pandas as pd

    from api.models.pipeline_schema import PipelineSchema
    from api.services.backend_client import BackendClient
    from api.services.project_store import ProjectStore

logger = logging.getLogger(__name__)


async def adaptive_search(
    baseline_opt: OptSearchPoint,
    variant_library: dict,
    eval_data: list,
    backend_client: BackendClient,
    axis_profiles: list[dict],
    max_rounds: int = 3,
    stop_threshold: float = 0.0,
    store: ProjectStore | None = None,
    backend_id: str = "",
    pipeline_params: dict | None = None,
    session_terms: list | None = None,
    progress_cb: Callable[[ScanEvent], None] | None = None,
    plan_id: str = "",
    pipeline_schema: PipelineSchema | None = None,
    experiment_id: str = "",
    model: str = "",
    temperature: float = 0.0,
) -> tuple[OptSearchPoint, dict, pd.DataFrame]:
    """Coordinate descent with per-axis budget from sensitivity profiles.

    Iterates over active axes (those not classified as ``"skip"``),
    sorted by sensitivity. Each round tries all variant values for each
    active axis, keeping the best. Axes are resolved (removed from
    future rounds) when they produce no improvement.

    Args:
        baseline_opt: Starting OptSearchPoint.
        variant_library: Full variant library dict.
        eval_data: Diagnostic query set.
        backend_client: Backend client for evaluation.
        axis_profiles: From ``sensitivity_scan()``.
        max_rounds: Maximum coordinate descent rounds.
        stop_threshold: Minimum per-round improvement to continue an axis.
        store: Optional ProjectStore for caching.
        backend_id: Backend identifier.
        pipeline_params: Base pipeline parameters.
        session_terms: Optional session terms.
        progress_cb: Optional callback ``(event: dict) -> None`` for
            progress reporting. Event types: ``round_start``,
            ``axis_start``, ``variant_done``, ``axis_resolved``.

    Returns:
        Tuple of (best_opt, best_pipeline_params, search_log_df).
    """
    import pandas as pd

    _cb = progress_cb or (lambda _e: None)

    if session_terms:
        await backend_client.init_session(session_terms)

    # Filter variant library to active pipeline steps
    variant_library = filter_variant_library(
        variant_library, pipeline_params, schema=pipeline_schema,
    )

    # Filter and sort axes by sensitivity
    active_axes = [
        p for p in axis_profiles if p["exploration_budget"] != "skip"
    ]
    active_axes.sort(key=lambda p: -p["sensitivity_range"])

    current_opt = baseline_opt
    current_params = dict(pipeline_params or {})
    resolved_axes: set[str] = set()
    log_rows: list[dict] = []

    _scan_ctx = EvalContext(
        backend_client=backend_client,
        store=store,
        backend_id=backend_id,
        pipeline_schema=pipeline_schema,
        source="adaptive_search",
        experiment_id=experiment_id,
    )
    _eval_opt = _make_eval_fn(
        eval_data, _scan_ctx,
        get_params=lambda: current_params,
        model=model,
        temperature=temperature,
    )

    # Get baseline accuracy
    baseline_scores = await _eval_opt(current_opt)
    current_acc = baseline_scores["accuracy"]
    current_composite = baseline_scores.get("composite", current_acc)

    for round_num in range(1, max_rounds + 1):
        round_improved = False
        _cb({
            "type": "round_start",
            "round": round_num, "max_rounds": max_rounds,
            "current_accuracy": current_composite,
            "active_axes": [
                p["axis"] for p in active_axes
                if p["axis"] not in resolved_axes
            ],
        })

        for profile in active_axes:
            axis_name = profile["axis"]
            axis_type = profile["axis_type"]
            budget = profile["exploration_budget"]

            if axis_name in resolved_axes:
                continue

            # Binary axes resolved after round 1
            if budget == "low" and round_num > 1:
                resolved_axes.add(axis_name)
                continue

            # Get values for this axis
            if axis_type == "prompt_field":
                values = variant_library.get("prompt_fields", {}).get(
                    axis_name, [],
                )
            else:
                values = variant_library.get("pipeline_params", {}).get(
                    axis_name, [],
                )

            _cb({
                "type": "axis_start",
                "round": round_num,
                "axis": axis_name, "axis_type": axis_type,
                "cardinality": len(values), "budget": budget,
            })

            best_value = (
                getattr(current_opt, axis_name, "")
                if axis_type == "prompt_field"
                else current_params.get(axis_name)
            )
            best_composite = current_composite

            for value in values:
                if axis_type == "prompt_field":
                    test_opt = current_opt.derive_candidate(**{axis_name: value})
                    test_params = current_params
                else:
                    test_opt = current_opt
                    test_params = {**current_params, axis_name: value}

                scores = await _eval_opt(test_opt, test_params)

                composite = scores.get("composite", scores["accuracy"])
                delta = composite - current_composite
                log_rows.append({
                    "round": round_num,
                    "axis": axis_name,
                    "axis_type": axis_type,
                    "value_preview": _preview(value),
                    "accuracy": scores["accuracy"],
                    "composite": composite,
                    "delta": delta,
                })
                _cb({
                    "type": "variant_done",
                    "round": round_num,
                    "axis": axis_name, "value_preview": _preview(value),
                    "accuracy": scores["accuracy"], "delta": delta,
                    "composite": composite,
                    "hits": scores["hits"], "total": scores["total"],
                    "results": scores.get("results", []),
                    "cached": scores.get("cached", False),
                })

                if composite > best_composite:
                    best_composite = composite
                    best_value = value

            # Apply best value if improved
            improvement = best_composite - current_composite
            if improvement > stop_threshold:
                if axis_type == "prompt_field":
                    current_opt = current_opt.derive_candidate(
                        **{axis_name: best_value},
                        changes_description=(
                            f"adaptive_r{round_num}_{axis_name}"
                        ),
                    )
                else:
                    current_params[axis_name] = best_value
                current_composite = best_composite
                round_improved = True
                _cb({
                    "type": "axis_resolved",
                    "round": round_num,
                    "axis": axis_name, "action": "improved",
                    "best_value": _preview(best_value),
                    "improvement": round(improvement, 4),
                    "new_accuracy": current_composite,
                })
            else:
                resolved_axes.add(axis_name)
                _cb({
                    "type": "axis_resolved",
                    "round": round_num,
                    "axis": axis_name, "action": "skipped",
                    "improvement": round(improvement, 4),
                })

        if not round_improved:
            logger.info(
                "Adaptive search: no improvement in round %d, stopping.",
                round_num,
            )
            _cb({
                "type": "round_done",
                "round": round_num, "improved": False,
                "accuracy": current_composite,
            })
            break
        _cb({
            "type": "round_done",
            "round": round_num, "improved": True,
            "accuracy": current_composite,
        })

    log_df = pd.DataFrame(log_rows)

    # Persist search results to plan
    if store and backend_id and plan_id:
        store.smart_search.update(backend_id, plan_id, {
            "status": "search_complete",
            "search_results": {
                "best_opt": current_opt.model_dump(),
                "best_params": current_params,
                "log_rows": log_df.to_dict(orient="records")
                if not log_df.empty else [],
            },
        })
        logger.info("Saved search results to plan: %s", plan_id)

    return current_opt, current_params, log_df
