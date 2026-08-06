"""``Sample`` is the data-side peer to SearchPoint; aggregates live in ``SampleIndex`` and measurements in ``measurements/``,
never duplicated on the model. Mutable because ``run_ids`` accumulates over the campaign."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from pydantic import ConfigDict, Field

from promptpotter.domain.strict_model import StrictModel


class Sample(StrictModel):
    # `extra="ignore"`: a dataset row carries whatever columns the operator's file had
    # (`task`, `source_sheet`, …); this model owns only the ones it names.
    model_config = ConfigDict(extra="ignore")

    # Primary identity + inputs — owned directly.
    id: int
    query: str
    ground_truth: str

    # Cross-campaign metadata — accumulates via SampleIndex.ingest_run.
    escalation_count: int = 0
    run_ids: list[str] = Field(default_factory=list)

    @classmethod
    def from_dict(cls, data: dict[str, Any], fallback_id: int | None = None) -> Sample:
        """``id`` falls back to ``fallback_id`` (positional) when absent.
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
    fitness: float | None
    node_configs: list[tuple[str, dict[str, Any]]]
    pipeline_data: dict[str, Any]
    created_at: str


__all__ = ["Measurement", "Sample"]
