from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from pydantic import ConfigDict, JsonValue

from promptpotter.domain.scoring import MeasuredCell
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


class ArchiveEntry(StrictModel):
    """A configuration under ONE dataset; ``name`` and ``created_at`` are its first answer's."""

    model_config = ConfigDict(frozen=True)

    config_key: str
    # ``PipelineSchema.sp_hash``: empty where the searchpoint resolves no node config.
    prompt_fields_id: str
    rendered_prompt_hash: str
    # Empty with it, and its cells are then never replayed.
    node_configs: list[tuple[str, dict[str, Any]]]
    pipeline_params: dict[str, Any]
    dataset_name: str | None
    name: str = ""
    created_at: str = ""

    @property
    def individual(self) -> str:
        return self.prompt_fields_id or self.config_key


def _stated(row: Mapping[str, object], key: str) -> str:
    value = row.get(key)
    if type(value) is not str:
        raise TypeError(f"a filed answer states its {key}; this row holds {value!r}")
    return value


def _stamp(row: Mapping[str, object], key: str) -> str | None:
    return None if row.get(key) is None else _stated(row, key)


@dataclass(frozen=True, slots=True)
class FiledAnswer:
    """``cell.sample_id`` names a slot in ``dataset_name`` and nowhere else."""

    cell: MeasuredCell
    config_key: str
    dataset_name: str | None
    role: str
    source: str
    provenance: str
    created_at: str
    compaction: str | None = None
    purged: str | None = None

    @classmethod
    def from_wire(cls, row: Mapping[str, object]) -> FiledAnswer:
        return cls(
            MeasuredCell.from_wire(row),
            config_key=_stated(row, "config_key"),
            dataset_name=_stamp(row, "dataset_name"),
            role=_stated(row, "role"),
            source=_stated(row, "source"),
            provenance=_stated(row, "provenance"),
            created_at=_stated(row, "created_at"),
            compaction=_stamp(row, "compaction"),
            purged=_stamp(row, "purged"),
        )

    def wire(self) -> dict[str, object]:
        out = {
            **self.cell.wire(),
            "config_key": self.config_key,
            "dataset_name": self.dataset_name,
            "role": self.role,
            "source": self.source,
            "provenance": self.provenance,
            "created_at": self.created_at,
        }
        if self.compaction is not None:
            out["compaction"] = self.compaction
        if self.purged is not None:
            out["purged"] = self.purged
        return out

    @property
    def answer(self) -> str:
        if (address := self.cell.answer) is None:
            raise TypeError(f"the answer at slot {self.cell.sample_id} carries no archive address")
        return address


__all__ = ["ArchiveEntry", "FiledAnswer", "Sample", "sample_key"]
