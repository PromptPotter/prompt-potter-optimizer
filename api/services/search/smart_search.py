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


def _preview(value: Any, max_len: int = 40) -> str:
    """Truncated preview of a variant value."""
    s = str(value)
    if not s:
        return "(empty)"
    return s[:max_len] + ("..." if len(s) > max_len else "")


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
        existing = store.load_dataset_run_by_hash(backend_id, content_hash)
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
        partial = store.load_partial_eval(backend_id, run_id)
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
            store.append_eval_item(backend_id, run_id, result)
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
        store.finalize_eval_run(backend_id, run_id, run_data)

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

    return pd.DataFrame(rows), profiles


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

    return current_ps, current_params, pd.DataFrame(log_rows)
