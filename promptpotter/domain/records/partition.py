from __future__ import annotations

from functools import cached_property
from typing import Literal

from pydantic import Field

from promptpotter.domain.bench import BankPartition, DatasetSplit
from promptpotter.domain.paired_reading import CellSet
from promptpotter.domain.records import CycleRecord
from promptpotter.shared.clock import utcnow_iso


class PartitionRecord(CycleRecord):
    """The partition a cycle's mint drew, as slots in its bank; a fork reads its parent's off the chain."""

    record_type: Literal["bank_partition"] = "bank_partition"
    dataset_hash: str
    split: DatasetSplit | None
    search_ids: list[int]
    bench_ids: list[int]
    # The held-out content two campaigns are compared on.
    bench_rows: CellSet
    demo_ids: list[int]
    timestamp: str = Field(default_factory=utcnow_iso)

    @classmethod
    def of(cls, partition: BankPartition, *, dataset_hash: str) -> PartitionRecord:
        return cls(
            dataset_hash=dataset_hash,
            split=partition.split,
            search_ids=[s.id for s in partition.search],
            bench_ids=[s.id for s in partition.bench],
            bench_rows=partition.bench_rows,
            demo_ids=[s.id for s in partition.demo],
        )

    @cached_property
    def bank_ids(self) -> frozenset[int]:
        return frozenset([*self.search_ids, *self.bench_ids, *self.demo_ids])
