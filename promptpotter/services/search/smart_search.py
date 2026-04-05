"""Shared types and utilities for sensitivity scan and adaptive search.

Axis classification, variant library filtering, eval function factory,
and diagnostic set builder.
"""
from __future__ import annotations

import logging
import random
from collections import defaultdict
from collections.abc import Callable
from typing import TYPE_CHECKING, Literal, TypedDict

from promptpotter.models.eval_context import EvalContext
from promptpotter.models.opt_search_point import OptSearchPoint
from promptpotter.services.eval_gateway import eval_search_point
from promptpotter.shared.constants import (
    DEFAULT_DIAGNOSTIC_QUERIES,
    DIAGNOSTIC_HIT_RATIO,
    MIN_DIAGNOSTIC_QUERIES,
    SCAN_TARGET_MDE,
)
from promptpotter.shared.errors import is_error_result

if TYPE_CHECKING:
    from promptpotter.models.pipeline_schema import PipelineSchema

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
    composite: float
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



def _profiles_from_rows(
    rows: list[dict],
    axes: list[tuple],
    n_eval: int,
) -> list[dict]:
    """Build axis profiles from scan rows.

    Args:
        rows: Per-variant result dicts with ``axis``, ``delta`` keys.
        axes: List of axis tuples (3 or 4 elements; extra elements ignored).
        n_eval: Number of diagnostic queries (for noise threshold).

    Returns:
        Axis profiles sorted by sensitivity_range (descending).
    """
    axis_deltas: dict[str, list[float]] = defaultdict(list)
    for row in rows:
        axis_deltas[row["axis"]].append(row["delta"])

    profiles: list[dict] = []
    for axis_tuple in axes:
        axis_name, axis_type, values = axis_tuple[0], axis_tuple[1], axis_tuple[2]
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
    dataset: list,
    ctx: EvalContext,
    get_params: Callable[[], dict],
    on_result: Callable | None = None,
) -> Callable:
    """Factory for the ``_eval_opt`` closure used by scan and adaptive search."""

    # Resolve prompt node — only if active in pipeline steps
    _pn = ""
    if ctx.pipeline_schema:
        _active = set(get_params().get("steps", []))
        for _name in ctx.pipeline_schema.prompt_node_names():
            if _name in _active:
                _pn = _name
                break

    async def _eval_opt(opt: OptSearchPoint, pp: dict | None = None) -> dict:
        _base_pp = pp or get_params()
        _steps = (_base_pp or {}).get("steps", [])
        sp = opt.to_job_search_point(
            base_pipeline_params=_base_pp,
            active_steps=_steps or None,
            prompt_node=_pn,
        )
        results, scores, cached = await eval_search_point(
            sp, dataset, ctx,
            label="scan",
            on_result=on_result,
        )
        return {**scores, "results": results, "cached": cached}
    return _eval_opt


# ---------------------------------------------------------------------------
# Diagnostic set builder
# ---------------------------------------------------------------------------


def build_diagnostic_set(
    dataset: list,
    baseline_results: list,
    n_queries: int = DEFAULT_DIAGNOSTIC_QUERIES,
    seed: int = 42,
    pipeline_schema: PipelineSchema | None = None,
) -> tuple[list, dict]:
    """Stratified query set: ~75% baseline hits (regression guard) + ~25% misses.

    Args:
        dataset: Full evaluation dataset (list of query dicts).
        baseline_results: Results from baseline evaluation (list of result dicts
            with ``hit`` and ``query`` keys).
        n_queries: Number of queries in the diagnostic set.
        seed: Random seed for reproducible sampling.

    Returns:
        Tuple of (diagnostic_queries, summary_dict).

    Raises:
        ValueError: If fewer than ``MIN_DIAGNOSTIC_QUERIES`` queries available.
    """
    if len(dataset) < MIN_DIAGNOSTIC_QUERIES:
        raise ValueError(
            f"Need at least {MIN_DIAGNOSTIC_QUERIES} eval queries, "
            f"got {len(dataset)}."
        )

    # Auto-adjust sample size for statistical power (Wave 2a)
    try:
        from promptpotter.services.search._stats import min_sample_size

        min_n = min_sample_size(SCAN_TARGET_MDE)
        if n_queries < min_n:
            adjusted = min(min_n, len(dataset))
            if adjusted > n_queries:
                logger.warning(
                    "Scan sample size %d too small to detect %.0f%% effect "
                    "(need %d); adjusting to %d",
                    n_queries, SCAN_TARGET_MDE * 100, min_n, adjusted,
                )
                n_queries = adjusted
    except ImportError:
        pass  # scipy not installed — skip auto-adjustment

    # Map queries to dataset items
    query_to_eval = {d["query"]: d for d in dataset}

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

    # Fallback: no baseline results — sample randomly from dataset
    if not hits and not misses:
        n = min(n_queries, len(dataset))
        diagnostic = rng.sample(dataset, n)
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

    # Wave 2c: diagnostic-aware miss stratification
    selected_misses = _stratify_misses(
        misses, baseline_results, n_misses, rng, pipeline_schema,
    )

    diagnostic = selected_hits + selected_misses
    rng.shuffle(diagnostic)

    summary = {
        "n_queries": len(diagnostic),
        "n_hits": len(selected_hits),
        "n_misses": len(selected_misses),
        "total_pool_hits": len(hits),
        "total_pool_misses": len(misses),
        "stratified": pipeline_schema is not None,
    }

    return diagnostic, summary


def _stratify_misses(
    miss_pool: list[dict],
    baseline_results: list[dict],
    n_misses: int,
    rng: random.Random,
    pipeline_schema: PipelineSchema | None,
) -> list[dict]:
    """Select misses ensuring each failure pattern is represented.

    Falls back to random sampling when ``pipeline_schema`` is not provided
    or when there are fewer patterns than slots.
    """
    if not pipeline_schema or n_misses <= 0 or not miss_pool:
        return rng.sample(miss_pool, min(n_misses, len(miss_pool)))

    from promptpotter.services.metrics import extract_sample_diagnostics

    # Map miss queries to their baseline results for diagnostic extraction
    result_by_query: dict[str, dict] = {}
    for r in baseline_results:
        if not r.get("hit") and not is_error_result(r):
            result_by_query[r.get("query", "")] = r

    # Group miss pool items by failure pattern key
    pattern_buckets: dict[tuple[str, ...], list[dict]] = defaultdict(list)
    for item in miss_pool:
        q = item["query"]
        r = result_by_query.get(q)
        if r:
            diag = extract_sample_diagnostics(r, pipeline_schema)
            key = tuple(
                f"{k}={diag[k]}" for k in ("gt_in_source", "gt_in_ranked", "terminated_at")
                if k in diag
            )
        else:
            key = ("unknown",)
        pattern_buckets[key].append(item)

    # Round-robin: one from each pattern, then fill remaining randomly
    selected: list[dict] = []
    used: set[str] = set()
    buckets = list(pattern_buckets.values())
    rng.shuffle(buckets)

    # First pass: one per pattern
    for bucket in buckets:
        if len(selected) >= n_misses:
            break
        pick = rng.choice(bucket)
        selected.append(pick)
        used.add(pick["query"])

    # Second pass: fill remaining from unused pool
    if len(selected) < n_misses:
        remaining = [m for m in miss_pool if m["query"] not in used]
        rng.shuffle(remaining)
        selected.extend(remaining[: n_misses - len(selected)])

    return selected[:n_misses]


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
    schema: PipelineSchema | None = None,
) -> dict:
    """Filter variant library to axes relevant for the active pipeline.

    - Keeps only ``pipeline_params`` axes whose owning step is active.
    - Drops all ``prompt_fields`` when no active step has ``prompt_meta``
      (prompt fields only affect steps with an LLM prompt template).

    Args:
        variant_library: Full variant library dict with ``prompt_fields``
            and optional ``pipeline_params`` sections.
        pipeline_params: Current pipeline parameters (must contain ``steps``
            key listing active step names).
        schema: PipelineSchema for step-param lookup.

    Returns:
        Filtered copy of the variant library.
    """
    if schema is None:
        return variant_library

    active_steps = set((pipeline_params or {}).get("steps", []))

    # Build set of param keys owned by active steps
    step_param_map = schema.node_param_keys()
    active_param_keys: set[str] = set()
    for step_name, param_keys in step_param_map.items():
        if step_name in active_steps:
            active_param_keys |= param_keys

    # Filter pipeline_params to only active-step keys
    filtered_pp: dict[str, list] = {}
    for axis, values in variant_library.get("pipeline_params", {}).items():
        if axis in active_param_keys:
            filtered_pp[axis] = values

    # Drop prompt_fields when no active step has a prompt template
    has_prompt_step = any(
        step.prompt_meta is not None
        for step in schema.nodes
        if step.name in active_steps
    )
    filtered_pf = variant_library.get("prompt_fields", {})
    if not has_prompt_step:
        filtered_pf = {}

    result: dict = {}
    if filtered_pf:
        result["prompt_fields"] = filtered_pf
    if filtered_pp:
        result["pipeline_params"] = filtered_pp

    removed_pp = set(variant_library.get("pipeline_params", {})) - set(filtered_pp)
    if removed_pp:
        logger.debug("filter_variant_library: dropped pipeline_params %s", removed_pp)
    if not filtered_pf and variant_library.get("prompt_fields"):
        logger.debug(
            "filter_variant_library: dropped all prompt_fields "
            "(no active step with prompt_meta)"
        )

    return result


def load_filtered_variant_library(
    pipeline_params: dict | None = None,
    pipeline_schema: PipelineSchema | None = None,
) -> dict:
    """Load variant library, filtering to active pipeline steps when possible."""
    from promptpotter.config.settings import load_variant_library
    lib = load_variant_library()
    if pipeline_params and pipeline_schema:
        lib = filter_variant_library(lib, pipeline_params, schema=pipeline_schema)
    return lib
