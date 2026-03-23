"""Grid search: validation, point building, execution, analysis, winner selection.

Builds cartesian products of prompt field variants, evaluates each
grid point, and analyzes results to find optimal configurations.
"""

from __future__ import annotations

import asyncio
import itertools
import json
import logging
import math
import random
from collections import defaultdict
from typing import TYPE_CHECKING, Any, Callable, NamedTuple

if TYPE_CHECKING:
    import pandas as pd

from api.models.prompt_state import LAYER1_STRING_FIELDS, PromptState
from api.services.llm_client import LLMClientBase
from api.services.project_store import ProjectStore
from api.services.search.smart_search import MIN_DIAGNOSTIC_QUERIES

if TYPE_CHECKING:
    from api.models.pipeline_schema import PipelineSchema

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

SAMPLING_ALPHA = 3.0
GRID_PREFIX = "grid_"

GRID_SEARCHABLE_FIELDS = set(LAYER1_STRING_FIELDS)


# ---------------------------------------------------------------------------
# Validation & grid point building
# ---------------------------------------------------------------------------


def validate_grid_config(
    grid_config: dict,
    baseline_ps: PromptState,
    grid_budget: int = 0,
    pipeline_schema: "PipelineSchema | None" = None,
) -> dict:
    """Validate grid axes and compute cartesian product size.

    Returns:
        Metadata dict: {axes, axis_names, total, actual_count,
        is_subsampled, is_uncapped}.

    Raises:
        ValueError: If axis keys are invalid or instruction variants lack
            required template variables.
    """
    axis_names = list(grid_config.keys())
    invalid = set(axis_names) - GRID_SEARCHABLE_FIELDS
    if invalid:
        raise ValueError(
            f"Invalid grid axis fields: {invalid}. "
            f"Must be in {GRID_SEARCHABLE_FIELDS}"
        )

    if "instruction" in grid_config and pipeline_schema is not None:
        required_vars = pipeline_schema.template_variables
        for i, variant in enumerate(grid_config["instruction"]):
            if not variant:
                continue
            missing = required_vars - set(
                var for var in required_vars if var in variant
            )
            if missing:
                raise ValueError(
                    f"instruction variant {i} is missing template variables: "
                    f"{missing}"
                )

    axes = {name: grid_config[name] for name in axis_names}
    total = 1
    for values in axes.values():
        total *= len(values)

    actual_count = min(grid_budget, total) if grid_budget > 0 else total
    is_uncapped = grid_budget > 0 and grid_budget >= total

    return {
        "axes": axes,
        "axis_names": axis_names,
        "total": total,
        "actual_count": actual_count,
        "is_subsampled": grid_budget > 0 and grid_budget < total,
        "is_uncapped": is_uncapped,
    }


def _point_distance(grid_point: tuple) -> int:
    """Count non-empty field values in a grid point."""
    return sum(1 for v in grid_point if v)


def _bucket_by_distance(
    all_points: list[tuple],
) -> dict[int, list]:
    """Group grid points by their distance (non-empty field count)."""
    buckets: dict[int, list] = defaultdict(list)
    for grid_point in all_points:
        buckets[_point_distance(grid_point)].append(grid_point)
    return buckets


def _allocate_budget(
    buckets: dict[int, list],
    grid_budget: int,
    exploration_rate: float,
    max_distance: int,
) -> dict[int, int]:
    """Allocate budget across distance bands using weighted sampling.

    Weight function: ``w(d) = exp(-alpha * |d - target| / max_d)``
    where ``target = exploration_rate * max_distance``.
    Uses largest-remainder method to ensure exact ``grid_budget`` total.
    """
    if max_distance == 0:
        return {0: min(grid_budget, len(buckets.get(0, [])))}

    target = exploration_rate * max_distance

    raw_weights: dict[int, float] = {}
    for d in buckets:
        raw_weights[d] = math.exp(
            -SAMPLING_ALPHA * abs(d - target) / max_distance
        )

    total_weight = sum(raw_weights.values()) or 1.0

    # Ideal (fractional) allocation, capped at bucket size
    ideal: dict[int, float] = {}
    for d in buckets:
        ideal[d] = min(
            (raw_weights[d] / total_weight) * grid_budget,
            len(buckets[d]),
        )

    # Largest-remainder method
    floored = {d: int(v) for d, v in ideal.items()}
    remainders = {d: ideal[d] - floored[d] for d in ideal}
    allocated = sum(floored.values())
    deficit = grid_budget - allocated

    sorted_ds = sorted(remainders, key=lambda d: (-remainders[d], d))
    for d in sorted_ds:
        if deficit <= 0:
            break
        room = len(buckets[d]) - floored[d]
        if room > 0:
            floored[d] += 1
            deficit -= 1

    return floored


def build_grid_points(
    grid_config: dict,
    baseline_ps: PromptState,
    grid_budget: int = 0,
    exploration_rate: float = 0.5,
    seed: int = 42,
) -> tuple[list, dict, dict]:
    """Build cartesian product of grid axes as PromptState variants.

    Uses distance-weighted stratified sampling when ``grid_budget > 0``.
    """
    axis_names = list(grid_config.keys())
    axis_values = [grid_config[name] for name in axis_names]
    all_points = list(itertools.product(*axis_values))
    total_space = len(all_points)

    is_uncapped = False
    if grid_budget > 0 and grid_budget < total_space:
        buckets = _bucket_by_distance(all_points)
        max_distance = max(buckets.keys()) if buckets else 0
        allocation = _allocate_budget(
            buckets, grid_budget, exploration_rate, max_distance,
        )

        rng = random.Random(seed)
        selected = []
        distance_distribution: dict[int, int] = {}
        for d in sorted(allocation):
            count = allocation[d]
            pool = buckets[d]
            sampled = pool if count >= len(pool) else rng.sample(pool, count)
            selected.extend(sampled)
            distance_distribution[d] = len(sampled)

        all_points = selected
    else:
        if grid_budget > 0:
            is_uncapped = True
        buckets = _bucket_by_distance(all_points)
        distance_distribution = {
            d: len(pts) for d, pts in sorted(buckets.items())
        }

    grid_points = []
    state_lookup = {}

    for grid_point in all_points:
        coord_dict = {}
        changes = {}
        labels = []

        for name, value in zip(axis_names, grid_point):
            idx = grid_config[name].index(value)
            coord_dict[name] = idx
            labels.append(f"{name[:2]}={idx}")
            if value:
                changes[name] = value

        desc = f"grid[{','.join(labels)}]"
        ps = baseline_ps.derive(**changes, changes_description=desc)
        grid_points.append((coord_dict, ps.id))
        state_lookup[ps.id] = ps

    sampling_meta = {
        "total_space": total_space,
        "n_selected": len(grid_points),
        "exploration_rate": exploration_rate,
        "distance_distribution": distance_distribution,
        "is_uncapped": is_uncapped,
    }

    return grid_points, state_lookup, sampling_meta


# ---------------------------------------------------------------------------
# Per-point eval resolution (shared by run_grid_search + notebook callers)
# ---------------------------------------------------------------------------


class PointEvalInfo(NamedTuple):
    """Pre-computed eval data and content hash for a single grid point."""
    point_idx: int
    coord_dict: dict
    ps_id: str
    point_eval: list
    content_hash: str


def resolve_point_evals(
    grid_points: list,
    state_lookup: dict,
    eval_data: list,
    sample_size: int = 0,
    shared_queries: bool = True,
    seed: int = 42,
    pipeline_params: dict | None = None,
) -> list[PointEvalInfo]:
    """Compute per-point eval query sets and content hashes.

    Centralizes the sampling logic so ``run_grid_search()``, pre-scan,
    and ``load_grid_plan_results()`` all produce identical content hashes.

    Args:
        grid_points: List of ``(coord_dict, ps_id)`` tuples.
        state_lookup: Dict mapping ``ps_id`` to ``PromptState``.
        eval_data: Full evaluation dataset (list of query dicts).
        sample_size: If >0, sample this many queries per point.
        shared_queries: If True, all points share the same query sample.
        seed: Random seed for reproducible sampling.
        pipeline_params: Optional pipeline parameter overrides included in
            the content hash via SearchPoint.

    Returns:
        List of ``PointEvalInfo`` named tuples with pre-computed
        ``content_hash`` values.
    """
    from api.models.search_point import SearchPoint

    if sample_size > 0 and shared_queries:
        rng = random.Random(seed)
        shared_eval = rng.sample(
            eval_data, min(sample_size, len(eval_data)),
        )
    else:
        shared_eval = None

    result = []
    for point_idx, (coord_dict, ps_id) in enumerate(grid_points):
        ps = state_lookup[ps_id]

        if sample_size > 0 and not shared_queries:
            rng = random.Random(seed + point_idx)
            point_eval = rng.sample(
                eval_data, min(sample_size, len(eval_data)),
            )
        elif shared_eval is not None:
            point_eval = shared_eval
        else:
            point_eval = eval_data

        sp = SearchPoint(prompt_state=ps, pipeline_params=pipeline_params)
        content_hash = sp.content_hash(point_eval)
        result.append(PointEvalInfo(
            point_idx, coord_dict, ps_id, point_eval, content_hash,
        ))

    return result


# ---------------------------------------------------------------------------
# Grid search execution
# ---------------------------------------------------------------------------


def _load_or_compute_point(
    info: PointEvalInfo,
    state_lookup: dict,
    backend_client: Any,
    store: ProjectStore | None,
    backend_id: str,
    pipeline_params: dict | None,
    on_query_done: Callable | None,
    pipeline_schema: "PipelineSchema | None" = None,
    experiment_id: str = "",
) -> tuple[dict[str, Any], bool]:
    """Evaluate (or load from cache) a single grid point.

    Delegates to ``evaluate_prompt_cached()`` for unified caching,
    scoring, and persistence.

    Returns:
        Tuple of (scores dict, was_cached bool).
    """
    from api.models.search_point import SearchPoint
    from api.services.prompt_eval import EvalContext, evaluate_prompt_cached

    ps = state_lookup[info.ps_id]
    sp = SearchPoint(prompt_state=ps, pipeline_params=pipeline_params)

    def _on_result(result: dict, index: int, total: int) -> None:
        if on_query_done is not None:
            on_query_done(info.point_idx, index, total, result)

    ctx = EvalContext(
        backend_client=backend_client,
        store=store,
        backend_id=backend_id,
        pipeline_schema=pipeline_schema,
        source="grid_search",
        experiment_id=experiment_id,
    )

    _results, scores, was_cached = evaluate_prompt_cached(
        sp, info.point_eval, ctx,
        label=f"grid_point_{info.point_idx}",
        on_result=_on_result,
    )
    return scores, was_cached


async def run_grid_search(
    grid_points: list,
    state_lookup: dict,
    eval_data: list,
    backend_client: Any,
    on_point_done: Callable | None = None,
    on_query_done: Callable | None = None,
    on_point_reused: Callable | None = None,
    request_delay: float = 0.0,
    store: ProjectStore | None = None,
    backend_id: str = "",
    session_terms: list | None = None,
    pipeline_params: dict | None = None,
    sample_size: int = 0,
    shared_queries: bool = True,
    seed: int = 42,
    plan_id: str = "",
    pipeline_schema: "PipelineSchema | None" = None,
    experiment_id: str = "",
) -> pd.DataFrame:
    """Evaluate each grid point on eval_data via the backend.

    Returns:
        DataFrame with columns: axis indices, prompt_state_id,
        hits, total, accuracy, errors. Sorted by accuracy desc.
    """
    import pandas as pd
    eval_plan = resolve_point_evals(
        grid_points, state_lookup, eval_data,
        sample_size, shared_queries, seed,
        pipeline_params=pipeline_params,
    )

    # Only init backend session if there are uncached points that need evaluation
    if session_terms and store and backend_id:
        needs_eval = any(
            not store.dataset_runs.load_by_hash(backend_id, info.content_hash)
            for info in eval_plan
        )
        if needs_eval:
            backend_client.init_session(session_terms)
    elif session_terms:
        backend_client.init_session(session_terms)

    rows = []
    try:
        for info in eval_plan:
            acc, was_cached = _load_or_compute_point(
                info, state_lookup, backend_client,
                store, backend_id, pipeline_params, on_query_done,
                pipeline_schema=pipeline_schema,
                experiment_id=experiment_id,
            )

            row = dict(info.coord_dict)
            row["prompt_state_id"] = info.ps_id
            row["hits"] = acc["hits"]
            row["total"] = acc["total"]
            row["accuracy"] = acc["accuracy"]
            row["errors"] = acc["errors"]
            rows.append(row)

            if was_cached and on_point_reused is not None:
                on_point_reused(info.point_idx, row)
            if on_point_done is not None:
                on_point_done(info.point_idx, row)
    except (KeyboardInterrupt, asyncio.CancelledError):
        logger.warning(
            "Grid search interrupted at point %d/%d. "
            "Completed points are saved via evaluate_prompt_cached.",
            len(rows), len(eval_plan),
        )
        raise

    df = pd.DataFrame(rows)
    df = df.sort_values("accuracy", ascending=False).reset_index(drop=True)

    if plan_id and store and backend_id:
        store.grid_plans.update_status(backend_id, plan_id, "completed")

    return df


# ---------------------------------------------------------------------------
# Grid result merging & lookup helpers
# ---------------------------------------------------------------------------


def merge_grid_results(*dataframes: pd.DataFrame) -> pd.DataFrame:
    """Merge multiple grid result DataFrames, keeping best accuracy per prompt_state_id."""
    import pandas as pd

    combined = pd.concat(dataframes, ignore_index=True)
    return (
        combined.sort_values("accuracy", ascending=False)
        .drop_duplicates(subset=["prompt_state_id"], keep="first")
        .sort_values("accuracy", ascending=False)
        .reset_index(drop=True)
    )


def build_combined_state_lookup(
    store: ProjectStore, backend_id: str, plan_ids: list[str],
) -> dict:
    """Build a combined state lookup from multiple grid plans."""
    from api.services.search.plan_persistence import deserialize_grid_plan

    combined: dict = {}
    for pid in plan_ids:
        plan_data = store.grid_plans.load(backend_id, pid)
        if plan_data:
            _, sl, _, _, _, _ = deserialize_grid_plan(plan_data)
            combined.update(sl)
    return combined


# ---------------------------------------------------------------------------
# Grid result analysis
# ---------------------------------------------------------------------------


def build_grid_analysis_prompt(
    grid_df: pd.DataFrame,
    grid_config: dict,
) -> str:
    """Build the LLM prompt for grid result analysis (no LLM call)."""
    axis_names = list(grid_config.keys())

    top_points = grid_df.head(5).to_dict("records")
    worst_points = grid_df.tail(3).to_dict("records")

    marginals = {}
    for name in axis_names:
        marginal = grid_df.groupby(name)["accuracy"].mean()
        marginals[name] = {
            int(idx): {
                "accuracy": float(acc),
                "label": (
                    grid_config[name][idx][:80]
                    if grid_config[name][idx]
                    else "(empty)"
                ),
            }
            for idx, acc in marginal.items()
        }

    return (
        "You are an optimization advisor. Analyze the results of a grid search "
        "over prompt configuration fields.\n\n"
        f"GRID AXES: {axis_names}\n"
        f"TOTAL GRID POINTS: {len(grid_df)}\n\n"
        f"TOP 5 GRID POINTS:\n{json.dumps(top_points, indent=2, default=str)}\n\n"
        f"WORST 3 GRID POINTS:\n{json.dumps(worst_points, indent=2, default=str)}\n\n"
        f"MARGINAL STATS (mean accuracy per axis value):\n"
        f"{json.dumps(marginals, indent=2, default=str)}\n\n"
        "Return a JSON object with:\n"
        '- "key_findings": array of 3-5 concise findings\n'
        '- "strongest_fields": array of field names that matter most for accuracy\n'
        '- "recommended_focus": which fields to prioritize in optimization\n'
        '- "campaign_advice": 2-3 sentence advice for the optimization campaign'
    )


async def analyze_grid_results(
    grid_df: pd.DataFrame,
    grid_config: dict,
    llm_client: LLMClientBase,
    model: str | None = None,
) -> dict:
    """LLM analysis of grid search results."""
    prompt = build_grid_analysis_prompt(grid_df, grid_config)
    response = await llm_client.chat(
        messages=[{"role": "user", "content": prompt}],
        model=model,
        temperature=0,
        max_tokens=2000,
        output_format="json",
    )
    return response.parsed or json.loads(response.content)


def select_grid_winner(
    grid_df: pd.DataFrame,
    state_lookup: dict,
) -> dict:
    """Select the best-performing grid point."""
    if grid_df.empty:
        raise ValueError("Cannot select winner from empty results.")
    best_row = grid_df.iloc[0]
    best_total = int(best_row["total"])
    if best_total < MIN_DIAGNOSTIC_QUERIES:
        logger.warning(
            "Winner based on only %d queries — may be unreliable.", best_total,
        )
    ps_id = best_row["prompt_state_id"]
    ps = state_lookup[ps_id]

    return {
        "round": "grid",
        "label": f"grid_winner ({ps.changes_description or ps_id[:12]})",
        "prompt_state": ps,
        "accuracy": best_row["accuracy"],
        "hits": int(best_row["hits"]),
        "total": int(best_row["total"]),
        "results": [],
    }


# ---------------------------------------------------------------------------
# Grid plan resume / build
# ---------------------------------------------------------------------------


async def resume_or_build_grid(
    campaign_config: dict,
    baseline: PromptState,
    llm_client: LLMClientBase,
    model: str,
    store: "ProjectStore",
    backend_id: str,
    improvement_areas: str = "",
    pipeline_schema: "PipelineSchema | None" = None,
    pipeline_params: dict | None = None,
) -> tuple:
    """Resume an existing grid plan or build a new one.

    Computes a stable plan_id from user-controlled config inputs. If a
    matching plan exists on disk and is not completed, it is deserialized
    and returned (skipping the LLM restructure call). Otherwise, a new
    plan is built, serialized, and saved.

    Returns:
        (plan_id, grid_points, grid_state_lookup, grid_axes,
         layer1_fields, grid_baseline, resumed: bool)
    """
    from api.services.search.context import restructure_context
    from api.services.search.plan_persistence import (
        deserialize_grid_plan,
        grid_plan_identity,
        serialize_grid_plan,
    )

    gs = campaign_config["grid_search"]
    grid_budget = gs.get("grid_budget", 0)
    exploration_rate = campaign_config.get("exploration_rate", 0.5)
    seed = gs.get("seed", 42)
    context_input = gs.get("context_fields", gs.get("context", ""))

    # Build grid axes from variant library prompt_fields
    if gs.get("use_defaults", True):
        from api.config.settings import load_variant_library
        from api.services.search.smart_search import filter_variant_library
        vl = load_variant_library()
        vl = filter_variant_library(vl, pipeline_params, schema=pipeline_schema)
        grid_axes = dict(vl.get("prompt_fields", {}))
    else:
        grid_axes = {}
    if gs.get("custom_axes"):
        grid_axes.update(gs["custom_axes"])

    plan_id = grid_plan_identity(
        grid_axes, baseline.instruction, context_input,
        grid_budget, exploration_rate, seed,
    )

    # Check for existing plan
    existing = store.grid_plans.load(backend_id, plan_id)
    if existing:
        (
            grid_points, grid_state_lookup, sampling_meta,
            grid_axes, layer1_fields, grid_baseline,
        ) = deserialize_grid_plan(existing)
        logger.info(
            "Resuming grid plan %s (status: %s, %d points)",
            plan_id, existing.get("status", "?"), len(grid_points),
        )
        return (
            plan_id, grid_points, grid_state_lookup,
            grid_axes, layer1_fields, grid_baseline, True,
        )

    # Build new plan: LLM restructure + grid points
    logger.info("Building new grid plan: %s", plan_id)
    layer1_fields = await restructure_context(
        context_input, llm_client,
        model=model,
        improvement_areas=improvement_areas,
    )

    grid_baseline = baseline.derive(
        **{k: v for k, v in layer1_fields.items() if v and k != "consultation"},
        changes_description="grid_baseline",
    )

    validate_grid_config(
        grid_axes, grid_baseline, grid_budget=grid_budget,
        pipeline_schema=pipeline_schema,
    )
    points, state_lookup, sampling_meta = build_grid_points(
        grid_axes, grid_baseline,
        grid_budget=grid_budget,
        exploration_rate=exploration_rate,
        seed=seed,
    )

    # Persist
    plan_data = serialize_grid_plan(
        plan_id, grid_axes, grid_baseline, layer1_fields,
        points, state_lookup, sampling_meta,
    )
    store.grid_plans.save(backend_id, plan_id, plan_data)

    return (
        plan_id, points, state_lookup,
        grid_axes, layer1_fields, grid_baseline, False,
    )


def load_grid_plan_results(
    store: "ProjectStore",
    backend_id: str,
    plan_id: str,
    eval_data: list,
    sample_size: int = 0,
    shared_queries: bool = True,
    seed: int = 42,
    pipeline_params: dict | None = None,
) -> pd.DataFrame | None:
    """Load stored eval results for a grid plan and return a results DataFrame.

    Rebuilds the same DataFrame shape that ``run_grid_search`` returns,
    but purely from stored data (no backend calls).  Returns None if
    the plan doesn't exist or has no stored results.

    Must receive the same eval sampling params as the grid search that
    produced the runs, so that content hashes match.
    """
    import pandas as pd
    from api.services.search.plan_persistence import deserialize_grid_plan

    plan_data = store.grid_plans.load(backend_id, plan_id)
    if not plan_data:
        return None

    grid_points, state_lookup, _, grid_axes, _, _ = deserialize_grid_plan(plan_data)

    eval_plan = resolve_point_evals(
        grid_points, state_lookup, eval_data,
        sample_size, shared_queries, seed,
        pipeline_params=pipeline_params,
    )
    rows = []
    for info in eval_plan:
        existing = store.dataset_runs.load_by_hash(backend_id, info.content_hash)
        if not existing:
            continue
        scores = existing.get("scores", {})
        row = dict(info.coord_dict) if isinstance(info.coord_dict, dict) else {}
        row.update({
            "prompt_state_id": info.ps_id,
            "accuracy": scores.get("accuracy", 0),
            "hits": scores.get("hits", 0),
            "total": scores.get("total", 0),
            "errors": scores.get("errors", 0),
        })
        rows.append(row)

    if not rows:
        return None

    return (
        pd.DataFrame(rows)
        .sort_values("accuracy", ascending=False)
        .reset_index(drop=True)
    )
