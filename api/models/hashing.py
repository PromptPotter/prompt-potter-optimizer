"""Content-addressed hashing for evaluation deduplication.

Shared between models (SearchPoint.content_hash) and services
(prompt_eval, synthesis) — lives in models to avoid layering violations.
"""
from __future__ import annotations

import hashlib
import json

# SHA256 truncated to 16 hex chars (64 bits) — sufficient for content-addressed
# deduplication within a single project.  Collision probability stays negligible
# for the expected dataset sizes (<100k eval runs).
HASH_TRUNCATE = 16


def eval_content_hash(
    rendered_prompt: str,
    eval_data: list,
    model: str,
    temperature: float,
    pipeline_params: dict | None = None,
) -> str:
    """Content-addressed hash for evaluation deduplication.

    ``sha256(rendered_prompt + sorted_query_gt_pairs + model + temperature
    + pipeline_params)[:16]``

    Order of eval_data queries does not affect the hash.
    ``pipeline_params`` is included when non-empty so that different
    pipeline configurations produce distinct hashes.
    """
    pairs = sorted(
        (d.get("query", ""), d.get("ground_truth", "")) for d in eval_data
    )
    blob_dict: dict = {
        "prompt": rendered_prompt, "pairs": pairs,
        "model": model, "temperature": temperature,
    }
    if pipeline_params:
        blob_dict["pipeline_params"] = pipeline_params
    blob = json.dumps(blob_dict, sort_keys=True)
    return hashlib.sha256(blob.encode()).hexdigest()[:HASH_TRUNCATE]
