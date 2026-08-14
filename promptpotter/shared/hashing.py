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

__all__ = ["HASH_TRUNCATE", "content_hash", "dataset_hash"]


def _sorted_pairs(dataset: list[Any]) -> list[tuple[str, str]]:
    """What the rows ARE, order-independent — the one definition both hashes below stand on."""
    return sorted((d.query, d.ground_truth) for d in dataset)


def dataset_hash(dataset: list[Any]) -> str:
    """The rows alone, so two measurements can be asked whether they stand on the same ones.

    Deliberately NOT a slice of :func:`content_hash`, which mixes the rendered prompt and the
    pipeline config into the same digest: two campaigns over one dataset hash differently there,
    which is right for a measurement cache key and useless as an identity a consumer can compare.
    Exported beside a fitness number for exactly that comparison — the number means nothing
    without the identity of the rows it was measured on.
    """
    blob = json.dumps({"pairs": _sorted_pairs(dataset)}, sort_keys=True)
    return hashlib.sha256(blob.encode()).hexdigest()[:HASH_TRUNCATE]


def content_hash(
    rendered_prompt: str,
    dataset: list[Any],
    pipeline_params: dict[str, Any] | None = None,
) -> str:
    """``sha256`` over rendered prompt + sorted query/ground-truth pairs + ``pipeline_params``. Sample ORDER does not affect
    it; ``pipeline_params`` is included when non-empty, so different pipeline configs hash distinctly."""
    blob_dict: dict[str, Any] = {
        "prompt": rendered_prompt,
        "pairs": _sorted_pairs(dataset),
    }
    if pipeline_params:
        blob_dict["pipeline_params"] = pipeline_params
    blob = json.dumps(blob_dict, sort_keys=True)
    return hashlib.sha256(blob.encode()).hexdigest()[:HASH_TRUNCATE]
