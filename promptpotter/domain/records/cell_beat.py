from __future__ import annotations

from typing import Literal

from pydantic import Field

from promptpotter.domain.cycle_paths import CycleHop
from promptpotter.domain.paired_reading import ArmKey
from promptpotter.domain.records import CycleRecord
from promptpotter.shared.clock import utcnow_iso


class CellBeatRecord(CycleRecord):
    """A scoring cell still open, by the arm it walks and the bank row it measures: no node runs it."""

    record_type: Literal["cell_beat"] = "cell_beat"
    round: int | None = None
    # ``None`` on a pass no arm walks (a bench pass, a verify).
    arm: ArmKey | None
    position: int
    elapsed_s: float
    detail: str | None = None
    # The cycle the cell runs (`Connector.cell_hop`), in THIS cycle's sandbox.
    inner: CycleHop | None = None
    timestamp: str = Field(default_factory=utcnow_iso)
