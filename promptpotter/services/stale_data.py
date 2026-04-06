"""Stale data load protocol — degradation detection, rerun, and recovery.

Walks a configurable ladder of steps (rerun → samplescan → sampleswitch)
to resolve pipeline-degraded cached results.  Hyperparameters are read
from the ``l1_evaluate`` optimizer node config.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from typing import TYPE_CHECKING

from promptpotter.services.eval_query import eval_query_via_backend
from promptpotter.services.metrics import find_rank

if TYPE_CHECKING:
    from promptpotter.models.pipeline_schema import PipelineSchema
    from promptpotter.services.backend_client import BackendClient
    from promptpotter.services.search.search_memory import SearchMemory
    from promptpotter.services.stores.intermediate_cache import IntermediateCache

logger = logging.getLogger(__name__)

__all__ = ["execute_stale_data_protocol", "is_degraded"]


def is_degraded(result: dict) -> bool:
    """Check if a result has pipeline degradation warnings."""
    return bool((result.get("pipeline_data") or {}).get("diagnostics", {}).get("warnings"))


def find_gt_rank(result: dict) -> int | None:
    """Find ground truth rank in final_ranking. Returns 1-indexed or None."""
    gt = result.get("ground_truth", "")
    if not gt:
        return None
    pd = result.get("pipeline_data") or {}
    return find_rank(pd.get("final_ranking", []), gt)


def compare_rerun(cached_result: dict, rerun_result: dict) -> dict:
    """Compare rerun to cached result. Returns improvement summary."""
    cached_hit = cached_result.get("hit", False)
    rerun_hit = rerun_result.get("hit", False)
    hit_change = f"{'HIT' if cached_hit else 'MISS'}->{'HIT' if rerun_hit else 'MISS'}"

    cached_rank = find_gt_rank(cached_result)
    rerun_rank = find_gt_rank(rerun_result)
    rank_change = (
        f"{cached_rank}->{rerun_rank}"
        if cached_rank is not None and rerun_rank is not None
        else None
    )

    improved = (not cached_hit and rerun_hit) or (
        cached_rank is not None and rerun_rank is not None and rerun_rank < cached_rank
    )

    return {"hit_change": hit_change, "rank_change": rank_change, "improved": improved}


async def execute_stale_data_protocol(
    protocol_steps: list[str],
    query_data: dict,
    cached_result: dict,
    backend_client: BackendClient,
    *,
    pipeline_params: dict | None = None,
    pipeline_schema: PipelineSchema | None = None,
    intermediate_cache: IntermediateCache | None = None,
    backend_id: str = "",
    search_memory: SearchMemory | None = None,
    stale_data_observations: dict[str, int | dict] | None = None,
    stop_check: Callable[[], bool] | None = None,
) -> tuple[dict, str]:
    """Walk the stale data load protocol ladder for a degraded cached query.

    Hyperparameters are read from the ``l1_evaluate`` node config in
    ``optimizer_pipeline.json``.  The protocol step list and all thresholds
    live on that single node — tunable via ``param_keys`` when PromptPotter
    self-optimizes.

    Returns ``(result_dict, step_taken)`` where *step_taken* is the step
    that resolved the query, or ``"exhausted"``.
    """
    from promptpotter.config.optimizer_pipeline import get_optimizer_schema

    cfg = get_optimizer_schema().get_node("l1_evaluate").current_config
    query = query_data["query"]
    result = cached_result

    for step in protocol_steps:
        if stop_check and stop_check():
            return {**result, "cached": result.get("cached", False)}, "interrupted"
        if step == "rerun":
            trigger_count = cfg.get("rerun_trigger_count", 3)
            obs_entry = (stale_data_observations or {}).get(query, 0)
            obs_count = obs_entry.get("obs_count", 0) if isinstance(obs_entry, dict) else obs_entry
            last_rerun = obs_entry.get("last_rerun") if isinstance(obs_entry, dict) else None
            if obs_count < trigger_count:
                if stale_data_observations is not None:
                    stale_data_observations[query] = {
                        "obs_count": obs_count + 1,
                        "last_rerun": last_rerun,
                    }
                return {
                    **cached_result,
                    "cached": cached_result.get("cached", False),
                    "degraded_observed": True,
                    "degraded_obs_count": obs_count + 1,
                    "degraded_obs_threshold": trigger_count,
                    "rerun_prior_outcome": last_rerun,
                }, "below_threshold"

            result = await eval_query_via_backend(
                query_data,
                backend_client,
                pipeline_params=pipeline_params,
                pipeline_schema=pipeline_schema,
                intermediate_cache=intermediate_cache,
                backend_id=backend_id,
            )
            comparison = compare_rerun(cached_result, result)
            result["retry_of_degraded"] = True
            result["rerun_comparison"] = comparison
            if stale_data_observations is not None:
                stale_data_observations[query] = {
                    "obs_count": obs_count,
                    "last_rerun": comparison,
                }
            if not is_degraded(result):
                if stale_data_observations is not None:
                    stale_data_observations.pop(query, None)
                return result, "rerun"

        elif step == "samplescan":
            n_candidates = cfg.get("samplescan_candidates", 3)
            resolved_threshold = cfg.get("samplescan_threshold", 0.5)

            result = await eval_query_via_backend(
                query_data,
                backend_client,
                pipeline_params=None,
                pipeline_schema=pipeline_schema,
                intermediate_cache=None,
                backend_id=backend_id,
            )
            result["samplescan_probe"] = True
            result["samplescan_config"] = {
                "n_candidates": n_candidates,
                "resolved_threshold": resolved_threshold,
            }
            if not is_degraded(result):
                return result, "samplescan"

        elif step == "sampleswitch":
            min_deg_rate = cfg.get("sampleswitch_min_degradation_rate", 0.5)
            if search_memory:
                deg_rate = search_memory.query_degradation_rate(query)
                if deg_rate >= min_deg_rate:
                    result = {**cached_result, "cached": True, "switched_out": True}
                    return result, "sampleswitch"

    return {**result, "persistently_degraded": True}, "exhausted"
