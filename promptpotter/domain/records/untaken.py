from __future__ import annotations

from typing import Literal

from pydantic import Field

from promptpotter.domain.records import CycleRecord
from promptpotter.shared.clock import utcnow_iso


class UntakenBoughtRecord(CycleRecord):
    """The answers a phase's walks BOUGHT and none took (a look-ahead cell its cut discarded,
    arrived before the close or after it): filed, so another campaign remembers them, and kept
    out of this campaign's own memory until one of its walks takes them."""

    record_type: Literal["untaken_bought"] = "untaken_bought"
    round: int
    answers: list[str] = Field(min_length=1)
    timestamp: str = Field(default_factory=utcnow_iso)
