"""Adaptive scoring-set sampling — swap dead queries for discriminating ones.

Pure function, no optimizer state. Lives in ``intelligence`` as the shared
ground between the scan loop and the optimization loop.
"""

from __future__ import annotations

import logging
import random
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from promptpotter.domain.analysis import QueryDifficulty

logger = logging.getLogger(__name__)

__all__ = ["adapt_scoring_set"]

# Never drop more than this fraction of the scoring set per adaptation
_MAX_DROP_FRACTION = 0.25


def adapt_scoring_set(
    current_dataset: list[dict],
    query_difficulty: QueryDifficulty,
    full_pool: list[dict],
    *,
    seed: int = 42,
) -> tuple[list[dict], dict]:
    """Replace dead queries with discriminating ones from the full pool.

    Args:
        current_dataset: Current scoring subset.
        query_difficulty: Precomputed difficulty classification.
        full_pool: Full scoring dataset to draw replacements from.
        seed: Random seed for reproducible sampling.

    Returns:
        Tuple of (new_dataset, summary_dict).
    """
    current_queries = {d["query"] for d in current_dataset}
    pool_by_query = {d["query"]: d for d in full_pool}
    n_original = len(current_dataset)
    max_drop = max(1, int(n_original * _MAX_DROP_FRACTION))

    # Find dead queries in current scoring set
    dead_in_current = {p.query for p in query_difficulty.dead if p.query in current_queries}
    to_drop = sorted(dead_in_current)[:max_drop]

    # Find discriminating queries NOT in current scoring set
    disc_available = [
        p.query
        for p in query_difficulty.discriminating
        if p.query not in current_queries and p.query in pool_by_query
    ]

    if not to_drop or not disc_available:
        return current_dataset, {"dropped": 0, "added": 0, "unchanged": True}

    rng = random.Random(seed)
    rng.shuffle(disc_available)
    n_swap = min(len(to_drop), len(disc_available))
    replacements = disc_available[:n_swap]

    # Build new scoring set — only drop as many as we can replace
    drop_set = set(to_drop[:n_swap])
    new_data = [d for d in current_dataset if d["query"] not in drop_set]
    for q in replacements:
        new_data.append(pool_by_query[q])

    logger.info(
        "Adaptive sampling: dropped %d dead queries, added %d discriminating",
        len(drop_set),
        len(replacements),
    )

    return new_data, {
        "dropped": len(drop_set),
        "added": len(replacements),
        "dropped_queries": list(drop_set),
        "added_queries": replacements,
        "unchanged": False,
    }
