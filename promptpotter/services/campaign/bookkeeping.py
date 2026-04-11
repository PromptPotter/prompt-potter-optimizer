"""Round bookkeeping — query flips, SearchMemory refresh, adaptive eval.

Extracted from the round loop to keep ``runner.py`` focused on control flow.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from promptpotter.services.campaign.nodes.round_execution import adapt_eval_set
from promptpotter.services.metrics import compile_query_difficulty

if TYPE_CHECKING:
    from promptpotter.services.campaign.config import LoopConfig
    from promptpotter.services.campaign.state import LoopState

logger = logging.getLogger(__name__)


def record_query_flips(state: LoopState, round_num: int) -> None:
    """Record query flips into SearchMemory for improvement attribution."""
    if not state.search_memory or len(state.rounds) < 2:
        return
    prev_round = state.rounds[-2]
    curr_round = state.rounds[-1]
    if not prev_round.results or not curr_round.results:
        return
    desc = (
        curr_round.candidate_scores[0].get("changes_description", "")
        if curr_round.candidate_scores
        else ""
    )
    _flips = state.search_memory.record_query_flips(
        round_num,
        desc,
        prev_round.results,
        curr_round.results,
    )
    if _flips:
        logger.debug("Round %d: %d query flips recorded", round_num, _flips)


def refresh_search_memory(
    state: LoopState,
    config: LoopConfig,
    round_num: int,
) -> None:
    """Refresh SearchMemory from latest dataset runs, save, recompute correlations."""
    if not state.search_memory or not state.scoring_ctx or not state.scoring_ctx.store:
        return

    from pathlib import Path

    _sm_store = state.scoring_ctx.store
    if state.search_memory.refresh(_sm_store, config.backend_id):
        # Periodically recompute failure group correlations
        if (
            round_num > 0
            and round_num % 5 == 0
            and state.search_memory.recompute_failure_group_correlations()
        ):
            logger.info(
                "SearchMemory: recomputed failure group correlations at round %d",
                round_num,
            )
        _sm_path = Path(_sm_store.base_dir) / config.backend_id / "search_memory.json"
        state.search_memory.save(_sm_path)


def try_adapt_eval_set(
    state: LoopState,
    full_dataset: list,
    config: LoopConfig,
    round_num: int,
) -> None:
    """Swap dead queries for discriminating ones based on round history."""
    if round_num < 2:
        return
    _hist = [r.results for r in state.rounds if r.results]
    if len(_hist) < 3:
        return
    _qd = compile_query_difficulty(_hist)
    state.eval_dataset, _adapt = adapt_eval_set(
        state.eval_dataset,
        _qd,
        full_dataset,
        seed=config.seed + round_num,
    )
    if not _adapt.get("unchanged"):
        logger.info("Adaptive eval: %s", _adapt)
