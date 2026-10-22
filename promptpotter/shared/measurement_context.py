from __future__ import annotations

import contextvars
import enum
from dataclasses import dataclass
from typing import Annotated

from promptpotter.shared.hashing import shapes_optimizer_prompt

# L4 is depth 1, and nothing else stops L5+.
MAX_INSTRUMENT_DEPTH = 2


@dataclass(frozen=True)
class InstrumentMode:
    """``root_round`` is the depth-0 run's round the cell spends under: its bills land on that ledger."""

    depth: int
    root_round: int | None


# Never reset: `_MODE` must cover FINALIZE, or a late bill lands on the cell's own ledger.
_MODE: Annotated[contextvars.ContextVar[InstrumentMode | None], shapes_optimizer_prompt] = (
    contextvars.ContextVar("instrument_mode", default=None)
)


def enter_instrument_mode(*, spawned_in: int | None) -> InstrumentMode:
    """Call INSIDE the inner cycle's own ``asyncio.Task``, or the mode leaks to the spawner.
    *spawned_in* is the spawner's round, which only a depth-1 cell takes as its root's."""
    outer = _MODE.get()
    mode = InstrumentMode(
        depth=instrument_depth() + 1,
        root_round=spawned_in if outer is None else outer.root_round,
    )
    _MODE.set(mode)
    return mode


@shapes_optimizer_prompt
def instrument_mode() -> InstrumentMode | None:
    return _MODE.get()


def instrument_depth() -> int:
    mode = _MODE.get()
    return mode.depth if mode is not None else 0


class MeasurementRole(enum.StrEnum):
    """Why a scoring pass ran, stamped on every answer it files: provenance an id cannot carry."""

    ORIGIN = "origin"
    PANEL = "panel"
    BACKFILL = "backfill"
    PARENT = "parent"
    REPAIR = "repair"
    # Report-only: reaching a decision would leave one arm better-identified than its rivals.
    OVERLAP = "overlap"
    VERIFY = "verify"
    BENCH = "bench"


class RoleScope(enum.StrEnum):
    """Which roles a reading of an individual's cells may see; two scopes never pair."""

    DECISION = "decision"
    REPORT = "report"
    BENCH = "bench"


_QUARANTINED = frozenset({MeasurementRole.OVERLAP, MeasurementRole.VERIFY})
_HELD_OUT = frozenset({MeasurementRole.BENCH})

SCOPE_ROLES: dict[RoleScope, frozenset[MeasurementRole]] = {
    RoleScope.DECISION: frozenset(MeasurementRole) - _QUARANTINED - _HELD_OUT,
    RoleScope.REPORT: frozenset(MeasurementRole) - _HELD_OUT,
    RoleScope.BENCH: _HELD_OUT,
}


# A cell of these roles is a cell of the ARM its individual is in its round; any other is a pass.
ARM_ROLES = frozenset({MeasurementRole.ORIGIN, MeasurementRole.PANEL, MeasurementRole.PARENT})
# The passes whose cells reach the ledger one by one, so a live surface draws and ticks them.
RECORDED_ROLES = ARM_ROLES | {MeasurementRole.BENCH}


@dataclass(frozen=True)
class MeasuredPass:
    """Bound at the INGRESS, or a re-entering asker inherits it stale."""

    individual_id: str
    role: MeasurementRole = MeasurementRole.PANEL


_MEASURED: contextvars.ContextVar[MeasuredPass | None] = contextvars.ContextVar(
    "measured_pass", default=None
)


def measured_pass_context(measured: MeasuredPass | None) -> contextvars.Context:
    """``None`` is an origin pass. A context, not a set: one loop launches every walk's cells."""
    context = contextvars.copy_context()
    context.run(_MEASURED.set, measured)
    return context


def measured_pass() -> MeasuredPass | None:
    return _MEASURED.get()
