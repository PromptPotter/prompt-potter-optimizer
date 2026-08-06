"""Content-addressed hashing for measurement deduplication. In ``shared/`` to avoid a circular import between the two
searchpoint modules."""

from __future__ import annotations

import hashlib
import json
from typing import Any

# SHA256 truncated to 24 hex chars (96 bits) — sufficient for content-addressed
# deduplication across campaigns.  Birthday-bound collision probability stays
# negligible up to ~280 billion items.
HASH_TRUNCATE = 24

__all__ = ["HASH_TRUNCATE", "content_hash"]


def content_hash(
    rendered_prompt: str,
    dataset: list[Any],
    pipeline_params: dict[str, Any] | None = None,
) -> str:
    """``sha256`` over rendered prompt + sorted query/ground-truth pairs + ``pipeline_params``. Sample ORDER does not affect
    it; ``pipeline_params`` is included when non-empty, so different pipeline configs hash distinctly."""
    pairs = sorted((d.query, d.ground_truth) for d in dataset)
    blob_dict: dict[str, Any] = {
        "prompt": rendered_prompt,
        "pairs": pairs,
    }
    if pipeline_params:
        blob_dict["pipeline_params"] = pipeline_params
    blob = json.dumps(blob_dict, sort_keys=True)
    return hashlib.sha256(blob.encode()).hexdigest()[:HASH_TRUNCATE]
