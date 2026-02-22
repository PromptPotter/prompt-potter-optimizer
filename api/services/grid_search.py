"""
Grid search service for prompt landscape exploration.

Builds cartesian products of prompt field variants, evaluates each
combination, and analyzes results to find optimal configurations.
"""
import itertools
import json
import math
import random
from collections import defaultdict
from typing import Any, Callable, Dict, List, Optional, Tuple

import pandas as pd

from api.models.prompt_state import PromptState
from api.services.llm_client import LLMClientBase
from api.services.project_store import ProjectStore
from api.services.prompt_eval import (
    backend_reranker_eval,
    compute_accuracy,
    eval_cache_key,
    build_dataset_run_data,
    make_incremental_writer,
)


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

DEFAULT_GRID_AXES: Dict[str, List[str]] = {
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

GRID_SEARCHABLE_FIELDS = {
    "persona", "task_intent", "problem_description",
    "instruction", "thinking_style", "answer_format",
}

SAMPLING_ALPHA = 3.0

REQUIRED_TEMPLATE_VARS = {"{{core_concept}}", "{{entity_profile_json}}", "{{matches}}"}


# ---------------------------------------------------------------------------
# Validation & combination building
# ---------------------------------------------------------------------------


def validate_grid_config(
    grid_config: dict,
    baseline_ps: PromptState,
    n_combos: int = 0,
) -> dict:
    """Validate grid axes and compute cartesian product size.

    Args:
        grid_config: Dict mapping field names to lists of variant values.
        baseline_ps: The baseline PromptState (for reference).
        n_combos: If >0, planned sample size (used for capped reporting).

    Returns:
        Metadata dict: {axes, axis_names, total, actual_count, is_subsampled,
        capped}.

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

    actual_count = min(n_combos, total) if n_combos > 0 else total
    capped = n_combos > 0 and n_combos >= total

    return {
        "axes": axes,
        "axis_names": axis_names,
        "total": total,
        "actual_count": actual_count,
        "is_subsampled": n_combos > 0 and n_combos < total,
        "capped": capped,
    }


def _combo_distance(combo: tuple) -> int:
    """Count non-empty field values in a combination."""
    return sum(1 for v in combo if v)


def _allocate_budget(
    buckets: Dict[int, list],
    n_combos: int,
    exploration_rate: float,
    max_distance: int,
) -> Dict[int, int]:
    """Allocate budget across distance bands using weighted sampling.

    Weight function: w(d) = exp(-alpha * |d - target| / max_d)
    where target = exploration_rate * max_distance.
    Uses largest-remainder method to ensure exact n_combos total.

    Args:
        buckets: Dict mapping distance -> list of combos at that distance.
        n_combos: Total budget to allocate.
        exploration_rate: 0.0 (conservative/low distance) to 1.0 (aggressive/high distance).
        max_distance: Maximum possible distance.

    Returns:
        Dict mapping distance -> number of combos to sample from that band.
    """
    if max_distance == 0:
        # All combos have the same distance; distribute evenly
        for d in buckets:
            return {d: min(n_combos, len(buckets[d]))}

    target = exploration_rate * max_distance

    # Compute raw weights
    raw_weights: Dict[int, float] = {}
    for d in buckets:
        raw_weights[d] = math.exp(-SAMPLING_ALPHA * abs(d - target) / max_distance)

    total_weight = sum(raw_weights.values())
    if total_weight == 0:
        total_weight = 1.0

    # Ideal (fractional) allocation, capped at bucket size
    ideal: Dict[int, float] = {}
    for d in buckets:
        ideal[d] = min(
            (raw_weights[d] / total_weight) * n_combos,
            len(buckets[d]),
        )

    # Largest-remainder method
    floored = {d: int(v) for d, v in ideal.items()}
    remainders = {d: ideal[d] - floored[d] for d in ideal}
    allocated = sum(floored.values())
    deficit = n_combos - allocated

    # Sort by remainder descending, break ties by distance
    sorted_ds = sorted(remainders, key=lambda d: (-remainders[d], d))
    for d in sorted_ds:
        if deficit <= 0:
            break
        room = len(buckets[d]) - floored[d]
        if room > 0:
            floored[d] += 1
            deficit -= 1

    return floored


def build_grid_combinations(
    grid_config: dict,
    baseline_ps: PromptState,
    n_combos: int = 0,
    exploration_rate: float = 0.5,
    seed: int = 42,
) -> Tuple[list, dict, dict]:
    """Build cartesian product of grid axes as PromptState variants.

    Uses distance-weighted stratified sampling when ``n_combos > 0``.
    Distance = number of non-empty field values in a combination.
    ``exploration_rate`` biases sampling toward low-distance (conservative)
    or high-distance (aggressive) bands.

    Args:
        grid_config: Dict mapping field names to lists of variant values.
        baseline_ps: The baseline PromptState to derive from.
        n_combos: If >0, sample exactly this many combos (capped at full
            space size). 0 = full grid (backward compat).
        exploration_rate: 0.0 = favor low-distance combos (conservative),
            1.0 = favor high-distance combos (aggressive). Default 0.5.
        seed: Random seed for reproducible sampling.

    Returns:
        Tuple of (combinations, ps_lookup, sampling_meta).
        combinations: list of (coord_dict, ps_id).
        ps_lookup: dict mapping ps_id -> PromptState.
        sampling_meta: dict with total_space, n_selected, exploration_rate,
            distance_distribution, capped.
    """
    axis_names = list(grid_config.keys())
    axis_values = [grid_config[name] for name in axis_names]
    all_combos = list(itertools.product(*axis_values))
    total_space = len(all_combos)

    capped = False
    if n_combos > 0 and n_combos < total_space:
        # Bucket by distance
        buckets: Dict[int, list] = defaultdict(list)
        for combo in all_combos:
            d = _combo_distance(combo)
            buckets[d].append(combo)

        max_distance = max(buckets.keys()) if buckets else 0

        allocation = _allocate_budget(buckets, n_combos, exploration_rate, max_distance)

        rng = random.Random(seed)
        selected = []
        distance_distribution: Dict[int, int] = {}
        for d in sorted(allocation):
            count = allocation[d]
            pool = buckets[d]
            if count >= len(pool):
                sampled = pool
            else:
                sampled = rng.sample(pool, count)
            selected.extend(sampled)
            distance_distribution[d] = len(sampled)

        all_combos = selected
    elif n_combos > 0:
        # n_combos >= total_space: use full grid
        capped = True
        distance_distribution = {}
        buckets_for_meta: Dict[int, list] = defaultdict(list)
        for combo in all_combos:
            d = _combo_distance(combo)
            buckets_for_meta[d].append(combo)
        for d in sorted(buckets_for_meta):
            distance_distribution[d] = len(buckets_for_meta[d])
    else:
        # Full grid mode
        distance_distribution = {}
        buckets_for_meta = defaultdict(list)
        for combo in all_combos:
            d = _combo_distance(combo)
            buckets_for_meta[d].append(combo)
        for d in sorted(buckets_for_meta):
            distance_distribution[d] = len(buckets_for_meta[d])

    combinations = []
    ps_lookup = {}

    for combo in all_combos:
        coord_dict = {}
        changes = {}
        labels = []

        for i, (name, value) in enumerate(zip(axis_names, combo)):
            idx = grid_config[name].index(value)
            coord_dict[name] = idx
            labels.append(f"{name[:2]}={idx}")
            if value:
                changes[name] = value

        desc = f"grid[{','.join(labels)}]"
        ps = baseline_ps.derive(**changes, changes_description=desc)
        combinations.append((coord_dict, ps.id))
        ps_lookup[ps.id] = ps

    sampling_meta = {
        "total_space": total_space,
        "n_selected": len(combinations),
        "exploration_rate": exploration_rate,
        "distance_distribution": distance_distribution,
        "capped": capped,
    }

    return combinations, ps_lookup, sampling_meta


# ---------------------------------------------------------------------------
# LLM-assisted context restructuring
# ---------------------------------------------------------------------------


async def restructure_context(
    context_input: Any,
    llm_client: LLMClientBase,
    model: Optional[str] = None,
    improvement_areas: str = "",
) -> dict:
    """LLM-assisted restructuring of user context into Layer 1 fields.

    Args:
        context_input: Either a string (raw context) or a dict of partial
            Layer 1 fields.
        llm_client: LLM client implementing LLMClientBase.
        model: Model identifier (uses client default if None).
        improvement_areas: Optional domain-expert observations about where
            improvement is most likely (e.g. "profile schema quality, web
            search relevance"). When non-empty, the LLM also returns a
            ``consultation`` key with strategic advice.

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
# Grid search execution & analysis
# ---------------------------------------------------------------------------


async def run_grid_search(
    combinations: list,
    ps_lookup: dict,
    eval_data: list,
    backend_client,
    on_combo_done: Optional[Callable] = None,
    on_query_done: Optional[Callable] = None,
    request_delay: float = 1.0,
    store: Optional["ProjectStore"] = None,
    backend_id: str = "",
    session_terms: Optional[list] = None,
    pipeline_params: Optional[dict] = None,
) -> pd.DataFrame:
    """Evaluate each grid combination on eval_data via the backend.

    Args:
        combinations: List of (coord_dict, ps_id) tuples.
        ps_lookup: Dict mapping ps_id -> PromptState.
        eval_data: List of query dicts with ``query`` and ``ground_truth``.
        backend_client: BackendClient for backend-driven evaluation.
            Each combo is evaluated by calling the backend's ``/matches``
            endpoint with the rendered prompt.
        on_combo_done: Optional callback ``(combo_index, row_data)`` called
            after each combination is evaluated.
        on_query_done: Optional callback
            ``(combo_index, query_index, total_queries, result)`` called after
            each individual query within a combo evaluation.
        request_delay: Seconds to sleep between backend calls within each
            combination (default 1.0).
        store: Optional ProjectStore for per-combo eval caching.
        backend_id: Required when store is provided.
        session_terms: Session terms to initialize the backend session.
        pipeline_params: Optional pipeline parameter overrides forwarded to
            the backend's ``/matches`` endpoint.

    Returns:
        DataFrame with columns: axis indices, prompt_state_id,
        hits, total, accuracy, errors. Sorted by accuracy desc.
    """
    import asyncio

    # Initialize backend session once (not per combo)
    if session_terms:
        await backend_client.init_session(session_terms)

    rows = []

    for combo_idx, (coord_dict, ps_id) in enumerate(combinations):
        ps = ps_lookup[ps_id]
        rendered = ps.render()
        content_hash = eval_cache_key(rendered, eval_data, "", 0.0)

        # Check cache
        cached_run = None
        if store and backend_id:
            cached_run = store.load_dataset_run_by_hash(backend_id, content_hash)

        if cached_run:
            acc = cached_run["scores"]
        else:
            run_id = f"grid_{content_hash[:8]}"

            # Partial resume
            partial = (
                store.load_partial_eval(backend_id, run_id)
                if store and backend_id
                else []
            )
            if partial and len(partial) < len(eval_data):
                remaining = eval_data[len(partial):]
            elif partial and len(partial) >= len(eval_data):
                remaining = []
            else:
                partial = []
                remaining = eval_data

            writer = (
                make_incremental_writer(store, backend_id, run_id)
                if store and backend_id
                else None
            )

            # Evaluate remaining queries via backend
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
                        combo_idx, len(partial) + qi, len(eval_data), result,
                    )

                await asyncio.sleep(request_delay)

            results = partial + new_results
            acc = compute_accuracy(results)

            # Finalize: save complete run, delete .partial.jsonl
            if store and backend_id:
                run_data = build_dataset_run_data(
                    run_id, f"grid_combo_{combo_idx}", content_hash,
                    ps_id, rendered, "", 0.0, acc, results,
                )
                store.finalize_eval_run(backend_id, run_id, run_data)

        row = dict(coord_dict)
        row["prompt_state_id"] = ps_id
        row["hits"] = acc["hits"]
        row["total"] = acc["total"]
        row["accuracy"] = acc["accuracy"]
        row["errors"] = acc["errors"]
        rows.append(row)

        if on_combo_done is not None:
            on_combo_done(combo_idx, row)

    df = pd.DataFrame(rows)
    df = df.sort_values("accuracy", ascending=False).reset_index(drop=True)
    return df


async def analyze_grid_results(
    grid_df: pd.DataFrame,
    grid_config: dict,
    llm_client: LLMClientBase,
    model: Optional[str] = None,
) -> dict:
    """LLM analysis of grid search results.

    Args:
        grid_df: DataFrame from run_grid_search().
        grid_config: Dict mapping field names to lists of variant values.
        llm_client: LLM client implementing LLMClientBase.
        model: Model identifier (uses client default if None).

    Returns:
        Dict with keys: key_findings, strongest_fields, recommended_focus,
        campaign_advice.
    """
    axis_names = list(grid_config.keys())

    top_combos = grid_df.head(5).to_dict("records")
    worst_combos = grid_df.tail(3).to_dict("records")

    marginals = {}
    for name in axis_names:
        marginal = grid_df.groupby(name)["accuracy"].mean()
        marginals[name] = {
            int(idx): {
                "accuracy": float(acc),
                "label": grid_config[name][idx][:80] if grid_config[name][idx] else "(empty)",
            }
            for idx, acc in marginal.items()
        }

    analysis_prompt = (
        "You are an optimization advisor. Analyze the results of a grid search "
        "over prompt configuration fields.\n\n"
        f"GRID AXES: {axis_names}\n"
        f"TOTAL COMBINATIONS: {len(grid_df)}\n\n"
        f"TOP 5 COMBINATIONS:\n{json.dumps(top_combos, indent=2, default=str)}\n\n"
        f"WORST 3 COMBINATIONS:\n{json.dumps(worst_combos, indent=2, default=str)}\n\n"
        f"MARGINAL STATS (mean accuracy per axis value):\n"
        f"{json.dumps(marginals, indent=2, default=str)}\n\n"
        "Return a JSON object with:\n"
        '- "key_findings": array of 3-5 concise findings\n'
        '- "strongest_fields": array of field names that matter most for accuracy\n'
        '- "recommended_focus": which fields to prioritize in optimization\n'
        '- "campaign_advice": 2-3 sentence advice for the optimization campaign'
    )

    response = await llm_client.chat(
        messages=[{"role": "user", "content": analysis_prompt}],
        model=model,
        temperature=0,
        max_tokens=2000,
        output_format="json",
    )
    return response.parsed or json.loads(response.content)


def select_grid_winner(
    grid_df: pd.DataFrame,
    ps_lookup: dict,
) -> dict:
    """Select the best-performing grid combination.

    Args:
        grid_df: DataFrame from run_grid_search() (sorted by accuracy desc).
        ps_lookup: Dict mapping ps_id -> PromptState.

    Returns:
        Campaign round entry dict with keys: round, label, prompt_state,
        accuracy, hits, total, results.
    """
    best_row = grid_df.iloc[0]
    ps_id = best_row["prompt_state_id"]
    ps = ps_lookup[ps_id]

    return {
        "round": "grid",
        "label": f"grid_winner ({ps.changes_description or ps_id[:12]})",
        "prompt_state": ps,
        "accuracy": best_row["accuracy"],
        "hits": int(best_row["hits"]),
        "total": int(best_row["total"]),
        "results": [],
    }


def _extract_eval_from_traces(exp_data: dict) -> list:
    """Build eval data from Langfuse-style traces in synced experiment data.

    Each trace in ``runs[0].traces[]`` carries named observations with
    pipeline step outputs.  Ground truth is joined from ``mappings[]``
    via the ``bom_material`` extracted from the query string.

    Returns:
        List of eval-data dicts (may be empty).
    """
    # Build bom_material -> ground truth lookup from mappings
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

        # Parse bom_material from query (same split logic as extract_replay_queries)
        if "/" in query:
            bom_material = query[: query.rfind("/")].strip()
        else:
            bom_material = query.strip()

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
    """Load per-query evaluation data from synced experiments or replay cache.

    Priority:
        1. Langfuse-style traces from ``runs[0].traces[]``
        2. Replay cache from existing executions
        3. Empty list if neither found

    Args:
        store: ProjectStore instance.
        backend_id: Backend identifier.
        experiment_id: Experiment identifier.
        query_limit: If >0, sample this many queries.

    Returns:
        List of query dicts with keys: query, ground_truth, pipeline_data,
        status.
    """
    exp_data = store.load_sync(backend_id, f"experiments/{experiment_id}.json")

    # Priority 1: traces from synced experiment data
    if exp_data:
        eval_data = _extract_eval_from_traces(exp_data)
        if eval_data:
            if query_limit > 0 and len(eval_data) > query_limit:
                rng = random.Random(42)
                eval_data = rng.sample(eval_data, query_limit)
            return eval_data

    # Priority 2: replay cache from existing executions
    executions = store.list_executions(backend_id)
    for ex_summary in executions:
        if ex_summary.get("experiment_id") == experiment_id:
            execution = store.load_execution(backend_id, ex_summary["execution_id"])
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
