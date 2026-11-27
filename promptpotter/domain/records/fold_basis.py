from __future__ import annotations

from typing import Literal

from pydantic import Field

from promptpotter.domain.fitness_fold import FoldBasis
from promptpotter.domain.records import CycleRecord
from promptpotter.shared.clock import utcnow_iso


class FoldBasisRecord(CycleRecord):
    """Filed as a scorer is seated: every reader folds a walk's recorded rows under the last one."""

    record_type: Literal["fold_basis"] = "fold_basis"
    basis: FoldBasis
    timestamp: str = Field(default_factory=utcnow_iso)
