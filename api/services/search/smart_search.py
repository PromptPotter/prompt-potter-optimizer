"""Sensitivity scan, adaptive search, diagnostic set, and axis classification.

One-at-a-time (OAT) perturbation scanning and importance-weighted
coordinate descent over prompt-field and pipeline-param axes.
"""

from __future__ import annotations

import logging
import random
from collections import defaultdict
from typing import TYPE_CHECKING, Any, Callable, Literal, TypedDict

import pandas as pd

from api.models.prompt_state import PromptState
from api.services.project_store import ProjectStore
from api.services.prompt_eval import evaluate_prompt_cached
from api.services.search.utils import preview as _preview

if TYPE_CHECKING:
    from api.models.pipeline_schema import PipelineSchema

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
DEFAULT_DIAGNOSTIC_QUERIES = 6
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
    backend_client: Any,
    store: ProjectStore | None,
    backend_id: str,
    get_params: Callable[[], dict],
    prompt_result_index: dict | None = None,
    pipeline_schema: "PipelineSchema | None" = None,
    on_result: Callable | None = None,
) -> Callable:
    """Factory for the ``_eval_ps`` closure used by scan and adaptive search."""
    from api.models.search_point import SearchPoint

    async def _eval_ps(ps: PromptState, pp: dict | None = None) -> dict:
        sp = SearchPoint(
            prompt_state=ps,
            pipeline_params=pp or get_params(),
        )
        results, scores, cached = await evaluate_prompt_cached(
            sp, eval_data, backend_client,
            store=store, backend_id=backend_id,
            label="scan",
            prompt_result_index=prompt_result_index,
            source="sensitivity_scan",
            pipeline_schema=pipeline_schema,
            on_result=on_result,
        )
        return {**scores, "results": results, "cached": cached}
    return _eval_ps


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
      (``PromptState.render()`` produces ``ranking_prompt`` consumed only
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


# ---------------------------------------------------------------------------
# Sensitivity scan
# ---------------------------------------------------------------------------


async def sensitivity_scan(
    baseline_ps: PromptState,
    variant_library: dict,
    eval_data: list,
    backend_client: Any,
    user_focus: str = "",
    store: ProjectStore | None = None,
    backend_id: str = "",
    pipeline_params: dict | None = None,
    session_terms: list | None = None,
    progress_cb: Callable[[ScanEvent], None] | None = None,
    prompt_result_index: dict | None = None,
    plan_id: str = "",
    partial_scan: dict | None = None,
    pipeline_schema: "PipelineSchema | None" = None,
    on_result: Callable | None = None,
) -> tuple[pd.DataFrame, list[dict]]:
    """OAT perturbation scan over all axes.

    Evaluates each axis value one-at-a-time against the baseline, holding
    all other axes at their baseline values. Returns per-variant results
    and an axis profile sorted by sensitivity.

    Args:
        baseline_ps: Baseline PromptState.
        variant_library: Full library dict with ``prompt_fields`` and
            ``pipeline_params`` sections.
        eval_data: Diagnostic query set.
        backend_client: Backend client for evaluation.
        user_focus: Optional hint string (e.g. "entity profiling").
        store: Optional ProjectStore for caching.
        backend_id: Backend identifier.
        pipeline_params: Base pipeline parameters.
        session_terms: Optional session terms for backend init.
        progress_cb: Optional callback ``(event: dict) -> None`` for
            progress reporting. Event types: ``baseline_done``,
            ``axis_start``, ``variant_done``, ``axis_done``.
        partial_scan: Optional dict with ``rows`` and ``completed_axes``
            from a previously interrupted scan. Completed axes are
            skipped and their rows are included in the output.

    Returns:
        Tuple of (per_variant_df, axis_profiles).
    """
    _cb = progress_cb or (lambda _e: None)

    if session_terms:
        await backend_client.init_session(session_terms)

    base_params = dict(pipeline_params or {})

    # Filter axes to only those relevant for the active pipeline
    if pipeline_schema is not None:
        variant_library = filter_variant_library(
            variant_library, pipeline_params, schema=pipeline_schema,
        )

    _eval_ps = _make_eval_fn(
        eval_data, backend_client, store, backend_id,
        get_params=lambda: base_params,
        prompt_result_index=prompt_result_index,
        pipeline_schema=pipeline_schema,
        on_result=on_result,
    )

    # Evaluate baseline
    baseline_scores = await _eval_ps(baseline_ps)
    baseline_acc = baseline_scores["accuracy"]
    _cb({
        "type": "baseline_done",
        "accuracy": baseline_acc,
        "hits": baseline_scores["hits"],
        "total": baseline_scores["total"],
        "results": baseline_scores.get("results", []),
        "cached": baseline_scores.get("cached", False),
    })

    # Collect all axes (prompt_fields + pipeline_params)
    axes: list[tuple[str, str, list]] = []  # (axis_name, axis_type, values)
    for name, values in variant_library.get("prompt_fields", {}).items():
        if len(values) > 1:
            axes.append((name, "prompt_field", values))
    for name, values in variant_library.get("pipeline_params", {}).items():
        if len(values) > 1:
            axes.append((name, "pipeline_param", values))

    # Priority sort: user_focus axes first
    if user_focus:
        focus_lower = user_focus.lower()
        axes.sort(
            key=lambda a: (0 if a[0].lower() in focus_lower else 1, a[0]),
        )

    # Prune partial scan: drop axes no longer in the filtered axes set
    if partial_scan:
        active_axis_names = {a[0] for a in axes}
        stale = set(partial_scan.get("completed_axes", [])) - active_axis_names
        if stale:
            logger.warning(
                "Pruning %d stale axes from partial scan: %s", len(stale), stale,
            )
            partial_scan["completed_axes"] = [
                a for a in partial_scan["completed_axes"] if a in active_axis_names
            ]
            partial_scan["rows"] = [
                r for r in partial_scan.get("rows", [])
                if r["axis"] in active_axis_names
            ]

    rows: list[dict] = []
    axis_stats: dict[str, list[float]] = defaultdict(list)
    completed_axes: set[str] = set()

    # Restore partial state from a previously interrupted scan
    if partial_scan:
        for row in partial_scan.get("rows", []):
            rows.append(row)
            axis_stats[row["axis"]].append(row["delta"])
        completed_axes = set(partial_scan.get("completed_axes", []))
        logger.info(
            "Resuming scan: %d rows, %d/%d axes completed",
            len(rows), len(completed_axes), len(axes),
        )

    for ai, (axis_name, axis_type, values) in enumerate(axes):
        if axis_name in completed_axes:
            logger.info("Skipping completed axis: %s", axis_name)
            continue

        _cb({
            "type": "axis_start",
            "axis": axis_name, "axis_type": axis_type,
            "cardinality": len(values),
            "axis_index": ai, "total_axes": len(axes),
        })

        for vi, value in enumerate(values):
            # Skip the baseline value for each axis
            if axis_type == "prompt_field":
                current_val = getattr(baseline_ps, axis_name, "")
            else:
                current_val = base_params.get(axis_name)

            if value == current_val:
                rows.append({
                    "axis": axis_name, "axis_type": axis_type,
                    "value_idx": vi,
                    "value_preview": _preview(value),
                    "hits": baseline_scores["hits"],
                    "total": baseline_scores["total"],
                    "accuracy": baseline_acc, "delta": 0.0,
                    "errors": 0,
                })
                axis_stats[axis_name].append(0.0)
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

            if axis_type == "prompt_field":
                perturbed = baseline_ps.derive(**{axis_name: value})
                scores = await _eval_ps(perturbed)
            else:
                perturbed_params = {**base_params, axis_name: value}
                scores = await _eval_ps(baseline_ps, perturbed_params)

            acc = scores["accuracy"]
            delta = acc - baseline_acc
            rows.append({
                "axis": axis_name, "axis_type": axis_type,
                "value_idx": vi,
                "value_preview": _preview(value),
                "hits": scores["hits"],
                "total": scores["total"],
                "accuracy": acc, "delta": delta,
                "errors": scores.get("errors", 0),
            })
            axis_stats[axis_name].append(delta)
            _cb({
                "type": "variant_done",
                "axis": axis_name, "value_idx": vi,
                "value_preview": _preview(value),
                "is_baseline_value": False,
                "accuracy": acc, "delta": delta,
                "hits": scores["hits"], "total": scores["total"],
                "results": scores.get("results", []),
                "cached": scores.get("cached", False),
            })

        # Check: did any non-baseline variant succeed?
        axis_variants = [
            r for r in rows
            if r["axis"] == axis_name and r.get("delta") != 0.0
        ]
        all_errored = (
            all(
                r.get("errors", 0) > 0 and r.get("errors", 0) == r.get("total", 0)
                for r in axis_variants
            )
            if axis_variants
            else False
        )

        if all_errored:
            logger.warning(
                "Axis '%s': all variants errored — not marking completed",
                axis_name,
            )
        else:
            completed_axes.add(axis_name)
        if store and backend_id and plan_id:
            store.smart_search.update(backend_id, plan_id, {
                "status": "scan_partial",
                "scan_results": {
                    "rows": rows,
                    "completed_axes": list(completed_axes),
                },
            })
            logger.info("Checkpoint: axis '%s' complete (%d/%d)", axis_name,
                        len(completed_axes), len(axes))

    # Build axis profiles
    profiles = _profiles_from_rows(rows, axes, len(eval_data))
    for profile in profiles:
        _cb({"type": "axis_done", **profile})

    # Persist scan results to plan
    if store and backend_id and plan_id:
        df = pd.DataFrame(rows)
        store.smart_search.update(backend_id, plan_id, {
            "status": "scan_complete",
            "scan_results": {
                "rows": df.to_dict(orient="records"),
                "axis_profiles": profiles,
            },
        })
        logger.info("Saved scan results to plan: %s", plan_id)
        return df, profiles

    return pd.DataFrame(rows), profiles


# ---------------------------------------------------------------------------
# Select best from scan (greedy composition, no backend calls)
# ---------------------------------------------------------------------------


def select_scan_winner(
    scan_df: pd.DataFrame,
    axis_profiles: list[dict],
    baseline_ps: PromptState,
    variant_library: dict,
    pipeline_params: dict | None = None,
) -> tuple[PromptState, dict]:
    """Pick best variant per sensitive axis from OAT scan results.

    Composes the best-performing value for each axis that showed positive
    improvement (best_delta > 0) into a single PromptState. No backend
    calls required — purely offline composition from existing scan data.

    Returns:
        (best_ps, best_params) — ready for evaluate_prompt() or feedback cycle.
    """
    base_params = dict(pipeline_params or {})
    prompt_changes: dict[str, Any] = {}
    param_changes: dict[str, Any] = {}

    improving = [
        p for p in axis_profiles
        if p["best_delta"] > 0 and p["exploration_budget"] != "skip"
    ]

    for profile in improving:
        axis_name = profile["axis"]
        axis_type = profile["axis_type"]

        axis_rows = scan_df[scan_df["axis"] == axis_name]
        if axis_rows.empty:
            continue

        best_row = axis_rows.loc[axis_rows["accuracy"].idxmax()]
        value_idx = int(best_row["value_idx"])

        if axis_type == "prompt_field":
            values = variant_library.get("prompt_fields", {}).get(axis_name, [])
        else:
            values = variant_library.get("pipeline_params", {}).get(axis_name, [])

        if value_idx >= len(values):
            logger.warning(
                "select_scan_winner: value_idx %d out of range for %s (len=%d)",
                value_idx, axis_name, len(values),
            )
            continue

        value = values[value_idx]

        if axis_type == "prompt_field":
            prompt_changes[axis_name] = value
        else:
            param_changes[axis_name] = value

    best_ps = baseline_ps
    if prompt_changes:
        best_ps = baseline_ps.derive(
            **prompt_changes,
            changes_description="scan_winner",
        )

    best_params = {**base_params, **param_changes}

    logger.info(
        "select_scan_winner: %d prompt changes, %d param changes from %d improving axes",
        len(prompt_changes), len(param_changes), len(improving),
    )

    return best_ps, best_params


# ---------------------------------------------------------------------------
# Adaptive search (importance-weighted coordinate descent)
# ---------------------------------------------------------------------------


async def adaptive_search(
    baseline_ps: PromptState,
    variant_library: dict,
    eval_data: list,
    backend_client: Any,
    axis_profiles: list[dict],
    max_rounds: int = 3,
    stop_threshold: float = 0.0,
    store: ProjectStore | None = None,
    backend_id: str = "",
    pipeline_params: dict | None = None,
    session_terms: list | None = None,
    progress_cb: Callable[[ScanEvent], None] | None = None,
    prompt_result_index: dict | None = None,
    plan_id: str = "",
    pipeline_schema: "PipelineSchema | None" = None,
) -> tuple[PromptState, dict, pd.DataFrame]:
    """Coordinate descent with per-axis budget from sensitivity profiles.

    Iterates over active axes (those not classified as ``"skip"``),
    sorted by sensitivity. Each round tries all variant values for each
    active axis, keeping the best. Axes are resolved (removed from
    future rounds) when they produce no improvement.

    Args:
        baseline_ps: Starting PromptState.
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
        Tuple of (best_ps, best_pipeline_params, search_log_df).
    """
    _cb = progress_cb or (lambda _e: None)

    if session_terms:
        await backend_client.init_session(session_terms)

    # Filter and sort axes by sensitivity
    active_axes = [
        p for p in axis_profiles if p["exploration_budget"] != "skip"
    ]
    active_axes.sort(key=lambda p: -p["sensitivity_range"])

    current_ps = baseline_ps
    current_params = dict(pipeline_params or {})
    resolved_axes: set[str] = set()
    log_rows: list[dict] = []

    _eval_ps = _make_eval_fn(
        eval_data, backend_client, store, backend_id,
        get_params=lambda: current_params,
        prompt_result_index=prompt_result_index,
        pipeline_schema=pipeline_schema,
    )

    # Get baseline accuracy
    baseline_scores = await _eval_ps(current_ps)
    current_acc = baseline_scores["accuracy"]

    for round_num in range(1, max_rounds + 1):
        round_improved = False
        _cb({
            "type": "round_start",
            "round": round_num, "max_rounds": max_rounds,
            "current_accuracy": current_acc,
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
                getattr(current_ps, axis_name, "")
                if axis_type == "prompt_field"
                else current_params.get(axis_name)
            )
            best_acc = current_acc

            for value in values:
                if axis_type == "prompt_field":
                    test_ps = current_ps.derive(**{axis_name: value})
                    test_params = current_params
                else:
                    test_ps = current_ps
                    test_params = {**current_params, axis_name: value}

                scores = await _eval_ps(test_ps, test_params)

                delta = scores["accuracy"] - current_acc
                log_rows.append({
                    "round": round_num,
                    "axis": axis_name,
                    "axis_type": axis_type,
                    "value_preview": _preview(value),
                    "accuracy": scores["accuracy"],
                    "delta": delta,
                })
                _cb({
                    "type": "variant_done",
                    "round": round_num,
                    "axis": axis_name, "value_preview": _preview(value),
                    "accuracy": scores["accuracy"], "delta": delta,
                    "hits": scores["hits"], "total": scores["total"],
                    "results": scores.get("results", []),
                    "cached": scores.get("cached", False),
                })

                if scores["accuracy"] > best_acc:
                    best_acc = scores["accuracy"]
                    best_value = value

            # Apply best value if improved
            improvement = best_acc - current_acc
            if improvement > stop_threshold:
                if axis_type == "prompt_field":
                    current_ps = current_ps.derive(
                        **{axis_name: best_value},
                        changes_description=(
                            f"adaptive_r{round_num}_{axis_name}"
                        ),
                    )
                else:
                    current_params[axis_name] = best_value
                current_acc = best_acc
                round_improved = True
                _cb({
                    "type": "axis_resolved",
                    "round": round_num,
                    "axis": axis_name, "action": "improved",
                    "best_value": _preview(best_value),
                    "improvement": round(improvement, 4),
                    "new_accuracy": current_acc,
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
                "accuracy": current_acc,
            })
            break
        _cb({
            "type": "round_done",
            "round": round_num, "improved": True,
            "accuracy": current_acc,
        })

    log_df = pd.DataFrame(log_rows)

    # Persist search results to plan
    if store and backend_id and plan_id:
        store.smart_search.update(backend_id, plan_id, {
            "status": "search_complete",
            "search_results": {
                "best_ps": current_ps.model_dump(),
                "best_params": current_params,
                "log_rows": log_df.to_dict(orient="records")
                if not log_df.empty else [],
            },
        })
        logger.info("Saved search results to plan: %s", plan_id)

    return current_ps, current_params, log_df


# ---------------------------------------------------------------------------
# Load scan results from plan store
# ---------------------------------------------------------------------------


def load_scan_results_from_plan(
    store: ProjectStore,
    backend_id: str,
    plan_id: str,
) -> pd.DataFrame | None:
    """Load scan results DataFrame from a persisted smart search plan.

    Returns ``None`` if no plan or no scan rows found.
    """
    from api.services.search.plan_persistence import deserialize_smart_search_plan

    plan_data = store.smart_search.load(backend_id, plan_id)
    if not plan_data:
        return None
    plan = deserialize_smart_search_plan(plan_data)
    rows = (plan.get("scan_results") or {}).get("rows", [])
    if not rows:
        return None
    return pd.DataFrame(rows)


# ---------------------------------------------------------------------------
# Diagnostic set resume / build
# ---------------------------------------------------------------------------


async def resume_or_build_diagnostic(
    campaign_config: dict,
    baseline: PromptState,
    baseline_results: list,
    llm_client: Any,
    model: str,
    store: "ProjectStore",
    backend_id: str,
    eval_data: list,
    improvement_areas: str = "",
    variant_library: dict | None = None,
) -> tuple[str, PromptState, list, dict, list]:
    """Resume or build smart search diagnostic set.

    Returns:
        (plan_id, search_baseline, diagnostic, diag_summary, axis_profiles_or_empty)

    If a plan already exists on disk, skips LLM restructure and diagnostic
    building. If the plan status is ``scan_complete`` or later, also returns
    cached axis profiles.
    """
    import hashlib as _hashlib
    import json as _json

    from api.config.settings import load_variant_library
    from api.services.search.context import restructure_context
    from api.services.search.plan_persistence import (
        deserialize_smart_search_plan,
        serialize_smart_search_plan,
        smart_search_plan_identity,
    )

    ss = campaign_config.get("smart_search", {})
    if variant_library is None:
        variant_library = load_variant_library()

    plan_id = smart_search_plan_identity(
        baseline.instruction,
        variant_library,
        ss,
        improvement_areas,
        seed=ss.get("seed", 42),
    )

    existing = store.smart_search.load(backend_id, plan_id)
    if existing:
        status = existing.get("status", "?")
        plan = deserialize_smart_search_plan(existing)

        if plan["scan_results"] and status in ("scan_complete", "search_complete"):
            cached_profiles = plan["scan_results"].get("axis_profiles", [])
            logger.info("Scan complete in plan %s, reusing profiles", plan_id)
            return (
                plan_id,
                plan["search_baseline_ps"],
                plan["diagnostic"],
                plan["diag_summary"],
                cached_profiles,
            )
        if plan["scan_results"] and status == "scan_partial":
            partial_rows = plan["scan_results"].get("rows", [])
            partial_completed = plan["scan_results"].get("completed_axes", [])
            # Rebuild axes list to compute partial profiles
            _vl = load_variant_library()
            _axes: list[tuple[str, str, list]] = []
            for name, vals in _vl.get("prompt_fields", {}).items():
                if len(vals) > 1:
                    _axes.append((name, "prompt_field", vals))
            for name, vals in _vl.get("pipeline_params", {}).items():
                if len(vals) > 1:
                    _axes.append((name, "pipeline_param", vals))
            partial_profiles = _profiles_from_rows(
                partial_rows, _axes, len(plan["diagnostic"]),
            )
            logger.info(
                "Scan partial in plan %s: %d axes done, %d profiles",
                plan_id, len(partial_completed), len(partial_profiles),
            )
            return (
                plan_id,
                plan["search_baseline_ps"],
                plan["diagnostic"],
                plan["diag_summary"],
                partial_profiles,
            )

        # diagnostic_built -> reuse saved baseline; prefer sibling with scan data
        siblings = [
            s for s in store.smart_search.list_all(backend_id)
            if s["plan_id"] != plan_id
            and s["status"] in ("scan_complete", "search_complete")
            and s.get("variant_library_hash") == existing.get("variant_library_hash", "")
            and s.get("n_axis_profiles", 0) > 0
        ]
        if siblings:
            current_n_diag = plan.get("config", {}).get("n_diagnostic", 6)
            siblings.sort(key=lambda s: (
                s.get("n_diagnostic") != current_n_diag,
                s["status"] != "scan_complete",
            ))
            sib_data = store.smart_search.load(backend_id, siblings[0]["plan_id"])
            sib_plan = deserialize_smart_search_plan(sib_data)
            sib_profiles = (sib_plan.get("scan_results") or {}).get("axis_profiles", [])
            logger.info(
                "Adopting scan data from sibling plan %s (%d profiles)",
                siblings[0]["plan_id"], len(sib_profiles),
            )
            return (
                plan_id,
                sib_plan["search_baseline_ps"],
                sib_plan["diagnostic"],
                sib_plan["diag_summary"],
                sib_profiles,
            )

        logger.info("Plan %s (status: %s), reusing saved diagnostic", plan_id, status)
        return (
            plan_id,
            plan["search_baseline_ps"],
            plan["diagnostic"],
            plan["diag_summary"],
            [],
        )

    # Build new plan: LLM restructure + diagnostic set
    logger.info("Building new smart search plan: %s", plan_id)
    layer1_fields = await restructure_context(
        baseline.instruction, llm_client,
        model=model,
        improvement_areas=improvement_areas,
    )
    search_baseline = baseline.derive(
        **{k: v for k, v in layer1_fields.items() if v and k != "consultation"},
        changes_description="search_baseline (decomposed)",
    )

    diagnostic, diag_summary = build_diagnostic_set(
        eval_data, baseline_results,
        n_queries=ss.get("n_diagnostic", 6),
    )

    # Compute a short hash of the full variant library for traceability
    vl_json = _json.dumps(variant_library, sort_keys=True)
    vl_hash = _hashlib.sha256(vl_json.encode()).hexdigest()[:12]

    config = {
        "n_diagnostic": ss.get("n_diagnostic", 6),
        "max_rounds": ss.get("max_rounds", 3),
        "stop_threshold": ss.get("stop_threshold", 0.0),
    }
    plan_data = serialize_smart_search_plan(
        plan_id, config, baseline, search_baseline,
        layer1_fields, diagnostic, diag_summary, vl_hash,
    )
    store.smart_search.save(backend_id, plan_id, plan_data)

    return plan_id, search_baseline, diagnostic, diag_summary, []
