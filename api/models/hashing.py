"""Content-addressed hashing for evaluation deduplication.

Shared between models (SearchPoint.content_hash) and services
(prompt_eval) — lives in models to avoid layering violations.
"""
from __future__ import annotations

import hashlib
import json

# SHA256 truncated to 16 hex chars (64 bits) — sufficient for content-addressed
# deduplication within a single project.  Collision probability stays negligible
# for the expected dataset sizes (<100k eval runs).
HASH_TRUNCATE = 16

# Fields that render() assembles into the prompt string.
# Lives here (leaf module) to avoid circular imports between
# search_point.py and opt_search_point.py.
PROMPT_STRING_FIELDS: list[str] = [
    "persona",
    "task_intent",
    "problem_description",
    "instruction",
    "thinking_style",
    "answer_format",
]


def sp_identity_hash(
    rendered_prompt_hash: str,
    model: str,
    temperature: float,
    pipeline_params: dict | None = None,
) -> str:
    """SearchPoint identity hash — hashes only dimensions that affect the result.

    When ``pipeline_params`` has a ``steps`` list that excludes ``llm_ranking``,
    the prompt is never executed by the backend.  In that case the prompt is
    excluded from the hash so that different prompt variants with the same
    pipeline config share the same SP hash.

    Uses ``rendered_prompt_hash`` (not the full prompt text) so the hash
    can be computed from stored index data without loading detail files.
    """
    pp = pipeline_params or {}
    steps = pp.get("steps")
    prompt_matters = steps is None or "llm_ranking" in steps

    blob_dict: dict = {"model": model, "temperature": temperature}
    if prompt_matters:
        blob_dict["rp_hash"] = rendered_prompt_hash
    if pipeline_params:
        blob_dict["pipeline_params"] = pipeline_params
    blob = json.dumps(blob_dict, sort_keys=True)
    return hashlib.sha256(blob.encode()).hexdigest()[:HASH_TRUNCATE]


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
