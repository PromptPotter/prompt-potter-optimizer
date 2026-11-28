"""The δ ruler's reading of the line at a round's close: the race's instrument and L4's level
series, never the round's statement, which names no elimination rule (architecture §0)."""

from __future__ import annotations

from typing import Literal

from pydantic import Field

from promptpotter.domain.records import CycleRecord
from promptpotter.domain.ruler import LevelSeries
from promptpotter.shared.clock import utcnow_iso


class LevelSeriesRecord(CycleRecord):
    """Banked beside each close: every standing round re-read on that close's one scale, so the
    series moves with the clock. A restatement banks none; the next close re-reads every round."""

    record_type: Literal["level_series"] = "level_series"
    round: int
    series: LevelSeries
    timestamp: str = Field(default_factory=utcnow_iso)
