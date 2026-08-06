from __future__ import annotations

import logging

from pydantic import ConfigDict, Field

from promptpotter.domain.phases import StopOutcome, stop_reason_outcome
from promptpotter.domain.results import L1_PARSE_FAILURE_TOOLING, CycleResult, RoundResult
from promptpotter.domain.strict_model import StrictModel

logger = logging.getLogger(__name__)


class InnerCycleUnscoreableError(RuntimeError):
    """Also raised by the panel resolver on a missing declaration, not only by the law here.
    The caller drops the cell loudly — an excluded cycle is never scored on zeros."""


class OuterSampleProxies(StrictModel):
    """One field, and it may NOT be defaulted — the absence of a measurement is not a value, so a cycle that cannot fill it
    is floored or excluded, never scored on zeros."""

    model_config = ConfigDict(frozen=True)

    # The ±4 rail is a PLAUSIBILITY bound, not a structural one. This was a difference of two
    # probabilities and so bounded in (−1,1) by construction; it is now a difference of two
    # abilities in LOGITS, where ±1 is a value a strongly-regressing inner cycle really can
    # exceed — and `extra="forbid"` + `Field` would then raise mid-run and kill the outer sample
    # rather than record the regression. ±4 spans well past the ruler's own reach, so it never
    # binds on live data while still stopping a runaway fit at the scoring clamp. A mean over
    # the adopted levels is a convex combination of them, so it cannot leave the range the
    # endpoint could reach and the rail is unchanged by the switch.
    mean_round_delta: float = Field(ge=-4.0, le=4.0)


# The observation keys one outer sample emits — DERIVED from the model, never hand-listed.
OUTER_PROXY_KEYS: tuple[str, ...] = tuple(OuterSampleProxies.model_fields)


def held_levels(result: CycleResult) -> list[float]:
    """ONE denominator for every cell on a panel: dividing by the series length instead makes the
    denominator a per-cell quantity, and the panel then compares two estimands rather than one."""
    levels = result.round_adopted_levels
    if not levels:
        return []
    n = max(result.round_budget, len(levels))
    return levels + [levels[-1]] * (n - len(levels))


def mean_round_delta_se(result: CycleResult) -> float | None:
    """A PRECISION, never a penalty: no ``mean - λ·se`` haircut, never an election rank key. Mean
    of the SEs, not ``σ/√n`` — the levels NEST, so dividing by n would manufacture power."""
    ses = result.round_adopted_level_ses
    if not ses or result.origin_level_se is None:
        return None
    # The DISTINCT levels — the padding `held_levels` adds carries no measurement, so it must not
    # enter the average as if it did. A 2-of-4-round cell contributes the precision of 2 rounds.
    within = sum(ses) / len(ses)
    return float((within**2 + result.origin_level_se**2) ** 0.5)


def _is_evidential(rnd: RoundResult) -> bool:
    """``L1_PARSE_FAILURE_TOOLING`` means the round lost its candidates to an empty optimizer
    response — missing data, not a bad mutation. Scoring it dirty grades provider flakiness."""
    return rnd.l1_parse_failure != L1_PARSE_FAILURE_TOOLING


def no_evidence_reason(result: CycleResult) -> str | None:
    """Only a SUCCESS ``StopOutcome`` is a measurement — anything else was cut short from outside
    the search, and an unexercised optimizer prompt reads as flawless to every aggregate."""
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
    """The one optimizer prompt-OWNED no-evidence shape, so it is FLOORED rather than excluded:
    every round losing its candidates to empty content is reproducible, hence evidence."""
    if stop_reason_outcome(result.stop_reason) is not StopOutcome.SUCCESS:
        return None
    if result.rounds and not any(_is_evidential(r) for r in result.rounds):
        return (
            f"every one of its {len(result.rounds)} L1 round(s) lost its candidates to an "
            "empty optimizer response"
        )
    return None


def _floor_proxies() -> OuterSampleProxies:
    """``-1`` sits at the bottom of the scoring formula's re-anchoring window, so the composed
    fitness is exactly 0.0 — and this ASSIGNED value is the only route to a zeroed cell."""
    return OuterSampleProxies(mean_round_delta=-1.0)


def compute_outer_proxies(result: CycleResult) -> OuterSampleProxies:
    """Raises :class:`InnerCycleUnscoreableError` on a no-fault evidence kill; an optimizer
    prompt-OWNED one returns the floor. Origin and rounds share one fit, so the ruler cancels."""
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
    # field's +/-4 rail is what states that (see `OuterSampleProxies`). The only divisor is the
    # round budget the mean is taken over; nothing normalizes for difficulty.
    levels = held_levels(result)
    return OuterSampleProxies(
        mean_round_delta=sum(levels) / len(levels) - result.origin_level,
    )


__all__ = [
    "OUTER_PROXY_KEYS",
    "InnerCycleUnscoreableError",
    "OuterSampleProxies",
    "compute_outer_proxies",
    "floor_reason",
    "held_levels",
    "mean_round_delta_se",
    "no_evidence_reason",
]
