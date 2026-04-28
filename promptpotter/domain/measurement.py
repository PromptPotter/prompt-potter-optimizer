"""Measurement — the canonical row of the archive.

One row = one ``(sample × config → outcome)`` measurement, denormalized for
direct inspection. Returned by both retrieval views: ``measurements_for_sample``
(by training example) and ``measurements_for_config`` (by searchpoint).
Both views return the same shape so callers can swap dimensions freely.

Frozen + slots — read-only projection over archive batches; never mutated
in place.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True, slots=True)
class Measurement:
    run_id: str
    content_hash: str
    sample_id: int
    query: str
    ground_truth: str
    predicted: str
    hit: bool
    score: float | None
    node_configs: list[tuple[str, dict[str, Any]]]
    pipeline_data: dict[str, Any]
    created_at: str
    run_scores: dict[str, Any]
