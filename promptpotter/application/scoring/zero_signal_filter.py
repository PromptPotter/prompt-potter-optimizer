"""Zero-signal sample filtering — deterministic dataset pruning.

Queries that are always-hit or always-miss across every SearchPoint tried
carry no optimization signal. Running them burns backend calls and
contributes nothing to candidate ranking.  This module sweeps the active
dataset at round boundaries, physically moves such queries into the
``excluded`` sidelist on disk, and mutates the in-memory dataset list so
the *next* round loads the smaller active set.

No LLM, no statistics beyond Bernoulli-variance gating.  Uses
``SearchMemory.dead_queries()`` which already tracks per-query hit
sequences.  Off by default — enable via ``CampaignConfig.optimization.zero_signal_filter_*``.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from promptpotter.application.intelligence.search_memory import SearchMemory
    from promptpotter.infrastructure.store import Stores

logger = logging.getLogger(__name__)

__all__ = ["apply_zero_signal_exclusions"]


def apply_zero_signal_exclusions(
    *,
    store: Stores,
    dataset_name: str,
    memory: SearchMemory,
    active_dataset: list[dict[str, Any]],
    min_observations: int,
    campaign_id: str = "",
) -> list[dict[str, Any]]:
    """Prune zero-signal queries from the active dataset and persist.

    Returns the list of excluded query records (possibly empty). The
    in-memory ``active_dataset`` list is mutated in place so the next
    round's loop iterates the smaller set.

    The caller is responsible for surfacing the result to the UI
    (``RunListener.on_phase`` or similar).
    """
    dead = memory.dead_queries(min_observations=min_observations)
    if not dead:
        return []

    active_queries = {d.get("query", "") for d in active_dataset}
    new_exclusions = [d for d in dead if d.query in active_queries]
    if not new_exclusions:
        return []

    exclusion_payload = [
        {
            "query": d.query,
            "reason": "zero_signal",
            "hit_rate": d.hit_rate,
            "observations": d.n_measurements,
            "campaign_id": campaign_id,
        }
        for d in new_exclusions
    ]

    moved = store.backends.exclude_dataset_items(
        dataset_name,
        exclusion_payload,
    )
    if moved == 0:
        return []

    excluded_set = {e["query"] for e in exclusion_payload}
    active_dataset[:] = [d for d in active_dataset if d.get("query", "") not in excluded_set]

    logger.info(
        "Zero-signal filter excluded %d samples from dataset %r (min_observations=%d, campaign=%s)",
        moved,
        dataset_name,
        min_observations,
        campaign_id or "-",
    )
    return exclusion_payload
