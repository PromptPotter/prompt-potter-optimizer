from __future__ import annotations

import enum
from dataclasses import dataclass


class MeasurementRole(enum.StrEnum):
    """Why a scoring pass ran, stamped on every answer it files: provenance an id cannot carry."""

    ORIGIN = "origin"
    PANEL = "panel"
    BACKFILL = "backfill"
    PARENT = "parent"
    REPAIR = "repair"
    # The origin panel beside a draw: the round's election pools it, no cross-round read does.
    OVERLAP = "overlap"
    VERIFY = "verify"
    BENCH = "bench"
    NOISE_FLOOR = "noise_floor"

    @property
    def is_proposal(self) -> bool:
        """An arm its round proposed: never the parent it re-read beside them."""
        return self != MeasurementRole.PARENT


class RoleScope(enum.StrEnum):
    """Which roles a reading of an individual's cells may see; two scopes never pair."""

    DECISION = "decision"
    REPORT = "report"
    BENCH = "bench"


@dataclass(frozen=True)
class MeasuredPass:
    individual_id: str
    role: MeasurementRole
