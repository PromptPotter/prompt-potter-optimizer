from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from pydantic import ConfigDict, JsonValue

from promptpotter.domain.strict_model import StrictModel
from promptpotter.shared.hashing import stable_hash


class Sample(StrictModel):
    # `extra="ignore"`: a dataset row carries whatever columns the operator's file had.
    model_config = ConfigDict(extra="ignore")

    id: int
    query: str
    # ``None`` DECLARES a verifier-graded cell; a placeholder string reads as a MISS on every row.
    ground_truth: str | None

    # The bare question, where ``query`` also carries context a grader must not read; else ``None``.
    question: str | None = None

    # Part of the SAMPLE's content address, never the instrument's: there, a growing panel re-keys every shared cell.
    source_pin: dict[str, JsonValue] | None = None

    # Where the row sits, never what it is: out of `key`, so its cells replay under any membership.
    bench_only: bool = False

    @property
    def key(self) -> str:
        return sample_key(
            query=self.query,
            ground_truth=self.ground_truth,
            question=self.question,
            source_pin=self.source_pin,
        )

    @classmethod
    def from_dict(cls, data: dict[str, Any], fallback_id: int | None = None) -> Sample:
        if "id" not in data and fallback_id is not None:
            data = {**data, "id": fallback_id}
        return cls(**data)


def sample_key(
    *,
    query: str,
    ground_truth: str | None,
    question: str | None,
    source_pin: dict[str, JsonValue] | None,
) -> str:
    """Position and dataset name are not in it; normalized as a measured row stores these fields, so the two agree."""
    return stable_hash(
        {
            "query": query,
            "ground_truth": ground_truth or "",
            "question": question or None,
            "source_pin": source_pin,
        }
    )


@dataclass(frozen=True, slots=True)
class Measurement:
    """``row`` is the banked facts WHOLE: a reader grades it as it stands, errors included."""

    answer: str
    config_key: str
    sample_id: int
    node_configs: list[tuple[str, dict[str, Any]]]
    row: dict[str, Any]
    created_at: str


__all__ = ["Measurement", "Sample", "sample_key"]
