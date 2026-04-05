"""Content-addressed hashing for evaluation deduplication.

Leaf module shared by both models and services. Lives in ``promptpotter/shared/``
to avoid circular imports between search_point.py and opt_search_point.py.
"""

from __future__ import annotations

import hashlib
import json

# SHA256 truncated to 24 hex chars (96 bits) — sufficient for content-addressed
# deduplication across campaigns.  Birthday-bound collision probability stays
# negligible up to ~280 billion items.
HASH_TRUNCATE = 24

__all__ = ["HASH_TRUNCATE", "eval_content_hash", "sp_identity_hash"]


def _hash_dict(blob_dict: dict) -> str:
    """SHA256-truncate a JSON-serialized dict."""
    blob = json.dumps(blob_dict, sort_keys=True)
    return hashlib.sha256(blob.encode()).hexdigest()[:HASH_TRUNCATE]


def sp_identity_hash(
    rendered_prompt_hash: str,
    pipeline_params: dict | None = None,
    prompt_node_names: list[str] | None = None,
) -> str:
    """SearchPoint identity hash — hashes only dimensions that affect the result.

    When ``pipeline_params`` has a ``steps`` list that excludes all
    prompt-bearing nodes, the prompt is never executed by the backend.
    In that case the prompt is excluded from the hash so that different
    prompt variants with the same pipeline config share the same SP hash.

    ``prompt_node_names`` lists the nodes whose output depends on the
    prompt text.  When empty or None, prompt is always included in the
    hash (safe default — assumes prompt matters).

    Uses ``rendered_prompt_hash`` (not the full prompt text) so the hash
    can be computed from stored index data without loading detail files.
    """
    names = prompt_node_names or []
    pp = pipeline_params or {}
    steps = pp.get("steps")
    # Prompt matters unless steps explicitly excludes all prompt nodes.
    # When names is empty (no schema info), assume prompt matters (safe default).
    prompt_matters = not names or steps is None or any(n in steps for n in names)

    blob_dict: dict = {}
    if prompt_matters:
        blob_dict["rp_hash"] = rendered_prompt_hash
    if pipeline_params:
        blob_dict["pipeline_params"] = pipeline_params
    return _hash_dict(blob_dict)


def eval_content_hash(
    rendered_prompt: str,
    dataset: list,
    pipeline_params: dict | None = None,
) -> str:
    """Content-addressed hash for evaluation deduplication.

    ``sha256(rendered_prompt + sorted_query_gt_pairs
    + pipeline_params)[:16]``

    Order of dataset queries does not affect the hash.
    ``pipeline_params`` is included when non-empty so that different
    pipeline configurations produce distinct hashes.
    """
    pairs = sorted((d.get("query", ""), d.get("ground_truth", "")) for d in dataset)
    blob_dict: dict = {
        "prompt": rendered_prompt,
        "pairs": pairs,
    }
    if pipeline_params:
        blob_dict["pipeline_params"] = pipeline_params
    return _hash_dict(blob_dict)


