"""``Sample`` — canonical per-sample domain object + ``Measurement`` archive row.

``Sample`` is data-side peer to SearchPoint: cross-campaign ``id``, inputs, and
accumulating metadata (``escalation_count``, ``run_ids``). Per-sample aggregate
stats live in ``SampleIndex``, measurements in ``measurements/`` —
read via ``SampleIndex`` / ``MeasurementArchive`` directly, never duplicated on
the model. Mutable because ``run_ids`` accumulates over the campaign lifecycle.

``Measurement`` is the denormalized read-only archive row returned by both
``measurements_for_sample`` / ``measurements_for_config`` views.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from pydantic import BaseModel, Field


class Sample(BaseModel):
    """Canonical per-sample handle + metadata + trace coordinates."""

    model_config = {"extra": "ignore"}

    # Primary identity + inputs — owned directly.
    id: int
    query: str
    ground_truth: str

    # Cross-campaign metadata — accumulates via SampleIndex.ingest_run.
    escalation_count: int = 0
    run_ids: list[str] = Field(default_factory=list)

    @classmethod
    def from_dict(cls, data: dict[str, Any], fallback_id: int | None = None) -> Sample:
        """Construct from a plain dict.

        ``id`` falls back to ``fallback_id`` (positional) when absent.
        Extra keys (``task``, ``source_sheet``, etc.) are ignored.
        """
        if "id" not in data and fallback_id is not None:
            data = {**data, "id": fallback_id}
        return cls(**data)


@dataclass(frozen=True, slots=True)
class Measurement:
    """One ``(sample × config → outcome)`` archive row, denormalized."""

    run_id: str
    content_hash: str
    sample_id: int
    query: str
    ground_truth: str
    predicted: str
    hit: bool
    fitness: float | None
    node_configs: list[tuple[str, dict[str, Any]]]
    pipeline_data: dict[str, Any]
    created_at: str
    run_scores: dict[str, Any]


__all__ = ["Measurement", "Sample"]
