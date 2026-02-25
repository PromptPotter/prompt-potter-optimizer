"""Sensitivity scan, adaptive search, diagnostic set, and axis classification.

One-at-a-time (OAT) perturbation scanning and importance-weighted
coordinate descent over prompt-field and pipeline-param axes.
"""

import hashlib
import logging
import random
from collections import defaultdict
from typing import Any, Callable

import pandas as pd

from api.models.prompt_state import PromptState
from api.services.project_store import ProjectStore
from api.services.prompt_eval import (
    HASH_TRUNCATE,
    backend_reranker_eval,
    build_dataset_run_data,
    compute_accuracy,
    eval_content_hash,
)

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

MIN_DIAGNOSTIC_QUERIES = 3
DEFAULT_DIAGNOSTIC_QUERIES = 6
DIAGNOSTIC_HIT_RATIO = 0.75


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
# Helpers
# ---------------------------------------------------------------------------

# Re-export for backward compatibility (synthesis.py imports from here)
from api.services.search.utils import preview as _preview  # noqa: E402


async def _eval_config(
    rendered_prompt: str,
    eval_data: list,
    backend_client: Any,
    pipeline_params: dict | None = None,
    request_delay: float = 1.0,
    store: ProjectStore | None = None,
    backend_id: str = "",
    prompt_result_index: dict | None = None,
) -> dict:
    """Evaluate a single config against the diagnostic set.

    Returns dict with keys: hits, total, accuracy, errors, results, cached.
    ``results`` contains per-query dicts; ``cached`` indicates cache hit.
    Uses content-hash caching when store is available, then falls back to
    the cross-run ``prompt_result_index`` for query-level historical lookups.
    """
    import asyncio

    content_hash = eval_content_hash(rendered_prompt, eval_data, "", 0.0)

    # Check exact content-hash cache (prompt + exact query set)
    if store and backend_id:
        existing = store.dataset_runs.load_by_hash(backend_id, content_hash)
        if existing:
            return {
                **existing["scores"],
                "results": existing.get("dataset_run_items", []),
                "cached": True,
            }

    # Check cross-run historical index (query-level lookups)
    if prompt_result_index and eval_data:
        rp_hash = hashlib.sha256(
            rendered_prompt.encode(),
        ).hexdigest()[:HASH_TRUNCATE]
        cached_by_query = prompt_result_index.get(rp_hash, {})
        if cached_by_query:
            n_matched = sum(
                1 for qd in eval_data if qd.get("query", "") in cached_by_query
            )
            coverage = n_matched / len(eval_data)
            if coverage >= 1.0:
                # Full historical coverage — skip backend entirely
                matched = [
                    cached_by_query[qd["query"]] for qd in eval_data
                ]
                acc = compute_accuracy(matched)
                logger.info(
                    "_eval_config [historical] full coverage (%d/%d queries)",
                    n_matched, len(eval_data),
                )
                return {**acc, "results": matched, "cached": True}
            if coverage > 0:
                logger.info(
                    "_eval_config [historical] partial coverage: "
                    "%.0f%% (%d/%d queries)",
                    coverage * 100, n_matched, len(eval_data),
                )
                # Continue below — evaluate only missing queries

    run_id = f"scan_{content_hash[:8]}"

    # Resume from partial results if available
    results: list[dict] = []
    start_idx = 0
    if store and backend_id:
        partial = store.dataset_runs.load_partial_eval(backend_id, run_id)
        if partial:
            results = partial
            start_idx = len(partial)
            logger.info(
                "_eval_config [%s] resuming from query %d/%d (partial)",
                run_id, start_idx + 1, len(eval_data),
            )

    # Determine which queries still need backend evaluation
    # If we got partial historical coverage, only evaluate missing queries
    queries_to_eval = eval_data
    historical_results: list[dict] = []
    if prompt_result_index and eval_data and not results:
        rp_hash = hashlib.sha256(
            rendered_prompt.encode(),
        ).hexdigest()[:HASH_TRUNCATE]
        cached_by_query = prompt_result_index.get(rp_hash, {})
        if cached_by_query:
            queries_to_eval = []
            for qd in eval_data:
                q = qd.get("query", "")
                if q in cached_by_query:
                    historical_results.append(cached_by_query[q])
                else:
                    queries_to_eval.append(qd)

    n_to_eval = len(queries_to_eval)
    for qi in range(start_idx, n_to_eval):
        qd = queries_to_eval[qi]
        logger.info(
            "_eval_config [%s] query %d/%d: %s",
            run_id, qi + 1, n_to_eval, qd["query"][:60],
        )
        result = await backend_reranker_eval(
            qd, backend_client, rendered_prompt,
            pipeline_params=pipeline_params,
        )
        hit_str = "HIT" if result.get("hit") else "MISS"
        logger.info(
            "_eval_config [%s] query %d/%d -> %s  pred=%s",
            run_id, qi + 1, n_to_eval, hit_str,
            (result.get("predicted") or "")[:50],
        )
        results.append(result)
        if store and backend_id:
            store.dataset_runs.append_eval_item(backend_id, run_id, result)
        if request_delay > 0:
            await asyncio.sleep(request_delay)

    # Merge historical results with freshly evaluated results
    all_results = historical_results + results
    acc = compute_accuracy(all_results)

    if store and backend_id:
        run_data = build_dataset_run_data(
            run_id, "scan", content_hash, "",
            rendered_prompt, "", 0.0, acc, all_results,
        )
        store.dataset_runs.finalize_eval_run(backend_id, run_id, run_data)

    return {
        **acc,
        "results": all_results,
        "cached": len(results) == 0 and len(historical_results) > 0,
    }


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
    request_delay: float = 1.0,
    session_terms: list | None = None,
    progress_cb: Callable | None = None,
    prompt_result_index: dict | None = None,
    plan_id: str = "",
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
        request_delay: Delay between backend calls.
        session_terms: Optional session terms for backend init.
        progress_cb: Optional callback ``(event: dict) -> None`` for
            progress reporting. Event types: ``baseline_done``,
            ``axis_start``, ``variant_done``, ``axis_done``.

    Returns:
        Tuple of (per_variant_df, axis_profiles).
    """
    _cb = progress_cb or (lambda _e: None)

    if session_terms:
        await backend_client.init_session(session_terms)

    base_params = dict(pipeline_params or {})
    baseline_rendered = baseline_ps.render()

    # Evaluate baseline
    baseline_scores = await _eval_config(
        baseline_rendered, eval_data, backend_client,
        pipeline_params=base_params,
        request_delay=request_delay,
        store=store, backend_id=backend_id,
        prompt_result_index=prompt_result_index,
    )
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

    rows: list[dict] = []
    axis_stats: dict[str, list[float]] = defaultdict(list)

    for ai, (axis_name, axis_type, values) in enumerate(axes):
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
                if value == current_val:
                    delta = 0.0
                    acc = baseline_acc
                    rows.append({
                        "axis": axis_name, "axis_type": axis_type,
                        "value_idx": vi,
                        "value_preview": _preview(value),
                        "hits": baseline_scores["hits"],
                        "total": baseline_scores["total"],
                        "accuracy": acc, "delta": delta,
                    })
                    axis_stats[axis_name].append(delta)
                    _cb({
                        "type": "variant_done",
                        "axis": axis_name, "value_idx": vi,
                        "value_preview": _preview(value),
                        "is_baseline_value": True,
                        "accuracy": acc, "delta": delta,
                        "hits": baseline_scores["hits"],
                        "total": baseline_scores["total"],
                        "results": [], "cached": False,
                    })
                    continue

                perturbed = baseline_ps.derive(**{axis_name: value})
                rendered = perturbed.render()
                scores = await _eval_config(
                    rendered, eval_data, backend_client,
                    pipeline_params=base_params,
                    request_delay=request_delay,
                    store=store, backend_id=backend_id,
                    prompt_result_index=prompt_result_index,
                )
            else:
                # Pipeline param
                current_val = base_params.get(axis_name)
                if value == current_val:
                    delta = 0.0
                    acc = baseline_acc
                    rows.append({
                        "axis": axis_name, "axis_type": axis_type,
                        "value_idx": vi,
                        "value_preview": _preview(value),
                        "hits": baseline_scores["hits"],
                        "total": baseline_scores["total"],
                        "accuracy": acc, "delta": delta,
                    })
                    axis_stats[axis_name].append(delta)
                    _cb({
                        "type": "variant_done",
                        "axis": axis_name, "value_idx": vi,
                        "value_preview": _preview(value),
                        "is_baseline_value": True,
                        "accuracy": acc, "delta": delta,
                        "hits": baseline_scores["hits"],
                        "total": baseline_scores["total"],
                        "results": [], "cached": False,
                    })
                    continue

                perturbed_params = {**base_params, axis_name: value}
                scores = await _eval_config(
                    baseline_rendered, eval_data, backend_client,
                    pipeline_params=perturbed_params,
                    request_delay=request_delay,
                    store=store, backend_id=backend_id,
                    prompt_result_index=prompt_result_index,
                )

            acc = scores["accuracy"]
            delta = acc - baseline_acc
            rows.append({
                "axis": axis_name, "axis_type": axis_type,
                "value_idx": vi,
                "value_preview": _preview(value),
                "hits": scores["hits"],
                "total": scores["total"],
                "accuracy": acc, "delta": delta,
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

    # Build axis profiles
    profiles: list[dict] = []
    for axis_name, axis_type, values in axes:
        deltas = axis_stats.get(axis_name, [0.0])
        sens_range = max(deltas) - min(deltas) if deltas else 0.0
        card = len(values)
        budget = classify_axis(card, sens_range, len(eval_data))
        profile = {
            "axis": axis_name,
            "axis_type": axis_type,
            "cardinality": card,
            "sensitivity_range": round(sens_range, 4),
            "best_delta": round(max(deltas), 4) if deltas else 0.0,
            "worst_delta": round(min(deltas), 4) if deltas else 0.0,
            "exploration_budget": budget,
            "estimated_eval_cost": card * len(eval_data),
        }
        profiles.append(profile)
        _cb({"type": "axis_done", **profile})

    profiles.sort(key=lambda p: -p["sensitivity_range"])

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
    request_delay: float = 1.0,
    session_terms: list | None = None,
    progress_cb: Callable | None = None,
    prompt_result_index: dict | None = None,
    plan_id: str = "",
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
        request_delay: Delay between backend calls.
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

    # Get baseline accuracy
    baseline_rendered = current_ps.render()
    baseline_scores = await _eval_config(
        baseline_rendered, eval_data, backend_client,
        pipeline_params=current_params,
        request_delay=request_delay,
        store=store, backend_id=backend_id,
        prompt_result_index=prompt_result_index,
    )
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
                    rendered = test_ps.render()
                    test_params = current_params
                else:
                    rendered = current_ps.render()
                    test_params = {**current_params, axis_name: value}

                scores = await _eval_config(
                    rendered, eval_data, backend_client,
                    pipeline_params=test_params,
                    request_delay=request_delay,
                    store=store, backend_id=backend_id,
                    prompt_result_index=prompt_result_index,
                )

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
