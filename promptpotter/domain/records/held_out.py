from __future__ import annotations

from typing import Literal, Self

from pydantic import Field, model_validator

from promptpotter.domain.bench import BenchReading, BenchScore, BenchSubject
from promptpotter.domain.paired_reading import ReadingState
from promptpotter.domain.records import CycleRecord
from promptpotter.domain.verification import NoiseBand, VerificationPass, VerifyReading
from promptpotter.shared.clock import utcnow_iso


class PassGradedRecord(CycleRecord):
    """The pass is the fact, the reading a cache of one grading of it; ``reading`` is ``None``
    exactly where ``state`` is not ``read``."""

    record_type: Literal["pass_graded"] = "pass_graded"
    verification: VerificationPass
    # A bench pass's place on the line; `None` on a verify.
    subject: BenchSubject | None
    # A bench pass's allowance of unscored rows; `None` on a verify, which reads what it scored.
    tolerance: int | None
    # Set only on the origin's bench pass.
    reserve_usd: float | None
    reserve_tokens: int | None
    reading: VerifyReading | BenchReading | NoiseBand | None
    state: ReadingState
    # Why an automatic verify the round owed was not sent; its pass then holds no cell.
    refusal: str | None
    timestamp: str = Field(default_factory=utcnow_iso)

    @model_validator(mode="after")
    def _refused_reads_nothing(self) -> Self:
        if self.refusal is not None and (self.reading is not None or self.verification.cells):
            raise ValueError("a refused pass sent nothing, so it reads nothing")
        return self


class BenchScoredRecord(CycleRecord):
    """The line's bench as a run declared it at its close."""

    record_type: Literal["bench_scored"] = "bench_scored"
    bench: BenchScore
    timestamp: str = Field(default_factory=utcnow_iso)
