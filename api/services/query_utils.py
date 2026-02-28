"""
Shared query-parsing and evaluation utilities.
"""

import random


def parse_bom_material(query: str) -> tuple[str, str]:
    """Split a query string into (bom_material, process).

    TermNorm queries use the format ``bom_material / process``.
    If no slash is present, process is an empty string.
    """
    if "/" in query:
        last_slash = query.rfind("/")
        bom_material = query[:last_slash].strip()
        process = query[last_slash + 1:].strip()
    else:
        bom_material = query.strip()
        process = ""
    return bom_material, process


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
