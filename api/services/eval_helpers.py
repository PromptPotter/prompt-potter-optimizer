"""Shared evaluation utilities.

Extracted from prompt_optimizer.py and feedback_cycle.py to deduplicate
query subsampling logic that was copy-pasted 3 times.
"""

import random


def subsample_queries(
    eval_data: list[dict],
    n_queries: int,
    seed: int = 42,
) -> list[dict]:
    """Deterministic subsample of eval queries.

    Returns the full list unchanged if ``n_queries <= 0`` or the dataset
    is already small enough.

    Args:
        eval_data: Full evaluation dataset.
        n_queries: Maximum number of queries to keep (0 = use all).
        seed: Random seed for reproducibility.
    """
    if n_queries > 0 and len(eval_data) > n_queries:
        return random.Random(seed).sample(eval_data, n_queries)
    return eval_data
