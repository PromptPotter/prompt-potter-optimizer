"""Sensitivity scan, adaptive search, diagnostic set, and axis classification.

One-at-a-time (OAT) perturbation scanning and importance-weighted
coordinate descent over prompt-field and pipeline-param axes.
"""

from __future__ import annotations

import logging
import random
from collections import defaultdict
from typing import TYPE_CHECKING, Callable, Literal, TypedDict

if TYPE_CHECKING:
    import pandas as pd

from api.config.settings import DEFAULT_DIAGNOSTIC_QUERIES
from api.models.opt_search_point import OptSearchPoint, PROMPT_STRING_FIELDS
from api.models.search_point import JobSearchPoint
from api.services.project_store import ProjectStore
from api.services.prompt_eval import EvalContext, evaluate_prompt_cached, _dominant_error_category
from api.services.search.coverage import preview as _preview

if TYPE_CHECKING:
    from api.models.pipeline_schema import PipelineSchema
    from api.services.backend_client import BackendClient

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Progress event types
# ---------------------------------------------------------------------------


class ScanEvent(TypedDict, total=False):
    """Progress event emitted by ``sensitivity_scan()`` and ``adaptive_search()``.

    All events carry ``type``.  Other fields depend on the event type:

    - ``baseline_done``: accuracy, hits, total, results, cached
    - ``axis_start``: axis, axis_type, cardinality, axis_index, total_axes,
      round (adaptive only), budget (adaptive only)
    - ``variant_done``: axis, value_idx, value_preview, is_baseline_value,
      accuracy, delta, hits, total, results, cached, round (adaptive only)
    - ``axis_done``: axis, axis_type, cardinality, sensitivity_range,
      best_delta, worst_delta, exploration_budget, estimated_eval_cost
    - ``axis_resolved``: round, axis, action, best_value, improvement,
      new_accuracy (adaptive only)
    - ``round_start``: round, max_rounds, current_accuracy, active_axes
      (adaptive only)
    - ``round_done``: round, improved, accuracy (adaptive only)
    """

    type: Literal[
        "baseline_done",
        "axis_start",
        "variant_done",
        "axis_done",
        "axis_resolved",
        "round_start",
        "round_done",
        "scan_aborted",
    ]
    # common
    axis: str
    axis_type: str
    accuracy: float
    delta: float
    hits: int
    total: int
    cached: bool
    results: list
    reason: str
    # axis_start
    cardinality: int
    axis_index: int
    total_axes: int
    budget: str
    # variant_done
    value_idx: int
    value_preview: str
    is_baseline_value: bool
    # axis_resolved
    action: str
    best_value: str
    improvement: float
    new_accuracy: float
    # round events
    round: int
    max_rounds: int
    current_accuracy: float
    active_axes: list[str]
    improved: bool
    # axis_done profile fields
    sensitivity_range: float
    best_delta: float
    worst_delta: float
    exploration_budget: str
    estimated_eval_cost: int


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

MIN_DIAGNOSTIC_QUERIES = 3
DIAGNOSTIC_HIT_RATIO = 0.75


def _profiles_from_rows(
    rows: list[dict],
    axes: list[tuple[str, str, list]],
    n_eval: int,
) -> list[dict]:
    """Build axis profiles from scan rows.

    Args:
        rows: Per-variant result dicts with ``axis``, ``delta`` keys.
        axes: List of ``(axis_name, axis_type, values)`` tuples.
        n_eval: Number of diagnostic queries (for noise threshold).

    Returns:
        Axis profiles sorted by sensitivity_range (descending).
    """
    axis_deltas: dict[str, list[float]] = defaultdict(list)
    for row in rows:
        axis_deltas[row["axis"]].append(row["delta"])

    profiles: list[dict] = []
    for axis_name, axis_type, values in axes:
        deltas = axis_deltas.get(axis_name, [0.0])
        sens_range = max(deltas) - min(deltas) if deltas else 0.0
        card = len(values)
        budget = classify_axis(card, sens_range, n_eval)
        profiles.append({
            "axis": axis_name,
            "axis_type": axis_type,
            "cardinality": card,
            "sensitivity_range": round(sens_range, 4),
            "best_delta": round(max(deltas), 4) if deltas else 0.0,
            "worst_delta": round(min(deltas), 4) if deltas else 0.0,
            "exploration_budget": budget,
            "estimated_eval_cost": card * n_eval,
        })

    profiles.sort(key=lambda p: -p["sensitivity_range"])
    return profiles


def _make_eval_fn(
    eval_data: list,
    ctx: "EvalContext",
    get_params: Callable[[], dict],
    on_result: Callable | None = None,
) -> Callable:
    """Factory for the ``_eval_opt`` closure used by scan and adaptive search."""

    async def _eval_opt(opt: OptSearchPoint, pp: dict | None = None) -> dict:
        sp = opt.to_job_search_point(
            model=ctx.model,
            temperature=ctx.temperature,
            base_pipeline_params=pp or get_params(),
        )
        results, scores, cached = await evaluate_prompt_cached(
            sp, eval_data, ctx,
            label="scan",
            on_result=on_result,
        )
        return {**scores, "results": results, "cached": cached}
    return _eval_opt


# ---------------------------------------------------------------------------
# Diagnostic set builder
# ---------------------------------------------------------------------------


def build_diagnostic_set(
    eval_data: list,
    baseline_results: list,
    n_queries: int = DEFAULT_DIAGNOSTIC_QUERIES,
    seed: int = 42,
) -> tuple[list, dict]:
    """Stratified query set: ~75% baseline hits (regression guard) + ~25% misses.

    Args:
        eval_data: Full evaluation dataset (list of query dicts).
        baseline_results: Results from baseline evaluation (list of result dicts
            with ``hit`` and ``query`` keys).
        n_queries: Number of queries in the diagnostic set.
        seed: Random seed for reproducible sampling.

    Returns:
        Tuple of (diagnostic_queries, summary_dict).

    Raises:
        ValueError: If fewer than ``MIN_DIAGNOSTIC_QUERIES`` queries available.
    """
    if len(eval_data) < MIN_DIAGNOSTIC_QUERIES:
        raise ValueError(
            f"Need at least {MIN_DIAGNOSTIC_QUERIES} eval queries, "
            f"got {len(eval_data)}."
        )

    # Map queries to eval_data items
    query_to_eval = {d["query"]: d for d in eval_data}

    hits = []
    misses = []
    for r in baseline_results:
        q = r.get("query", "")
        if q not in query_to_eval:
            continue
        if r.get("hit"):
            hits.append(query_to_eval[q])
        else:
            misses.append(query_to_eval[q])

    rng = random.Random(seed)

    # Fallback: no baseline results — sample randomly from eval_data
    if not hits and not misses:
        n = min(n_queries, len(eval_data))
        diagnostic = rng.sample(eval_data, n)
        summary = {
            "n_queries": len(diagnostic),
            "n_hits": 0,
            "n_misses": 0,
            "total_pool_hits": 0,
            "total_pool_misses": 0,
            "stratified": False,
        }
        return diagnostic, summary
    n_queries = min(n_queries, len(hits) + len(misses))

    n_hits = max(1, round(n_queries * DIAGNOSTIC_HIT_RATIO))
    n_misses = n_queries - n_hits

    # Clamp to pool sizes
    if n_hits > len(hits):
        n_hits = len(hits)
        n_misses = min(n_queries - n_hits, len(misses))
    if n_misses > len(misses):
        n_misses = len(misses)
        n_hits = min(n_queries - n_misses, len(hits))

    selected_hits = rng.sample(hits, min(n_hits, len(hits)))
    selected_misses = rng.sample(misses, min(n_misses, len(misses)))

    diagnostic = selected_hits + selected_misses
    rng.shuffle(diagnostic)

    summary = {
        "n_queries": len(diagnostic),
        "n_hits": len(selected_hits),
        "n_misses": len(selected_misses),
        "total_pool_hits": len(hits),
        "total_pool_misses": len(misses),
    }

    return diagnostic, summary


# ---------------------------------------------------------------------------
# Axis classification
# ---------------------------------------------------------------------------


def classify_axis(
    cardinality: int,
    sensitivity_range: float,
    n_diagnostic: int = DEFAULT_DIAGNOSTIC_QUERIES,
) -> str:
    """Classify an axis into an exploration budget tier.

    Args:
        cardinality: Number of discrete values for this axis.
        sensitivity_range: ``max(delta) - min(delta)`` from sensitivity scan.
        n_diagnostic: Number of diagnostic queries (for noise threshold).

    Returns:
        One of ``"skip"``, ``"low"``, ``"medium"``, ``"high"``.
    """
    noise_threshold = 1.0 / n_diagnostic if n_diagnostic > 0 else 0.0

    if sensitivity_range < noise_threshold:
        return "skip"
    if cardinality == 2:
        return "low"
    if cardinality <= 5 and sensitivity_range <= 0.3:
        return "medium"
    return "high"



# ---------------------------------------------------------------------------
# Variant library filtering
# ---------------------------------------------------------------------------


def filter_variant_library(
    variant_library: dict,
    pipeline_params: dict | None,
    schema: "PipelineSchema",
) -> dict:
    """Filter variant library to axes relevant for the active pipeline.

    - Keeps only ``pipeline_params`` axes whose owning step is active.
    - Drops all ``prompt_fields`` when ``llm_ranking`` is not active
      (``OptSearchPoint.render()`` produces the prompt consumed only
      by that step).

    Args:
        variant_library: Full variant library dict with ``prompt_fields``
            and optional ``pipeline_params`` sections.
        pipeline_params: Current pipeline parameters (must contain ``steps``
            key listing active step names).
        schema: PipelineSchema for step-param lookup.

    Returns:
        Filtered copy of the variant library.
    """
    active_steps = set((pipeline_params or {}).get("steps", []))

    # Build set of param keys owned by active steps
    step_param_map = schema.step_param_keys()
    active_param_keys: set[str] = set()
    for step_name, param_keys in step_param_map.items():
        if step_name in active_steps:
            active_param_keys |= param_keys

    # Filter pipeline_params to only active-step keys
    filtered_pp: dict[str, list] = {}
    for axis, values in variant_library.get("pipeline_params", {}).items():
        if axis in active_param_keys:
            filtered_pp[axis] = values

    # Drop prompt_fields when llm_ranking is not active
    filtered_pf = variant_library.get("prompt_fields", {})
    if "llm_ranking" not in active_steps:
        filtered_pf = {}

    result: dict = {}
    if filtered_pf:
        result["prompt_fields"] = filtered_pf
    if filtered_pp:
        result["pipeline_params"] = filtered_pp

    removed_pp = set(variant_library.get("pipeline_params", {})) - set(filtered_pp)
    if removed_pp:
        logger.info("filter_variant_library: dropped pipeline_params %s", removed_pp)
    if not filtered_pf and variant_library.get("prompt_fields"):
        logger.info(
            "filter_variant_library: dropped all prompt_fields "
            "(llm_ranking not active)"
        )

    return result


def load_filtered_variant_library(
    pipeline_params: dict | None = None,
    pipeline_schema: "PipelineSchema | None" = None,
) -> dict:
    """Load variant library, filtering to active pipeline steps when possible."""
    from api.config.settings import load_variant_library
    lib = load_variant_library()
    if pipeline_params and pipeline_schema:
        lib = filter_variant_library(lib, pipeline_params, schema=pipeline_schema)
    return lib


# ---------------------------------------------------------------------------
# Sensitivity scan
# ---------------------------------------------------------------------------


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
    pipeline_schema: "PipelineSchema | None" = None,
    progress_cb: Callable[[ScanEvent], None] | None = None,
    on_result: Callable | None = None,
    experiment_id: str = "",
) -> tuple[pd.DataFrame, list[dict]]:
    """OAT perturbation scan over all axes.

    Evaluates each axis value one-at-a-time against the baseline, holding
    all other axes at their baseline values. Returns per-variant results
    and an axis profile sorted by sensitivity.

    Args:
        baseline: Baseline JobSearchPoint (model + temperature + pipeline_params).
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
        model=baseline.model,
        temperature=baseline.temperature,
        pipeline_params=baseline.pipeline_params,
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
    baseline_results, baseline_scores, baseline_cached = await evaluate_prompt_cached(
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
        dominant = _dominant_error_category(baseline_results)
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
                    model=baseline.model,
                    temperature=baseline.temperature,
                    base_pipeline_params=baseline.pipeline_params,
                )
            else:
                perturbed = baseline.derive(
                    pipeline_params={**(baseline.pipeline_params or {}), axis_name: value},
                )

            results, scores, cached = await evaluate_prompt_cached(
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
                    dominant = _dominant_error_category(results)
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


# ---------------------------------------------------------------------------
# Select best from scan (greedy composition, no backend calls)
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# Adaptive search (importance-weighted coordinate descent)
# ---------------------------------------------------------------------------


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
    pipeline_schema: "PipelineSchema | None" = None,
    experiment_id: str = "",
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
        pipeline_params=pipeline_params,
        experiment_id=experiment_id,
    )
    _eval_opt = _make_eval_fn(
        eval_data, _scan_ctx,
        get_params=lambda: current_params,
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


