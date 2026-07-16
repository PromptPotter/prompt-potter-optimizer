"""Instrument mode — this task's cycle is a MEASUREMENT, not a campaign.

L4 uses "run a full PromptPotter campaign" as a measurement function,
``f(meta_prompt, seed) -> proxies``. For the outer optimizer to learn anything ``f`` must
be a *function*: same inputs, same output. But a campaign is a stateful process — it reads
tenant-global mutable memory, it is bounded by rails, it retries on live provider
conditions — so without help ``f`` is a function of the meta-prompt AND of the whole
history and mood of the machine it ran on. It leaks by construction.

An inner cycle is therefore made hermetic by *subtracting* campaign features. That
subtraction used to be three independent ambient ContextVars (recursion depth, the
optimizer decoding clamp, the evidence epoch), each set by hand at the spawn site and each
one a thing a new code path could forget — and forgetting any one of them silently
reintroduces the leak, because the resulting number still looks like a measurement. This
is the one declared mode that replaces them: a cycle either IS an instrument (and every
hermetic property holds together) or it is not.

**Not a fourth LayerStrategy.** The ladder is closed at L1/L2/L3 and L4 is recursion (see
``application/optimization/CLAUDE.md``). This declares what a cycle *is*, not a new tier of
agent that decides anything.

Lives in ``shared/`` because its readers span layers that cannot import each other:
``infrastructure/store/archive_views`` (the evidence epoch) and
``application/optimization/dispatch/llm_call`` (the decoding clamp).
"""

from __future__ import annotations

import contextvars
from dataclasses import dataclass
from typing import Any

__all__ = [
    "MAX_INSTRUMENT_DEPTH",
    "InstrumentMode",
    "MeasuredCandidate",
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

    Bound in the OUTER task by ``score_one_candidate``. ``run_inner_cycle`` runs in that
    same task (its inner campaign gets a fresh task only afterwards), so the binding is
    live at the spawn site.
    """

    idx: int
    candidate_id: str
    label: str


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
