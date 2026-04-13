"""Stale data load protocol — degradation detection, rerun, and recovery.

Walks a configurable ladder of steps (rerun → samplescan → sampleswitch)
to resolve pipeline-degraded cached results.  Hyperparameters are read
from the ``l1_score`` optimizer node config.
"""

from __future__ import annotations

import logging
from collections.abc import Mapping
from dataclasses import replace
from typing import TYPE_CHECKING, Any

from promptpotter.application.scoring.metrics import find_rank
from promptpotter.application.scoring.sample_measurement import measure_sample

if TYPE_CHECKING:
    from promptpotter.domain.scoring import ScoringEnv

logger = logging.getLogger(__name__)

__all__ = ["execute_stale_data_protocol", "is_degraded"]


def is_degraded(result: Mapping[str, Any]) -> bool:
    """Check if a result has pipeline degradation warnings."""
    return bool((result.get("pipeline_data") or {}).get("diagnostics", {}).get("warnings"))


def find_gt_rank(result: Mapping[str, Any]) -> int | None:
    """Find ground truth rank in final_ranking. Returns 1-indexed or None."""
    gt = result.get("ground_truth", "")
    if not gt:
        return None
    pd = result.get("pipeline_data") or {}
    return find_rank(pd.get("final_ranking", []), gt)


def compare_rerun(cached_result: Mapping[str, Any], rerun_result: Mapping[str, Any]) -> dict:
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
    cached_result: dict[str, Any],
    env: ScoringEnv,
    *,
    pipeline_params: dict | None = None,
) -> tuple[dict[str, Any], str]:
    """Walk the stale data load protocol ladder for a degraded cached query.

    Hyperparameters are read from the ``l1_score`` node config in
    ``optimizer_pipeline.json``.  The protocol step list and all thresholds
    live on that single node — tunable via ``param_keys`` when PromptPotter
    self-optimizes.

    Observation counts come from SearchMemory (``query_degradation_count``),
    which ingests from dataset_runs at round boundaries. Within a round the
    count is constant; no mutable state is passed through the scorer.

    Returns ``(result_dict, step_taken)`` where *step_taken* is the step
    that resolved the query, or ``"exhausted"``.
    """
    from promptpotter.application.optimization.pipeline import get_optimizer_schema

    node = get_optimizer_schema().get_node("l1_score")
    assert node is not None, "l1_score node missing from optimizer schema"
    cfg = node.current_config
    query = query_data["query"]
    result = cached_result

    # Samplescan bypasses the suffix cache — it's a fresh probe whose job
    # is to detect whether the degraded state is reproducible.
    bypass_env = replace(env, store=None)

    for step in protocol_steps:
        if env.stop_check and env.stop_check():
            return {**result, "cached": result.get("cached", False)}, "interrupted"
        if step == "rerun":
            trigger_count = cfg.get("rerun_trigger_count", 3)
            # Historical count through last SearchMemory refresh (previous round).
            # The current observation bumps the effective count by 1.
            historical = (
                env.search_memory.query_degradation_count(query) if env.search_memory else 0
            )
            effective_count = historical + 1
            if effective_count < trigger_count:
                return {
                    **cached_result,
                    "cached": cached_result.get("cached", False),
                    "degraded_observed": True,
                    "degraded_obs_count": effective_count,
                    "degraded_obs_threshold": trigger_count,
                }, "below_threshold"

            result = dict(await measure_sample(query_data, env, pipeline_params=pipeline_params))
            result["retry_of_degraded"] = True
            result["rerun_comparison"] = compare_rerun(cached_result, result)
            if not is_degraded(result):
                return result, "rerun"

        elif step == "samplescan":
            n_candidates = cfg.get("samplescan_candidates", 3)
            resolved_threshold = cfg.get("samplescan_threshold", 0.5)

            result = dict(await measure_sample(query_data, bypass_env, pipeline_params=None))
            result["samplescan_probe"] = True
            result["samplescan_config"] = {
                "n_candidates": n_candidates,
                "resolved_threshold": resolved_threshold,
            }
            if not is_degraded(result):
                return result, "samplescan"

        elif step == "sampleswitch":
            min_deg_rate = cfg.get("sampleswitch_min_degradation_rate", 0.5)
            if env.search_memory:
                deg_rate = env.search_memory.query_degradation_rate(query)
                if deg_rate >= min_deg_rate:
                    result = {**cached_result, "cached": True, "switched_out": True}
                    return result, "sampleswitch"

    return {**result, "persistently_degraded": True}, "exhausted"
