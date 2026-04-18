"""Shared types and utilities for sensitivity scan and adaptive search.

Axis classification, variant library filtering, eval function factory,
diagnostic set builder, and plan persistence.
"""

from __future__ import annotations

import hashlib
import json
import logging
import random
from collections import defaultdict
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Literal, TypedDict

from promptpotter.application.recon.failure_groups import preview as _preview
from promptpotter.application.scoring.search_point_scorer import score_search_point
from promptpotter.domain.opt_search_point import OptSearchPoint
from promptpotter.domain.scoring import ScoringEnv
from promptpotter.shared.constants import (
    DEFAULT_DIAGNOSTIC_QUERIES,
    DIAGNOSTIC_HIT_RATIO,
    MIN_DIAGNOSTIC_QUERIES,
    RECON_TARGET_MDE,
)
from promptpotter.shared.errors import is_error_result
from promptpotter.shared.statistics import min_sample_size

if TYPE_CHECKING:
    import pandas as pd

    from promptpotter.application.campaign.campaign_setup import SessionEnv
    from promptpotter.domain.pipeline_schema import PipelineSchema

logger = logging.getLogger(__name__)


class ReconEvent(TypedDict, total=False):
    """Progress event emitted by ``run_recon()`` and ``run_adaptive_recon()``.

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
        "axis_pruned",
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
    # axis_pruned
    variants_tested: int
    remaining_skipped: int
    # axis_done profile fields
    sensitivity_range: float
    best_delta: float
    worst_delta: float
    exploration_budget: str
    estimated_eval_cost: int


def build_axis_profiles(
    rows: list[dict],
    axes: list[tuple],
    n_samples: int,
) -> list[dict]:
    """Build axis profiles from scan rows.

    Args:
        rows: Per-variant result dicts with ``axis``, ``delta`` keys.
        axes: List of axis tuples (3 or 4 elements; extra elements ignored).
        n_samples: Number of diagnostic queries (for noise threshold).

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
        budget = classify_axis(card, sens_range, n_samples)
        profiles.append(
            {
                "axis": axis_name,
                "axis_type": axis_type,
                "cardinality": card,
                "sensitivity_range": round(sens_range, 4),
                "best_delta": round(max(deltas), 4) if deltas else 0.0,
                "worst_delta": round(min(deltas), 4) if deltas else 0.0,
                "exploration_budget": budget,
                "estimated_scoring_cost": card * n_samples,
            }
        )

    profiles.sort(key=lambda p: -p["sensitivity_range"])
    return profiles


def make_scoring_fn(
    dataset: list,
    ctx: ScoringEnv,
    get_params: Callable[[], dict],
    on_result: Callable | None = None,
) -> Callable:
    """Factory for the ``_score_opt`` closure used by scan and adaptive search."""

    async def _score_opt(opt: OptSearchPoint, pp: dict | None = None) -> dict:
        _base_pp = pp or get_params()
        sp = opt.to_job_search_point(
            base_pipeline_params=_base_pp,
            schema=ctx.pipeline_schema,
        )
        results, scores, cached, _ = await score_search_point(
            sp,
            dataset,
            ctx,
            label="scan",
            on_result=on_result,
        )
        return {**scores, "results": results, "cached": cached}

    return _score_opt


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
            f"Need at least {MIN_DIAGNOSTIC_QUERIES} eval queries, got {len(dataset)}."
        )

    # Auto-adjust sample size for statistical power (Wave 2a)
    min_n = min_sample_size(RECON_TARGET_MDE)
    if n_queries < min_n:
        adjusted = min(min_n, len(dataset))
        if adjusted > n_queries:
            logger.warning(
                "Scan sample size %d too small to detect %.0f%% effect (need %d); adjusting to %d",
                n_queries,
                RECON_TARGET_MDE * 100,
                min_n,
                adjusted,
            )
            n_queries = adjusted

    # Map queries to dataset items
    query_to_score = {d["query"]: d for d in dataset}

    hits = []
    misses = []
    for r in baseline_results:
        q = r.get("query", "")
        if q not in query_to_score:
            continue
        if r.get("hit"):
            hits.append(query_to_score[q])
        else:
            misses.append(query_to_score[q])

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
        misses,
        baseline_results,
        n_misses,
        rng,
        pipeline_schema,
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

    from promptpotter.application.scoring.metrics import extract_sample_diagnostics

    # Map miss queries to their baseline results for diagnostic extraction
    result_by_query: dict[str, dict[str, Any]] = {}
    for r in baseline_results:
        if not r.get("hit") and not is_error_result(r):
            result_by_query[r.get("query", "")] = r

    # Group miss pool items by failure pattern key
    pattern_buckets: dict[tuple[str, ...], list[dict]] = defaultdict(list)
    for item in miss_pool:
        q = item["query"]
        matched = result_by_query.get(q)
        if matched:
            diag = extract_sample_diagnostics(matched, pipeline_schema)
            key = tuple(
                f"{k}={diag[k]}"
                for k in ("gt_in_source", "gt_in_ranked", "terminated_at")
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

    # Schema is pre-filtered to active nodes — use it directly
    active_param_keys: set[str] = {p for n in schema.nodes for p in n.param_keys}

    # Filter pipeline_params to only active-step keys
    filtered_pp: dict[str, list] = {}
    for axis, values in variant_library.get("pipeline_params", {}).items():
        if axis in active_param_keys:
            filtered_pp[axis] = values

    # Drop prompt_fields when no active step has a prompt template
    filtered_pf = variant_library.get("prompt_fields", {})
    if not any(n.prompt_meta is not None for n in schema.nodes):
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
            "filter_variant_library: dropped all prompt_fields (no active step with prompt_meta)"
        )

    return result


SSPLAN_PREFIX = "ssplan_"


def adaptive_recon_plan_identity(
    baseline_instruction: str,
    variant_library: dict,
    adaptive_recon_config: Mapping[str, Any],
    seed: int = 42,
) -> str:
    """Compute a stable identity hash for a smart search plan."""
    payload = json.dumps(
        {
            "baseline_instruction": baseline_instruction,
            "variant_library": variant_library,
            "n_diagnostic": adaptive_recon_config.get("n_diagnostic", 6),
            "max_rounds": adaptive_recon_config.get("max_rounds", 3),
            "stop_threshold": adaptive_recon_config.get("stop_threshold", 0.0),
            "seed": seed,
        },
        sort_keys=True,
        default=str,
    )
    digest = hashlib.sha256(payload.encode()).hexdigest()[:12]
    return f"{SSPLAN_PREFIX}{digest}"


def serialize_adaptive_recon_plan(
    plan_id: str,
    config: dict,
    baseline_opt: OptSearchPoint,
    search_baseline_opt: OptSearchPoint,
    layer1_fields: dict,
    diagnostic: list,
    diag_summary: dict,
    variant_library_hash: str,
) -> dict:
    """Serialize a smart search plan to a JSON-safe dict."""
    return {
        "plan_id": plan_id,
        "status": "diagnostic_built",
        "config": config,
        "baseline_ps": baseline_opt.model_dump(),
        "search_baseline_ps": search_baseline_opt.model_dump(),
        "layer1_fields": layer1_fields,
        "diagnostic": diagnostic,
        "diag_summary": diag_summary,
        "variant_library_hash": variant_library_hash,
    }


def deserialize_adaptive_recon_plan(plan_data: dict) -> dict:
    """Reconstruct smart search plan objects from saved data."""
    return {
        "plan_id": plan_data["plan_id"],
        "status": plan_data["status"],
        "config": plan_data.get("config", {}),
        "baseline_ps": OptSearchPoint.from_prompt_fields(plan_data["baseline_ps"]),
        "search_baseline_ps": OptSearchPoint.from_prompt_fields(plan_data["search_baseline_ps"]),
        "layer1_fields": plan_data.get("layer1_fields", {}),
        "diagnostic": plan_data.get("diagnostic", []),
        "diag_summary": plan_data.get("diag_summary", {}),
        "variant_library_hash": plan_data.get("variant_library_hash", ""),
        "recon_results": plan_data.get("recon_results"),
        "search_results": plan_data.get("search_results"),
    }


async def run_adaptive_recon(
    baseline_opt: OptSearchPoint,
    variant_library: dict,
    dataset: list,
    session: SessionEnv,
    axis_profiles: list[dict],
    max_rounds: int = 3,
    stop_threshold: float = 0.0,
    pipeline_params: dict | None = None,
    progress_cb: Callable[[ReconEvent], None] | None = None,
    plan_id: str = "",
    pipeline_schema: PipelineSchema | None = None,
    experiment_id: str = "",
    scoring_formula: str | None = None,
) -> tuple[OptSearchPoint, dict, pd.DataFrame]:
    """Coordinate descent with per-axis budget from sensitivity profiles.

    Iterates over active axes (those not classified as ``"skip"``),
    sorted by sensitivity. Each round tries all variant values for each
    active axis, keeping the best. Axes are resolved (removed from
    future rounds) when they produce no improvement.

    Args:
        baseline_opt: Starting OptSearchPoint.
        variant_library: Full variant library dict.
        dataset: Diagnostic query set.
        backend_client: Backend client for evaluation.
        axis_profiles: From ``run_recon()``.
        max_rounds: Maximum coordinate descent rounds.
        stop_threshold: Minimum per-round improvement to continue an axis.
        store: Optional ``Stores`` bundle for caching.
        backend_id: Backend identifier.
        pipeline_params: Base pipeline parameters.
        index_terms: Optional session terms.
        progress_cb: Optional callback ``(event: dict) -> None`` for
            progress reporting. Event types: ``round_start``,
            ``axis_start``, ``variant_done``, ``axis_resolved``.

    Returns:
        Tuple of (best_opt, best_pipeline_params, search_log_df).
    """
    import pandas as pd

    # Unpack session
    backend_client = session.backend_client
    store = session.store
    backend_id = session.backend_id
    pipeline_schema = pipeline_schema or session.pipeline_schema

    _cb = progress_cb or (lambda _e: None)

    if session.index_terms:
        await backend_client.init_session(session.index_terms)

    # Filter variant library to active pipeline steps
    variant_library = filter_variant_library(
        variant_library,
        pipeline_params,
        schema=pipeline_schema,
    )

    # Filter and sort axes by sensitivity
    active_axes = [p for p in axis_profiles if p["exploration_budget"] != "skip"]
    active_axes.sort(key=lambda p: -p["sensitivity_range"])

    current_opt = baseline_opt
    current_params = dict(pipeline_params or {})
    resolved_axes: set[str] = set()
    log_rows: list[dict] = []

    from promptpotter.shared.scoring import compile_scorer

    _scan_ctx = ScoringEnv(
        backend_client=backend_client,
        store=store,
        backend_id=backend_id,
        pipeline_schema=pipeline_schema,
        source="run_adaptive_recon",
        experiment_id=experiment_id,
        scorer=compile_scorer(scoring_formula),
    )
    _score_opt = make_scoring_fn(
        dataset,
        _scan_ctx,
        get_params=lambda: current_params,
    )

    # Get baseline accuracy
    baseline_scores = await _score_opt(current_opt)
    current_acc = baseline_scores["accuracy"]
    current_composite = baseline_scores.get("composite", current_acc)

    for round_num in range(1, max_rounds + 1):
        round_improved = False
        _cb(
            {
                "type": "round_start",
                "round": round_num,
                "max_rounds": max_rounds,
                "current_accuracy": current_composite,
                "active_axes": [p["axis"] for p in active_axes if p["axis"] not in resolved_axes],
            }
        )

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
                    axis_name,
                    [],
                )
            else:
                values = variant_library.get("pipeline_params", {}).get(
                    axis_name,
                    [],
                )

            _cb(
                {
                    "type": "axis_start",
                    "round": round_num,
                    "axis": axis_name,
                    "axis_type": axis_type,
                    "cardinality": len(values),
                    "budget": budget,
                }
            )

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

                scores = await _score_opt(test_opt, test_params)

                composite = scores.get("composite", scores["accuracy"])
                delta = composite - current_composite
                log_rows.append(
                    {
                        "round": round_num,
                        "axis": axis_name,
                        "axis_type": axis_type,
                        "value_preview": _preview(value),
                        "accuracy": scores["accuracy"],
                        "composite": composite,
                        "delta": delta,
                    }
                )
                _cb(
                    {
                        "type": "variant_done",
                        "round": round_num,
                        "axis": axis_name,
                        "value_preview": _preview(value),
                        "accuracy": scores["accuracy"],
                        "delta": delta,
                        "composite": composite,
                        "hits": scores["hits"],
                        "total": scores["total"],
                        "results": scores.get("results", []),
                        "cached": scores.get("cached", False),
                    }
                )

                if composite > best_composite:
                    best_composite = composite
                    best_value = value

            # Apply best value if improved
            improvement = best_composite - current_composite
            if improvement > stop_threshold:
                if axis_type == "prompt_field":
                    current_opt = current_opt.derive_candidate(
                        **{axis_name: best_value},
                        changes_description=(f"adaptive_r{round_num}_{axis_name}"),
                    )
                else:
                    current_params[axis_name] = best_value
                current_composite = best_composite
                round_improved = True
                _cb(
                    {
                        "type": "axis_resolved",
                        "round": round_num,
                        "axis": axis_name,
                        "action": "improved",
                        "best_value": _preview(best_value),
                        "improvement": round(improvement, 4),
                        "new_accuracy": current_composite,
                    }
                )
            else:
                resolved_axes.add(axis_name)
                _cb(
                    {
                        "type": "axis_resolved",
                        "round": round_num,
                        "axis": axis_name,
                        "action": "skipped",
                        "improvement": round(improvement, 4),
                    }
                )

        if not round_improved:
            logger.info(
                "Adaptive search: no improvement in round %d, stopping.",
                round_num,
            )
            _cb(
                {
                    "type": "round_done",
                    "round": round_num,
                    "improved": False,
                    "accuracy": current_composite,
                }
            )
            break
        _cb(
            {
                "type": "round_done",
                "round": round_num,
                "improved": True,
                "accuracy": current_composite,
            }
        )

    log_df = pd.DataFrame(log_rows)

    # Persist search results to plan
    if store and backend_id and plan_id:
        store.adaptive_recon.update(
            backend_id,
            plan_id,
            {
                "status": "search_complete",
                "search_results": {
                    "best_opt": current_opt.model_dump(),
                    "best_params": current_params,
                    "log_rows": log_df.to_dict(orient="records") if not log_df.empty else [],
                },
            },
        )
        logger.info("Saved search results to plan: %s", plan_id)

    return current_opt, current_params, log_df


# ---------------------------------------------------------------------------
# Variant library filtering for display (extracted from display layer)
# ---------------------------------------------------------------------------


@dataclass
class FilteredVariantLibrary:
    """Result of filtering the variant library by axes and/or source."""

    fields: dict[str, list[dict]]
    source_counts: dict[str, int]
    per_field_sources: dict[str, dict[str, int]]


def _normalize_variant(v: Any) -> dict:
    """Normalize a variant to dict form with a ``source`` field."""
    return v if isinstance(v, dict) else {"text": v, "source": "PromptPotter"}


def filter_variant_library_display(
    rich: dict,
    axes: list[str] | None = None,
    source: str | None = None,
) -> FilteredVariantLibrary:
    """Filter variant library by axes and source with per-field + global source counts."""
    all_fields = rich.get("prompt_fields", {})
    if axes:
        all_fields = {k: v for k, v in all_fields.items() if k in axes}

    source_counts: dict[str, int] = {}
    filtered: dict[str, list[dict]] = {}
    per_field_sources: dict[str, dict[str, int]] = {}

    for field_name, variants in all_fields.items():
        field_variants: list[dict] = []
        by_source: dict[str, int] = {}
        for v in variants:
            nv = _normalize_variant(v)
            s = nv.get("source") or "PromptPotter"
            source_counts[s] = source_counts.get(s, 0) + 1
            if source and s != source:
                continue
            field_variants.append(nv)
            by_source[s] = by_source.get(s, 0) + 1

        if field_variants:
            filtered[field_name] = field_variants
            per_field_sources[field_name] = by_source

    return FilteredVariantLibrary(
        fields=filtered,
        source_counts=source_counts,
        per_field_sources=per_field_sources,
    )
