"""Instrument mode — this task's cycle is a MEASUREMENT, not a campaign.

L4 runs a whole campaign as ``f(optimizer_prompt, seed) -> proxies``, and a campaign is a
stateful process, so ``f`` leaks unless features are *subtracted*: recursion depth, the
optimizer decoding clamp, the evidence epoch. This is the ONE declared mode that binds
them — never re-split it into properties a spawn site sets by hand, because forgetting
one reintroduces the leak and the number still looks exactly like a measurement.
"""

from __future__ import annotations

import contextvars
import enum
from dataclasses import dataclass
from typing import Any

__all__ = [
    "MAX_INSTRUMENT_DEPTH",
    "InstrumentMode",
    "MeasuredCandidate",
    "MeasurementRole",
    "enter_instrument_mode",
    "instrument_depth",
    "instrument_mode",
    "measured_candidate",
    "set_measured_candidate",
]

# How deep the recursion may nest. L4 (an outer campaign scoring inner campaigns) is depth 1;
# the machinery re-enters, so L5+ nests by construction and nothing else would stop it. Depth
# rides the mode rather than the spawn context because ``asyncio.create_task`` copies the
# context into the inner campaign, so each level reads exactly its parent's value.
MAX_INSTRUMENT_DEPTH = 2


@dataclass(frozen=True)
class InstrumentMode:
    """What it means for this task's cycle to be a measurement instrument.

    ``depth`` — recursion depth (a top-level campaign is 0, i.e. no mode bound at all).

    ``evidence_epoch`` — the run-ids already banked in the tenant archive when this cycle
    started. Evidence reads skip them, so the cycle sees only what it measured itself. The
    archive plays two roles that conflating is what made an inner cycle unreproducible: a
    content-addressed CACHE of raw grades (keyed by content hash, so a hit IS the same
    measurement — it must stay shared, it is what lets an origin replay instead of being
    re-paid and re-drawn) and cross-run MEMORY (the δ ruler + the ``AxisIndex`` panels).
    MEMORY over the whole tenant archive is a feature for a campaign and contamination for
    an instrument: every inner cycle appends runs, so the next one calibrates on a different
    ruler and renders different panel text — busting the optimizer call cache and re-rolling
    the whole trajectory. An instrument must not depend on how often it has been used.

    ``optimizer_clamp`` — decoding knobs pinned onto EVERY optimizer call (``temperature``
    and ``seed``). ``temperature`` pins the DISTRIBUTION; without a ``seed`` the provider
    still draws freely from it, so the two only clamp together. ``None`` leaves the
    optimizer's file config alone.
    """

    depth: int
    evidence_epoch: frozenset[str]
    optimizer_clamp: dict[str, Any] | None


_MODE: contextvars.ContextVar[InstrumentMode | None] = contextvars.ContextVar(
    "instrument_mode", default=None
)


def enter_instrument_mode(
    *,
    evidence_epoch: frozenset[str],
    optimizer_clamp: dict[str, Any] | None,
) -> InstrumentMode:
    """Declare this task's cycle a measurement instrument; return the bound mode.

    Call once, at the spawn site, INSIDE the inner cycle's own ``asyncio.Task`` — the task
    owns its context copy, so the mode cannot leak back to the cycle that spawned it, and a
    cycle nested inside this one reads exactly this depth as its parent's.
    """
    mode = InstrumentMode(
        depth=instrument_depth() + 1,
        evidence_epoch=evidence_epoch,
        optimizer_clamp=optimizer_clamp,
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
    """WHY a scoring pass ran — the half of provenance an id alone cannot carry.

    ``PANEL`` is a candidate's own evidence, measured in the round's deterministic shared
    order. The other three re-enter the gateway for a DIFFERENT purpose and therefore
    outside that order: ``BACKFILL`` catches a prior up on a sample the current candidate
    reached, ``PARENT`` re-scores the round's parent on this round's scoring set, and
    ``REPAIR`` re-measures a closed round's unmeasured cells on resume.
    """

    PANEL = "panel"
    BACKFILL = "backfill"
    PARENT = "parent"
    REPAIR = "repair"


@dataclass(frozen=True)
class MeasuredCandidate:
    """Which candidate the OUTER loop is scoring right now.

    Deliberately NOT part of :class:`InstrumentMode`: that declares what a cycle *is*
    (hermetic properties that must hold together), while this is provenance — who asked
    for the measurement. Conflating them would make a mode-read imply a spawn-read.

    It exists because candidate identity dies at the connector seam. ``measure_sample``
    takes ``(sample, session, pipeline_params)`` and the ``WireAdapter``/``InProcessRun``
    protocols carry only ``(query, payload)`` — deliberately, so a connector stays
    ignorant of the loop above it. Widening that protocol for a field only L4 reads would
    tax ``termnorm`` and every future connector; an ambient read costs them nothing. Same
    reasoning, and same shape, as ``_CURRENT_ROUND`` — which supplies the round half of
    this stamp and is why the round isn't duplicated here.

    Bound in the OUTER task by ``score_search_point`` — the sole scoring ingress — from an
    argument every caller must supply. It used to be bound by ``score_one_candidate``
    alone, so the two askers that RE-ENTER the gateway (``_pobb_backfill``,
    ``rescore_parent``) inherited whichever candidate happened to be bound last and
    stamped their measurements with it: two PoBB backfills that ran C1.1's optimizer
    prompts were recorded as C1.2's. Binding at the ingress makes every pass declare who
    it is measuring for, and no future caller can inherit a stale identity.
    ``run_inner_cycle`` runs in that same task (its inner campaign gets a fresh task only
    afterwards), so the binding is live at the spawn site.

    ``role`` is why a stamp is not just an id. A ``BACKFILL`` measures a PRIOR searchpoint
    to fill a paired comparison, out of the round's shared order and on a different code
    path; a ``PANEL`` row is a candidate's own evidence, measured in that order. Both are
    legitimate and they are not interchangeable — recording only the id let one be read as
    the other, which is how a paired-comparison row was promoted into a candidate's panel.
    """

    idx: int
    candidate_id: str
    label: str
    role: MeasurementRole = MeasurementRole.PANEL


_MEASURED: contextvars.ContextVar[MeasuredCandidate | None] = contextvars.ContextVar(
    "measured_candidate", default=None
)


def set_measured_candidate(candidate: MeasuredCandidate | None) -> None:
    """Declare the candidate the outer loop is scoring, for anything it spawns.

    Set (never reset) per candidate: the loop scores them in sequence within one task, so
    each ``set`` supersedes the last, and the value is only ever read *inside* a scoring
    call. ``None`` at a spawn means no candidate was in scope — an origin (C0) pass, which
    is a real answer, not a missing one.
    """
    _MEASURED.set(candidate)


def measured_candidate() -> MeasuredCandidate | None:
    """The candidate being scored in this task, or ``None`` outside candidate scoring."""
    return _MEASURED.get()
