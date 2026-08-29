"""Instrument mode — this task's cycle is a MEASUREMENT, not a campaign. The ONE declared mode
binding the three subtractions; re-split it and forgetting one still looks like a measurement."""

from __future__ import annotations

import contextlib
import contextvars
import enum
from collections.abc import Iterator
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from promptpotter.domain.ruler import DeltaRuler

__all__ = [
    "MAX_INSTRUMENT_DEPTH",
    "MeasuredCandidate",
    "MeasurementRole",
    "enter_instrument_mode",
    "instrument_depth",
    "instrument_mode",
    "measured_candidate",
    "measured_candidate_scope",
]

# How deep the recursion may nest. L4 (an outer campaign scoring inner campaigns) is depth 1;
# the machinery re-enters, so L5+ nests by construction and nothing else would stop it. Depth
# rides the mode rather than the spawn context because ``asyncio.create_task`` copies the
# context into the inner campaign, so each level reads exactly its parent's value.
MAX_INSTRUMENT_DEPTH = 2


@dataclass(frozen=True)
class InstrumentMode:
    """``evidence_epoch`` keeps the archive a shared CACHE while withholding it as cross-run MEMORY:
    an instrument must not depend on how often it has been used. ``temperature`` and ``seed`` clamp only together.

    ``ruler`` is the δ scale the SPAWNER fixed — the third subtraction: without it a cell fits its
    own from whatever the epoch leaves visible, which is the arms under test."""

    depth: int
    evidence_epoch: frozenset[str]
    optimizer_clamp: dict[str, Any] | None
    ruler: DeltaRuler | None


_MODE: contextvars.ContextVar[InstrumentMode | None] = contextvars.ContextVar(
    "instrument_mode", default=None
)


def enter_instrument_mode(
    *,
    evidence_epoch: frozenset[str],
    optimizer_clamp: dict[str, Any] | None,
    ruler: DeltaRuler | None,
) -> InstrumentMode:
    """Declare this task's cycle a measurement instrument. Call once at the spawn site, INSIDE the
    inner cycle's own ``asyncio.Task``, so the mode cannot leak back to the spawning cycle."""
    mode = InstrumentMode(
        depth=instrument_depth() + 1,
        evidence_epoch=evidence_epoch,
        optimizer_clamp=optimizer_clamp,
        ruler=ruler,
    )
    _MODE.set(mode)
    return mode


def instrument_mode() -> InstrumentMode | None:
    """The instrument mode bound for this task, or ``None`` — the normal campaign case."""
    return _MODE.get()


def instrument_depth() -> int:
    """Recursion depth of this task's cycle; 0 when it is a normal campaign."""
    mode = _MODE.get()
    return mode.depth if mode is not None else 0


class MeasurementRole(enum.StrEnum):
    """WHY a scoring pass ran — the half of provenance an id alone cannot carry. ``PANEL`` is a
    candidate's own evidence in the round's shared order; every other member re-enters outside it."""

    PANEL = "panel"
    BACKFILL = "backfill"
    PARENT = "parent"
    REPAIR = "repair"
    # QUARANTINED: the winner read on the cells its whole line has already answered
    # (``domain/results.py::OverlapReading``). Report-only — these rows reach no election, no
    # parent floor, no lift and no acquisition, so the pass may measure one arm without making
    # it better-identified than the arms it was judged against.
    OVERLAP = "overlap"


@dataclass(frozen=True)
class MeasuredCandidate:
    """Which candidate the OUTER loop is scoring — provenance, not a mode, and ambient because that
    identity dies at the connector seam. Bound at the INGRESS, or a re-entering asker inherits it stale."""

    idx: int
    candidate_id: str
    label: str
    role: MeasurementRole = MeasurementRole.PANEL


_MEASURED: contextvars.ContextVar[MeasuredCandidate | None] = contextvars.ContextVar(
    "measured_candidate", default=None
)


@contextlib.contextmanager
def measured_candidate_scope(candidate: MeasuredCandidate | None) -> Iterator[None]:
    """The candidate the outer loop is scoring, for anything it spawns; ``None`` is an origin pass.
    Scoped, never a bare set: scoring re-enters itself in the SAME task (a prior catch-up), and
    without the restore that callee's stamp mis-keys the rest of this walk's ``inner_campaign_id``."""
    token = _MEASURED.set(candidate)
    try:
        yield
    finally:
        _MEASURED.reset(token)


def measured_candidate() -> MeasuredCandidate | None:
    """The candidate being scored in this task, or ``None`` outside candidate scoring."""
    return _MEASURED.get()
