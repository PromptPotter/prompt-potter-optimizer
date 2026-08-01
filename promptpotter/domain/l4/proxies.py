"""One outer sample's proxy vector, and the law that computes it from a finished inner cycle.

The three questions this module answers, in order, for every inner cycle:

1. Is it optimizer prompt-owned no-evidence? → score it at the FLOOR (:func:`floor_reason`).
2. Is it no-fault no-evidence? → EXCLUDE it (:func:`no_evidence_reason`).
3. Otherwise → MEASURE it (:func:`compute_outer_proxies`).

Pure domain: reads a :class:`CycleResult`, returns an :class:`OuterSampleProxies`. No I/O,
no session, no store — which is what keeps the law from drifting into orchestration.
"""

from __future__ import annotations

import logging

from pydantic import ConfigDict, Field

from promptpotter.domain.phases import StopOutcome, stop_reason_outcome
from promptpotter.domain.results import L1_PARSE_FAILURE_TOOLING, CycleResult, RoundResult
from promptpotter.domain.strict_model import StrictModel

logger = logging.getLogger(__name__)


class InnerCycleUnscoreableError(RuntimeError):
    """This inner cycle carries no evidence about the optimizer prompt that ran it.

    Raised by the resolver when the panel's declaration is missing, and by the law when a
    cycle's trajectory was cut short by something the optimizer prompt does not own. The caller
    drops the panel cell — loudly. It is never scored on zeros.
    """


class OuterSampleProxies(StrictModel):
    """One outer sample's observation vector — what a finished inner cycle says about the
    optimizer prompt that ran it. **This type is the governing law**; nothing restates it.

    **It is one number, and that is the design.** The vector carried eight fields and the outer
    formula composed four of them; measured over a full 39-cell panel, only this one discriminated
    between optimizer prompts. ``cleanliness`` put twice as much of its variance into the SEED as into
    the arm (30.9% vs 15.4%) — it was grading which data a cell drew, not which optimizer prompt ran
    it, settling the question `l4-outer-loop.md` left open. ``diversity_health`` never left the
    top fifth of its range, so it carried no candidate gradient at all, which the spec's own
    governing law already disqualifies. ``delta_per_dollar`` correlated 0.958 with the lift core
    and flipped no ordering — it was this delta counted a second time. ``rounds_improved_frac``
    flipped nothing. Each was a multiplier with authority over an ordering it could not justify.

    **It reads the incumbent the inner search ADOPTED, never its proposals** — what a round
    delivers is what it crowns, and the arms it discarded are the price of finding that. The full
    argument, and the measurement that forced it, sit on ``exploration.adopted_level_trajectory``.

    **It needs no denominator.** Every level is an ability θ in LOGITS on the cycle's fixed δ
    ruler, and the delta is one level minus another — a difference on one interval scale, which is
    the property that makes it comparable across seeds of different origin strength. It is NOT
    bounded by construction: the projection back through ``ruler_expected_accuracy`` that once
    made these probabilities in (0,1) was dropped precisely because the sigmoid is flat near the
    ceiling and compressed the same gain for a strong origin. ``ge``/``le`` below are therefore a
    plausibility rail, not a statement of structure.

    Per-cell difficulty is already modelled where it belongs — the round-winner election and
    PoBB elimination fit a Rasch θ with an explicit per-cell δ, and the outer verdict pairs
    within-cell against the cached origin.

    **The quality events still act; they simply act structurally rather than twice.** A cycle
    whose every round lost its candidates to an empty optimizer response goes to the FLOOR
    (:func:`floor_reason`); a collapsed arm is dropped from the inner election and eliminated at
    PoBB. Charging them a second time, graded, in the fitness was a second mechanism for a job the
    loop already does.

    The field is a measurement and may not be defaulted: the absence of a measurement is not a
    value. A cycle that cannot fill it in is excluded or floored, never scored on zeros.

    ``extra="forbid"`` + ``frozen`` fix the emitted key set to the declared one, so it can be
    diffed against the outer dataset's ``observation_mappings`` rather than drifting from it.

    There is deliberately no ``rounds_to_N`` and no *target* here. Counting rounds-to-a-threshold
    requires asserting up front how much room the inner benchmark has — an assumption that carries
    no candidate gradient and is wrong to make: a task the inner model looks bad at is a task it
    has not been tuned for yet, not a task with no headroom.

    Also deliberately absent: a ``peak_delta`` / lift-and-hold reading. ``final_delta`` reads the
    ADOPTED incumbent, so a peak the inner search later walked away from scores as nothing — on
    the 39-cell panel that is 17 cells, mean gap +0.052. It is a one-line derivation from
    ``round_adopted_levels``, and it is left out because it changes the ESTIMAND: under a pure
    peak ruler the origin loses. That is a decision to take on measurement, not in passing.
    """

    model_config = ConfigDict(frozen=True)

    # The ±4 rail is a PLAUSIBILITY bound, not a structural one. This was a difference of two
    # probabilities and so bounded in (−1,1) by construction; it is now a difference of two
    # abilities in LOGITS, where ±1 is a value a strongly-regressing inner cycle really can
    # exceed — and `extra="forbid"` + `Field` would then raise mid-run and kill the outer sample
    # rather than record the regression. ±4 spans well past the ruler's own reach, so it never
    # binds on live data while still stopping a runaway fit at the scoring clamp.
    final_delta: float = Field(ge=-4.0, le=4.0)


# The observation keys one outer sample emits — DERIVED from the model, never hand-listed.
OUTER_PROXY_KEYS: tuple[str, ...] = tuple(OuterSampleProxies.model_fields)


def _is_evidential(rnd: RoundResult) -> bool:
    """False when the round says nothing about the optimizer prompt under test.

    A ``L1_PARSE_FAILURE_TOOLING`` round lost its candidates to an empty optimizer response.
    That is missing data — the same class as a crashed inner cycle. Scoring it dirty makes
    provider flakiness look like a bad mutation."""
    return rnd.l1_parse_failure != L1_PARSE_FAILURE_TOOLING


def no_evidence_reason(result: CycleResult) -> str | None:
    """Why this inner cycle says nothing about the optimizer prompt under test — ``None`` if it does.

    THE exclusion decision, asked once. The question is **"did this run produce evidence?"**,
    never "did it fail?". Those differ: a cycle can end without ever running an L1 round (target
    hit at origin, a budget rail, the origin gate, an operator Ctrl+C), and every aggregate then
    reads an *unexercised* optimizer prompt as flawless.

    **Only a SUCCESS outcome is a measurement.** :class:`StopOutcome` draws exactly this line —
    SUCCESS means the cycle ended on its own terms (round cap, lives, target, L3 convergence),
    while HALTED/FAILED/PAUSED mean something *outside the search* stopped it. Such a trajectory
    is truncated, and a truncated trajectory is indistinguishable from "this optimizer prompt found
    nothing" — so scoring one lets provider mood or a Ctrl+C masquerade as optimizer prompt quality.
    Read off the typed table, never a hand-written reason set.

    Deliberately NOT routed to the floor: the floor zeroes the cell, which would punish the
    optimizer prompt for a slow provider."""
    outcome = stop_reason_outcome(result.stop_reason)
    if outcome is not StopOutcome.SUCCESS:
        return (
            f"it did not end on its own terms — {outcome} (stop_reason={result.stop_reason}); "
            "its trajectory was cut short by something the optimizer prompt does not own"
        )
    if not result.rounds:
        return f"it ran no L1 rounds (stop_reason={result.stop_reason})"
    if result.origin_level is None:
        return "its origin was never scored, so there is no floor to difference its rounds against"
    if not result.round_adopted_levels:
        return "it adopted no levels to difference against its origin"
    return None


def floor_reason(result: CycleResult) -> str | None:
    """The one optimizer prompt-OWNED no-evidence shape — scored at the FLOOR, never excluded.

    One empty optimizer response is provider noise (``_is_evidential`` drops that round), but a
    cycle whose EVERY L1 round lost its candidates to empty content is the verbose-optimizer prompt
    failure mode, reproducible under the inner determinism clamp — so it IS evidence about the
    optimizer prompt. Excluding it let a candidate that breaks its own measurement escape penalty, and
    left an un-crownable, un-eliminable zombie arm burning budget every remaining round.

    Only a cycle that ended on its OWN terms can be floored — anything else was cut short by a
    rail, so its empty rounds may just be the ones it got to run."""
    if stop_reason_outcome(result.stop_reason) is not StopOutcome.SUCCESS:
        return None
    if result.rounds and not any(_is_evidential(r) for r in result.rounds):
        return (
            f"every one of its {len(result.rounds)} L1 round(s) lost its candidates to an "
            "empty optimizer response"
        )
    return None


def _floor_proxies() -> OuterSampleProxies:
    """The worst measurable verdict — ASSIGNED (not measured) to an optimizer prompt-owned failure.

    ``final_delta = -1`` sits exactly at the bottom of the scoring formula's re-anchoring window,
    so the composed fitness is exactly 0.0 — and this is the ONLY route to a zeroed cell. A
    measured cycle reaches -1 logit only by collapsing the inner ability outright, so a zero means
    "the optimizer prompt broke its own measurement", never "this seed drew a strong origin".

    This is the ONLY route to a zeroed cell: a *measured* cycle reaches −1 only by collapsing the
    inner ability to nothing, so a zero means "the optimizer prompt broke its own measurement", never
    "this seed drew a strong origin"."""
    return OuterSampleProxies(final_delta=-1.0)


def compute_outer_proxies(result: CycleResult) -> OuterSampleProxies:
    """The outer signal from a finished inner cycle — one subset-invariant raw term the outer
    scoring formula re-anchors (the backend never hides the composite).

    ONE reading of ONE series: the ability of the incumbent each round adopted, in LOGITS on the
    cycle's locked ruler (``origin_level`` / ``round_adopted_levels``, built upstream in
    ``adopted_level_trajectory``). ``final_delta`` is where the search ENDED minus its origin —
    what the optimizer prompt actually delivered. One estimator, one interval scale, so the delta is
    not compressed by where its origin happened to sit.

    Why the other seven readings are gone is the type's own docstring; the short version is that
    a 39-cell panel measured each of them and none discriminated between optimizer prompts.

    The delta may be negative on a regressing optimizer prompt — levels are not floored at origin —
    and the scoring formula re-anchors it into [0,1].

    Raises :class:`InnerCycleUnscoreableError` when the cycle carries no evidence for a no-fault
    reason; an optimizer prompt-owned evidence kill returns the floor instead. Asking those two first is
    what makes "absent" unrepresentable here."""
    if (floor := floor_reason(result)) is not None:
        logger.warning("inner cycle scored at the floor: %s", floor)
        return _floor_proxies()
    if (reason := no_evidence_reason(result)) is not None:
        # Loud, never silent: this drops a panel cell, and a dropped cell that reads as "covered"
        # is worse than no cell at all.
        logger.warning("inner cycle EXCLUDED (no evidence about the optimizer prompt): %s", reason)
        raise InnerCycleUnscoreableError(reason)

    assert result.origin_level is not None  # guaranteed by no_evidence_reason
    # Every level is an ability in LOGITS on the fixed ruler, so a delta is a difference of two
    # unbounded quantities — plausibly within a couple of logits, structurally within none. The
    # field's +/-4 rail is what states that (see `OuterSampleProxies`); there is still nothing to
    # divide by.
    return OuterSampleProxies(final_delta=result.round_adopted_levels[-1] - result.origin_level)


__all__ = [
    "OUTER_PROXY_KEYS",
    "InnerCycleUnscoreableError",
    "OuterSampleProxies",
    "compute_outer_proxies",
    "floor_reason",
    "no_evidence_reason",
]
