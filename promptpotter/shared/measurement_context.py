from __future__ import annotations

import contextvars
import enum
from dataclasses import dataclass
from typing import TYPE_CHECKING, Annotated

from promptpotter.shared.hashing import shapes_optimizer_prompt

if TYPE_CHECKING:
    from promptpotter.domain.ruler import DeltaRuler

__all__ = [
    "MAX_INSTRUMENT_DEPTH",
    "NO_ROUND_SLOT",
    "MeasuredCandidate",
    "MeasurementRole",
    "enter_instrument_mode",
    "instrument_depth",
    "instrument_mode",
    "measured_candidate",
    "measured_candidate_context",
]

# L4 is depth 1, and nothing else stops L5+.
MAX_INSTRUMENT_DEPTH = 2


@dataclass(frozen=True)
class InstrumentMode:
    """``ruler`` is the δ scale the SPAWNER fixed; without it a cell fits one from the arms."""

    depth: int
    ruler: DeltaRuler | None


# Never reset: `_MODE` must cover FINALIZE, or archive reads and the ruler de-hermeticize.
_MODE: Annotated[contextvars.ContextVar[InstrumentMode | None], shapes_optimizer_prompt] = (
    contextvars.ContextVar("instrument_mode", default=None)
)


def enter_instrument_mode(*, ruler: DeltaRuler | None) -> InstrumentMode:
    """Call INSIDE the inner cycle's own ``asyncio.Task``, or the mode leaks to the spawner."""
    mode = InstrumentMode(depth=instrument_depth() + 1, ruler=ruler)
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


# Every reader keyed by slot BRANCHES on it; it still drives the per-sample tick.
NO_ROUND_SLOT = -1


@dataclass(frozen=True)
class MeasuredCandidate:
    """Bound at the INGRESS, or a re-entering asker inherits it stale."""

    idx: int
    candidate_id: str
    label: str
    role: MeasurementRole = MeasurementRole.PANEL


_MEASURED: contextvars.ContextVar[MeasuredCandidate | None] = contextvars.ContextVar(
    "measured_candidate", default=None
)


def measured_candidate_context(candidate: MeasuredCandidate | None) -> contextvars.Context:
    """``None`` is an origin pass. A context, not a set: one loop launches every walk's cells."""
    context = contextvars.copy_context()
    context.run(_MEASURED.set, candidate)
    return context


def measured_candidate() -> MeasuredCandidate | None:
    return _MEASURED.get()
