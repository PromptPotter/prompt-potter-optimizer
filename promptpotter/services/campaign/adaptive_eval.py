"""Adaptive eval set sampling — Wave 1d.

Between optimization rounds, drop dead queries (always hit or always miss)
and replace them with discriminating queries from the full pool.
"""

from __future__ import annotations

import logging
import random
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from promptpotter.models.analysis import QueryDifficulty

logger = logging.getLogger(__name__)

__all__ = ["adapt_eval_set"]

# Never drop more than this fraction of the eval set per adaptation
MAX_DROP_FRACTION = 0.25

# Minimum rounds of history before adaptation kicks in
MIN_ROUNDS_FOR_ADAPTATION = 3


def adapt_eval_set(
    current_eval_data: list[dict],
    query_difficulty: QueryDifficulty,
    full_pool: list[dict],
    *,
    seed: int = 42,
) -> tuple[list[dict], dict]:
    """Replace dead queries with discriminating ones from the full pool.

    Args:
        current_eval_data: Current evaluation subset.
        query_difficulty: Precomputed difficulty classification.
        full_pool: Full evaluation dataset to draw replacements from.
        seed: Random seed for reproducible sampling.

    Returns:
        Tuple of (new_eval_data, summary_dict).
    """
    current_queries = {d["query"] for d in current_eval_data}
    pool_by_query = {d["query"]: d for d in full_pool}
    n_original = len(current_eval_data)
    max_drop = max(1, int(n_original * MAX_DROP_FRACTION))

    # Find dead queries in current eval set
    dead_in_current = {
        p.query for p in query_difficulty.dead if p.query in current_queries
    }
    to_drop = sorted(dead_in_current)[:max_drop]

    # Find discriminating queries NOT in current eval set
    disc_available = [
        p.query for p in query_difficulty.discriminating
        if p.query not in current_queries and p.query in pool_by_query
    ]

    if not to_drop or not disc_available:
        return current_eval_data, {"dropped": 0, "added": 0, "unchanged": True}

    rng = random.Random(seed)
    rng.shuffle(disc_available)
    n_swap = min(len(to_drop), len(disc_available))
    replacements = disc_available[:n_swap]

    # Build new eval set — only drop as many as we can replace
    drop_set = set(to_drop[:n_swap])
    new_data = [d for d in current_eval_data if d["query"] not in drop_set]
    for q in replacements:
        new_data.append(pool_by_query[q])

    logger.info(
        "Adaptive sampling: dropped %d dead queries, added %d discriminating",
        len(drop_set), len(replacements),
    )

    return new_data, {
        "dropped": len(drop_set),
        "added": len(replacements),
        "dropped_queries": list(drop_set),
        "added_queries": replacements,
        "unchanged": False,
    }
