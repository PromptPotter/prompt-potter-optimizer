from __future__ import annotations

import enum
from dataclasses import dataclass

# L4 runs one level deep: an inner run spawns none (`docs/concepts/l4.md` § Depth).
MAX_INSTRUMENT_DEPTH = 1


@dataclass(frozen=True)
class InstrumentMode:
    """``root_round`` is the depth-0 run's round the cell spends under: its bills land on that ledger."""

    depth: int
    root_round: int | None


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


class RoleScope(enum.StrEnum):
    """Which roles a reading of an individual's cells may see; two scopes never pair."""

    DECISION = "decision"
    REPORT = "report"
    BENCH = "bench"


@dataclass(frozen=True)
class MeasuredPass:
    individual_id: str
    role: MeasurementRole
