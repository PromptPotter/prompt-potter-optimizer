"""Sample — the canonical per-sample domain object, plus ``Measurement`` archive row.

Sample is the data-side peer to SearchPoint. It owns a stable cross-campaign
``id``, the input strings (``query``, ``ground_truth``), and accumulating
cross-campaign metadata (``escalation_count``, ``run_ids``). Measurement
values (predicted/hit/fitness/pipeline_data) live in ``archive/measurements/``
and the per-sample aggregate stats live in ``SampleIndex`` — accessed via
the methods below, never duplicated as fields on the Sample itself.

Mutable (unlike frozen ``JobSearchPoint``) because ``escalation_count`` and
``run_ids`` accumulate over the campaign lifecycle.

``Measurement`` is the canonical archive row — one ``(sample × config →
outcome)`` row, denormalized for direct inspection, returned by both
retrieval views (``measurements_for_sample`` / ``measurements_for_config``).
Frozen + slots — read-only projection over archive batches.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from pydantic import BaseModel, Field

if TYPE_CHECKING:
    from promptpotter.application.intelligence.indexes import SampleIndex


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

    # --- Cross-campaign signal accessors (resolve through SampleIndex) ---
    #
    # Retrieve-from-archive accessors (``latest_measurement`` /
    # ``all_measurements`` / ``pipeline_data_for``) live on the archive
    # reader, not here — they need store access which SampleIndex itself
    # does not hold. Use ``MeasurementArchive`` directly when trace content
    # is required.

    def hits(self, index: SampleIndex) -> list[bool]:
        """Hit/miss history across all measurements touching this sample."""
        return index.hits(self.id)

    def failure_modes(self, index: SampleIndex) -> list[str]:
        """Failure mode (``terminated_at``) history for this sample."""
        return index.failure_modes(self.id)

    def degradation_count(self, index: SampleIndex) -> int:
        """Number of measurements that produced degradation warnings."""
        return index.degradation_count(self.id)


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
