"""
Grid search service for prompt landscape exploration.

Builds cartesian products of prompt field variants, evaluates each
grid point, and analyzes results to find optimal configurations.
"""

import hashlib
import itertools
import json
import logging
import math
import random
from collections import defaultdict
from typing import Any, Callable, NamedTuple

import pandas as pd

from api.models.prompt_state import PromptState
from api.services.llm_client import LLMClientBase
from api.services.project_store import ProjectStore
from api.services.prompt_eval import (
    backend_reranker_eval,
    build_dataset_run_data,
    compute_accuracy,
    eval_content_hash,
    make_incremental_writer,
)
from api.services.query_utils import parse_bom_material

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

SAMPLING_ALPHA = 3.0
GRIDPLAN_PREFIX = "gridplan_"
GRID_PREFIX = "grid_"

GRID_SEARCHABLE_FIELDS = {
    "persona", "task_intent", "problem_description",
    "instruction", "thinking_style", "answer_format",
}

REQUIRED_TEMPLATE_VARS = {
    "{{core_concept}}", "{{entity_profile_json}}", "{{matches}}",
}

DEFAULT_GRID_AXES: dict[str, list[str]] = {
    "persona": [
        "",
        "You are a domain expert with deep knowledge of this field.",
        "You are a precise, analytical system that evaluates candidates methodically.",
        "You are a careful assistant that considers all options before deciding.",
    ],
    "task_intent": [
        "",
        "Your task is to identify the single best match from the candidates.",
        "Rank candidates by how well they match the concept described.",
    ],
    "thinking_style": [
        "",
        "Think step by step.",
        "Focus on semantic meaning, not surface-level word overlap.",
        "First understand the core concept, then evaluate each candidate against it.",
    ],
    "answer_format": [
        "",
        "Rank all candidates from most to least relevant.",
    ],
}


# ---------------------------------------------------------------------------
# Validation & grid point building
# ---------------------------------------------------------------------------


def validate_grid_config(
    grid_config: dict,
    baseline_ps: PromptState,
    grid_budget: int = 0,
) -> dict:
    """Validate grid axes and compute cartesian product size.

    Returns:
        Metadata dict: {axes, axis_names, total, actual_count,
        is_subsampled, capped}.

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

    if "instruction" in grid_config:
        for i, variant in enumerate(grid_config["instruction"]):
            if not variant:
                continue
            missing = REQUIRED_TEMPLATE_VARS - set(
                var for var in REQUIRED_TEMPLATE_VARS if var in variant
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
    capped = grid_budget > 0 and grid_budget >= total

    return {
        "axes": axes,
        "axis_names": axis_names,
        "total": total,
        "actual_count": actual_count,
        "is_subsampled": grid_budget > 0 and grid_budget < total,
        "capped": capped,
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
        for d in buckets:
            return {d: min(grid_budget, len(buckets[d]))}

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

    capped = False
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
            capped = True
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
        "capped": capped,
    }

    return grid_points, state_lookup, sampling_meta


# ---------------------------------------------------------------------------
# Grid plan persistence
# ---------------------------------------------------------------------------


def grid_plan_identity(
    grid_axes: dict,
    baseline_instruction: str,
    context_input: Any,
    grid_budget: int,
    exploration_rate: float,
    seed: int,
) -> str:
    """Compute a stable identity hash for a grid search plan.

    The hash covers user-controlled inputs only so the same config
    produces the same plan ID across kernel restarts.
    """
    payload = json.dumps(
        {
            "grid_axes": grid_axes,
            "baseline_instruction": baseline_instruction,
            "context_input": context_input
            if isinstance(context_input, str)
            else json.dumps(context_input, sort_keys=True),
            "grid_budget": grid_budget,
            "exploration_rate": exploration_rate,
            "seed": seed,
        },
        sort_keys=True,
    )
    digest = hashlib.sha256(payload.encode()).hexdigest()[:12]
    return f"{GRIDPLAN_PREFIX}{digest}"


def serialize_grid_plan(
    plan_id: str,
    grid_axes: dict,
    baseline_ps: PromptState,
    layer1_fields: dict,
    grid_points: list,
    state_lookup: dict,
    sampling_meta: dict,
) -> dict:
    """Serialize a grid search plan to a JSON-safe dict."""
    return {
        "plan_id": plan_id,
        "status": "in_progress",
        "grid_axes": grid_axes,
        "baseline_ps": baseline_ps.model_dump(),
        "layer1_fields": layer1_fields,
        "grid_points": grid_points,
        "state_lookup": {
            ps_id: ps.model_dump() for ps_id, ps in state_lookup.items()
        },
        "sampling_meta": sampling_meta,
    }


def deserialize_grid_plan(
    plan_data: dict,
) -> tuple:
    """Reconstruct grid plan objects from saved data.

    Returns:
        (grid_points, state_lookup, sampling_meta, grid_axes,
         layer1_fields, baseline_ps)
    """
    baseline_ps = PromptState(**plan_data["baseline_ps"])
    state_lookup = {
        ps_id: PromptState(**ps_data)
        for ps_id, ps_data in plan_data.get(
            "state_lookup", plan_data.get("ps_lookup", {})
        ).items()
    }
    return (
        plan_data.get("grid_points", plan_data.get("combinations", [])),
        state_lookup,
        plan_data["sampling_meta"],
        plan_data["grid_axes"],
        plan_data.get("layer1_fields", plan_data.get("structured_fields", {})),
        baseline_ps,
    )


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
    eval_queries_per_point: int = 0,
    shared_queries: bool = True,
    seed: int = 42,
) -> list[PointEvalInfo]:
    """Compute per-point eval query sets and content hashes.

    Centralizes the sampling logic so ``run_grid_search()``, pre-scan,
    and ``load_grid_plan_results()`` all produce identical content hashes.

    Args:
        grid_points: List of ``(coord_dict, ps_id)`` tuples.
        state_lookup: Dict mapping ``ps_id`` to ``PromptState``.
        eval_data: Full evaluation dataset (list of query dicts).
        eval_queries_per_point: If >0, sample this many queries per point.
        shared_queries: If True, all points share the same query sample.
        seed: Random seed for reproducible sampling.

    Returns:
        List of ``PointEvalInfo`` named tuples with pre-computed
        ``content_hash`` values.
    """
    if eval_queries_per_point > 0 and shared_queries:
        rng = random.Random(seed)
        shared_eval = rng.sample(
            eval_data, min(eval_queries_per_point, len(eval_data)),
        )
    else:
        shared_eval = None

    result = []
    for point_idx, (coord_dict, ps_id) in enumerate(grid_points):
        ps = state_lookup[ps_id]
        rendered = ps.render()

        if eval_queries_per_point > 0 and not shared_queries:
            rng = random.Random(seed + point_idx)
            point_eval = rng.sample(
                eval_data, min(eval_queries_per_point, len(eval_data)),
            )
        elif shared_eval is not None:
            point_eval = shared_eval
        else:
            point_eval = eval_data

        content_hash = eval_content_hash(rendered, point_eval, "", 0.0)
        result.append(PointEvalInfo(
            point_idx, coord_dict, ps_id, point_eval, content_hash,
        ))

    return result


# ---------------------------------------------------------------------------
# LLM-assisted context restructuring
# ---------------------------------------------------------------------------


async def restructure_context(
    context_input: Any,
    llm_client: LLMClientBase,
    model: str | None = None,
    improvement_areas: str = "",
) -> dict:
    """LLM-assisted restructuring of user context into Layer 1 fields.

    Args:
        context_input: Either a string (raw context) or a dict of partial
            Layer 1 fields.
        llm_client: LLM client implementing LLMClientBase.
        model: Model identifier (uses client default if None).
        improvement_areas: Optional domain-expert observations about where
            improvement is most likely.

    Returns:
        Dict of structured Layer 1 field values, plus a ``consultation``
        string when improvement_areas is provided.
    """
    if isinstance(context_input, dict):
        user_content = (
            "The user has provided partial Layer 1 fields for a prompt. "
            "Validate them, fill any gaps, and suggest improvements.\n\n"
            f"Provided fields:\n{json.dumps(context_input, indent=2)}"
        )
    else:
        user_content = (
            "The user has provided a raw context description. Parse it into "
            "structured Layer 1 prompt fields.\n\n"
            f"Context:\n{context_input}"
        )

    if improvement_areas:
        user_content += (
            "\n\nThe user has identified the following areas where improvement "
            "is most likely:\n"
            f"{improvement_areas}\n\n"
            "Take these observations into account when structuring the fields "
            "and provide strategic advice in the consultation field."
        )

    layer1_keys_description = (
        "Layer 1 fields:\n"
        "- persona: Who the LLM should act as (e.g., 'You are a domain expert...')\n"
        "- task_intent: What the prompt needs to accomplish\n"
        "- problem_description: Description of the problem domain\n"
        "- instruction: Core instruction text (may contain template variables)\n"
        "- thinking_style: How to reason (e.g., 'Think step by step')\n"
        "- answer_format: Expected output format\n"
    )

    if improvement_areas:
        system_prompt = (
            "You are a prompt engineering assistant. Your job is to structure "
            "user-provided context into Layer 1 prompt fields for an optimization "
            "campaign.\n\n"
            f"{layer1_keys_description}\n"
            "Return a JSON object with these keys plus a \"consultation\" key. "
            "The consultation value should be a natural-language paragraph of "
            "strategic advice on how to approach optimization given the user's "
            "identified improvement areas. Use empty string for Layer 1 fields "
            "that don't apply. Be concise and actionable."
        )
    else:
        system_prompt = (
            "You are a prompt engineering assistant. Your job is to structure "
            "user-provided context into Layer 1 prompt fields for an optimization "
            "campaign.\n\n"
            f"{layer1_keys_description}\n"
            "Return a JSON object with exactly these keys. Use empty string for "
            "fields that don't apply. Be concise and actionable."
        )

    response = await llm_client.chat(
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_content},
        ],
        model=model,
        temperature=0.3,
        max_tokens=2000,
        output_format="json",
    )
    result = response.parsed or json.loads(response.content)

    for key in ("persona", "task_intent", "problem_description",
                "instruction", "thinking_style", "answer_format"):
        result.setdefault(key, "")

    if improvement_areas:
        result.setdefault("consultation", "")

    return result


# ---------------------------------------------------------------------------
# Grid search execution
# ---------------------------------------------------------------------------


async def _load_or_compute_point(
    info: PointEvalInfo,
    state_lookup: dict,
    backend_client: Any,
    request_delay: float,
    store: ProjectStore | None,
    backend_id: str,
    pipeline_params: dict | None,
    on_query_done: Callable | None,
) -> dict[str, Any]:
    """Evaluate (or load from cache) a single grid point.

    Handles three cases in order:
    1. Full cache hit — return stored scores immediately.
    2. Partial resume — continue from the last written item.
    3. Fresh evaluation — run all queries through the backend.

    Returns:
        Dict with keys ``hits``, ``total``, ``accuracy``, ``errors``.
    """
    import asyncio

    ps = state_lookup[info.ps_id]
    rendered = ps.render()

    # Case 1: full cache hit
    if store and backend_id:
        existing_run = store.load_dataset_run_by_hash(
            backend_id, info.content_hash,
        )
        if existing_run:
            return existing_run["scores"]

    # Determine resume state
    run_id = f"{GRID_PREFIX}{info.content_hash[:8]}"
    partial: list = []
    if store and backend_id:
        partial = store.load_partial_eval(backend_id, run_id)

    if partial and len(partial) >= len(info.point_eval):
        # All queries already evaluated in partial; just need to finalize
        remaining = []
    elif partial:
        remaining = info.point_eval[len(partial):]
    else:
        partial = []
        remaining = info.point_eval

    writer = (
        make_incremental_writer(store, backend_id, run_id)
        if store and backend_id
        else None
    )

    # Case 2/3: evaluate remaining queries
    new_results = []
    for qi, qd in enumerate(remaining):
        result = await backend_reranker_eval(
            qd, backend_client, rendered,
            pipeline_params=pipeline_params,
        )
        new_results.append(result)

        if writer:
            writer(result, qi, len(remaining))
        if on_query_done is not None:
            on_query_done(
                info.point_idx, len(partial) + qi,
                len(info.point_eval), result,
            )

        await asyncio.sleep(request_delay)

    results = partial + new_results
    acc = compute_accuracy(results)

    # Finalize: save complete run, delete .partial.jsonl
    if store and backend_id:
        run_data = build_dataset_run_data(
            run_id, f"grid_point_{info.point_idx}", info.content_hash,
            info.ps_id, rendered, "", 0.0, acc, results,
        )
        store.finalize_eval_run(backend_id, run_id, run_data)

    return acc


async def run_grid_search(
    grid_points: list,
    state_lookup: dict,
    eval_data: list,
    backend_client: Any,
    on_point_done: Callable | None = None,
    on_query_done: Callable | None = None,
    on_point_reused: Callable | None = None,
    request_delay: float = 1.0,
    store: ProjectStore | None = None,
    backend_id: str = "",
    session_terms: list | None = None,
    pipeline_params: dict | None = None,
    eval_queries_per_point: int = 0,
    shared_queries: bool = True,
    seed: int = 42,
) -> pd.DataFrame:
    """Evaluate each grid point on eval_data via the backend.

    Returns:
        DataFrame with columns: axis indices, prompt_state_id,
        hits, total, accuracy, errors. Sorted by accuracy desc.
    """
    if session_terms:
        await backend_client.init_session(session_terms)

    eval_plan = resolve_point_evals(
        grid_points, state_lookup, eval_data,
        eval_queries_per_point, shared_queries, seed,
    )

    rows = []
    for info in eval_plan:
        acc = await _load_or_compute_point(
            info, state_lookup, backend_client, request_delay,
            store, backend_id, pipeline_params, on_query_done,
        )

        # Notify reuse callback when result came from cache
        is_cached = (
            store and backend_id
            and store.load_dataset_run_by_hash(backend_id, info.content_hash)
            is not None
        )

        row = dict(info.coord_dict)
        row["prompt_state_id"] = info.ps_id
        row["hits"] = acc["hits"]
        row["total"] = acc["total"]
        row["accuracy"] = acc["accuracy"]
        row["errors"] = acc["errors"]
        rows.append(row)

        if is_cached and on_point_reused is not None:
            on_point_reused(info.point_idx, row)
        if on_point_done is not None:
            on_point_done(info.point_idx, row)

    df = pd.DataFrame(rows)
    df = df.sort_values("accuracy", ascending=False).reset_index(drop=True)
    return df


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
    best_row = grid_df.iloc[0]
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
# Eval dataset loading
# ---------------------------------------------------------------------------


def _extract_eval_from_traces(exp_data: dict) -> list:
    """Build eval data from Langfuse-style traces in synced experiment data.

    Each trace in ``runs[0].traces[]`` carries named observations with
    pipeline step outputs.  Ground truth is joined from ``mappings[]``
    via the ``bom_material`` extracted from the query string.

    Returns:
        List of eval-data dicts (may be empty).
    """
    bom_to_gt: dict = {}
    for m in exp_data.get("mappings", []):
        bom = m.get("bom_material", "")
        entry = m.get("dataset_entry", "").strip()
        if bom and entry and entry != "--":
            bom_to_gt[bom] = entry

    runs = exp_data.get("runs", [])
    if not runs:
        return []

    traces = runs[0].get("traces", [])
    if not traces:
        return []

    eval_data = []
    for trace in traces:
        query = (trace.get("input") or {}).get("query", "")
        if not query:
            continue

        bom_material, _ = parse_bom_material(query)
        ground_truth = bom_to_gt.get(bom_material)
        if not ground_truth:
            continue

        # Extract pipeline step outputs from observations
        entity_profile = None
        token_matched_candidates = None
        for obs in trace.get("observations", []):
            name = obs.get("name", "")
            output = obs.get("output")
            if not output:
                continue
            if name == "entity_profiling":
                entity_profile = output
            elif name == "token_matching":
                token_matched_candidates = output.get("candidates")

        if not entity_profile:
            continue

        pipeline_data: dict = {"entity_profile": entity_profile}
        if token_matched_candidates is not None:
            pipeline_data["token_matched_candidates"] = token_matched_candidates

        eval_data.append({
            "query": query,
            "ground_truth": ground_truth,
            "pipeline_data": pipeline_data,
            "status": "success",
        })

    return eval_data


def load_eval_dataset(
    store: ProjectStore,
    backend_id: str,
    experiment_id: str,
    query_limit: int = 0,
) -> list:
    """Load per-query evaluation data from synced experiments or stored replays.

    Priority:
        1. Langfuse-style traces from ``runs[0].traces[]``
        2. Stored replay executions
        3. Empty list if neither found
    """
    exp_data = store.load_sync(
        backend_id, f"experiments/{experiment_id}.json",
    )

    if exp_data:
        eval_data = _extract_eval_from_traces(exp_data)
        if eval_data:
            if query_limit > 0 and len(eval_data) > query_limit:
                rng = random.Random(42)
                eval_data = rng.sample(eval_data, query_limit)
            return eval_data

    executions = store.list_executions(backend_id)
    for ex_summary in executions:
        if ex_summary.get("experiment_id") == experiment_id:
            execution = store.load_execution(
                backend_id, ex_summary["execution_id"],
            )
            if execution:
                eval_data = [
                    r.model_dump() for r in execution.results
                    if r.status == "success"
                    and r.pipeline_data
                    and r.pipeline_data.get("entity_profile")
                ]
                if eval_data:
                    if query_limit > 0 and len(eval_data) > query_limit:
                        rng = random.Random(42)
                        eval_data = rng.sample(eval_data, query_limit)
                    return eval_data

    return []
