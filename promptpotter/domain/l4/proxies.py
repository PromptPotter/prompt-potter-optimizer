"""One outer sample's proxy vector, and the law that computes it from a finished inner cycle.

The three questions this module answers, in order, for every inner cycle:

1. Is it meta-prompt-owned no-evidence? → score it at the FLOOR (:func:`floor_reason`).
2. Is it no-fault no-evidence? → EXCLUDE it (:func:`no_evidence_reason`).
3. Otherwise → MEASURE it (:func:`compute_outer_proxies`).

Pure domain: reads a :class:`CycleResult`, returns an :class:`OuterSampleProxies`. No I/O,
no session, no store — which is what keeps the law from drifting into orchestration.
"""

from __future__ import annotations

import logging

from pydantic import BaseModel, ConfigDict, Field

from promptpotter.domain.escalation_signals import NurseOwner
from promptpotter.domain.phases import StopOutcome, stop_reason_outcome
from promptpotter.domain.results import L1_PARSE_FAILURE_TOOLING, CycleResult, RoundResult

logger = logging.getLogger(__name__)


class InnerCycleUnscoreableError(RuntimeError):
    """This inner cycle carries no evidence about the meta-prompt that ran it.

    Raised by the resolver when the panel's declaration is missing, and by the law when a
    cycle's trajectory was cut short by something the meta-prompt does not own. The caller
    drops the panel cell — loudly. It is never scored on zeros.
    """


class OuterSampleProxies(BaseModel):
    """One outer sample's observation vector — what a finished inner cycle says about the
    meta-prompt that ran it. **This type is the governing law**; nothing restates it.

    Bounds are carried where they are provable. ``normalized_gain`` divides a move by the room
    available to make it (``max(origin, 1−origin) ≥ 0.5``), so ``[-1, 1]`` holds by construction
    and the ``ge``/``le`` states a fact rather than clamping one. The quality terms are
    ``1 − mean(rate ∈ [0,1])`` and ``rounds_improved_frac`` is a share. The endpoint deltas and
    the efficiency ratios are genuinely unbounded — a regressing meta-prompt goes negative, and
    lift-per-dollar has no ceiling — so they carry the one bound that always holds: finite.
    ``allow_inf_nan=False`` keeps a ``0/0`` from reaching the scoring clamp, where
    ``min(1.0, nan)`` short-circuits to ``1.0`` and a sample that measured nothing scores perfect.

    Every field is a measurement, and none may be defaulted: the absence of a measurement is not
    a value. A cycle that cannot fill this in is excluded or floored, never scored on zeros.

    ``extra="forbid"`` + ``frozen`` fix the emitted key set to the declared one, so it can be
    diffed against the outer dataset's ``observation_mappings`` rather than drifting from it.

    There is deliberately no ``rounds_to_N`` and no *target* anywhere in this vector. Counting
    rounds-to-a-threshold requires asserting up front how much room the inner benchmark has — an
    assumption that carried no candidate gradient and was wrong to make: a task the inner model
    looks bad at is a task it has not been tuned for yet, not a task with no headroom.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    # `after_N_rounds_delta` is a wire key naming the pipeline_data observation the outer
    # scoring formula reads, so its spelling is not ours to normalize.
    first_round_delta: float = Field(allow_inf_nan=False)
    after_N_rounds_delta: float = Field(allow_inf_nan=False)  # noqa: N815
    normalized_gain: float = Field(ge=-1.0, le=1.0)
    cleanliness: float = Field(ge=0.0, le=1.0)
    diversity_health: float = Field(ge=0.0, le=1.0)
    rounds_improved_frac: float = Field(ge=0.0, le=1.0)
    delta_per_dollar: float = Field(allow_inf_nan=False)
    delta_per_candidate: float = Field(allow_inf_nan=False)
    delta_per_second: float = Field(allow_inf_nan=False)


# The observation keys one outer sample emits — DERIVED from the model, never hand-listed.
OUTER_PROXY_KEYS: tuple[str, ...] = tuple(OuterSampleProxies.model_fields)


def _clamp(x: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, x))


def _mean(xs: list[float]) -> float:
    """Mean of a NON-EMPTY sequence. The empty case is a caller bug, not a zero: every use here
    feeds a proxy the outer formula reads as a positive signal, so ``sum([])/1 -> 0.0`` would
    report an unexercised meta-prompt as perfectly clean. Callers exclude first; this refuses to
    invent the verdict for them."""
    if not xs:
        raise InnerCycleUnscoreableError(
            "a proxy was averaged over no rounds — the cycle should have been excluded"
        )
    return sum(xs) / len(xs)


def _is_evidential(rnd: RoundResult) -> bool:
    """False when the round says nothing about the meta-prompt under test.

    A ``L1_PARSE_FAILURE_TOOLING`` round lost its candidates to an empty optimizer response.
    That is missing data — the same class as a crashed inner cycle. Scoring it dirty makes
    provider flakiness look like a bad mutation."""
    return rnd.l1_parse_failure != L1_PARSE_FAILURE_TOOLING


def _self_heal_rate(rnd: RoundResult) -> float:
    """Share of the round's candidates that tripped the inner self-healing machinery for a
    META-PROMPT-owned reason ∈ [0,1]: a malformed L1 variant (any ``ValidationFailure``) or an
    operator-terminal ``RuntimeFailure`` (a config-deterministic break). A transient-transport
    ``RuntimeFailure`` is provider noise, not the meta-prompt's fault, and is excluded.

    Whatever the inner loop had to self-heal is evidence about the meta-prompt under test, so it
    rides the ``cleanliness`` penalty to the outer optimizer."""
    cands = rnd.candidate_scores
    if not cands:
        return 0.0
    healed = sum(
        1
        for c in cands
        if c.validation_failures
        or any(rf.owner is NurseOwner.OPERATOR for rf in c.runtime_failures)
    )
    return healed / len(cands)


def _round_mode_collapse_rate(rnd: RoundResult) -> float:
    """Per-round mode-collapse ∈ [0,1): share of generated variants the invariant detector nuked
    as no-op / duplicate. A meta-prompt that induces the inner L1 to regurgitate the parent is not
    exploring, and the outer loop should steer away from it."""
    collapsed = rnd.l1_n_no_op + rnd.l1_n_duplicate
    generated = collapsed + rnd.candidates_scored
    return collapsed / generated if generated else 0.0


def _round_problem_rate(rnd: RoundResult) -> float:
    """Per-round dirtiness ∈ [0,1] for an evidential round: inner samples that degraded or came
    back unscoreable, the meta-fault self-heal load, or a round the meta-prompt made unparseable.

    A parse failure is charged to the ROUND, not to a candidate: ``l1_generate`` returns zero
    candidates in exactly that case, so any per-candidate scan is structurally empty and the
    worst possible round — a meta-prompt that makes its own children unreadable — would otherwise
    score perfectly clean."""
    if rnd.l1_parse_failure:
        return 1.0
    health = rnd.health
    struct = 0.0
    if health and health.samples:
        struct = health.degraded_rate + health.no_result_count / health.samples
    struct += _self_heal_rate(rnd)
    return _clamp(struct, 0.0, 1.0)


def no_evidence_reason(result: CycleResult) -> str | None:
    """Why this inner cycle says nothing about the meta-prompt under test — ``None`` if it does.

    THE exclusion decision, asked once. The question is **"did this run produce evidence?"**,
    never "did it fail?". Those differ: a cycle can end without ever running an L1 round (target
    hit at origin, a budget rail, the origin gate, an operator Ctrl+C), and every aggregate then
    reads an *unexercised* meta-prompt as flawless.

    **Only a SUCCESS outcome is a measurement.** :class:`StopOutcome` draws exactly this line —
    SUCCESS means the cycle ended on its own terms (round cap, lives, target, L3 convergence),
    while HALTED/FAILED/PAUSED mean something *outside the search* stopped it. Such a trajectory
    is truncated, and a truncated trajectory is indistinguishable from "this meta-prompt found
    nothing" — so scoring one lets provider mood or a Ctrl+C masquerade as meta-prompt quality.
    Read off the typed table, never a hand-written reason set.

    Deliberately NOT routed to the floor: the floor zeroes the cell, which would punish the
    meta-prompt for a slow provider."""
    outcome = stop_reason_outcome(result.stop_reason)
    if outcome is not StopOutcome.SUCCESS:
        return (
            f"it did not end on its own terms — {outcome} (stop_reason={result.stop_reason}); "
            "its trajectory was cut short by something the meta-prompt does not own"
        )
    if not result.rounds:
        return f"it ran no L1 rounds (stop_reason={result.stop_reason})"
    if result.origin_level is None:
        return "its origin was never scored, so there is no floor to difference its rounds against"
    if not result.round_discovered_levels:
        return "it discovered no levels to difference against its origin"
    # Unmeasured spend is not cheap spend. `delta_per_dollar` divides by cost, so a cost of 0.0
    # pins efficiency to its MAXIMUM — under-reporting spend would make a run score fitter, which
    # is the incentive exactly backwards. Unpriced tokens are the same harm partially applied.
    spend = result.spend
    if spend is None or spend.cost_usd <= 0.0:
        return "it recorded no spend, so its cost-efficiency cannot be measured"
    if spend.unpriced_tokens > 0:
        return (
            f"{spend.unpriced_tokens} of its tokens have no USD rate on file, so its recorded "
            f"cost (${spend.cost_usd:.4f}) understates real spend"
        )
    return None


def floor_reason(result: CycleResult) -> str | None:
    """The one meta-prompt-OWNED no-evidence shape — scored at the FLOOR, never excluded.

    One empty optimizer response is provider noise (``_is_evidential`` drops that round), but a
    cycle whose EVERY L1 round lost its candidates to empty content is the verbose-meta-prompt
    failure mode, reproducible under the inner determinism clamp — so it IS evidence about the
    meta-prompt. Excluding it let a candidate that breaks its own measurement escape penalty, and
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
    """The worst measurable verdict — ASSIGNED (not measured) to a meta-prompt-owned failure.
    ``normalized_gain=-1`` zeroes the formula's lift core and the modulators sit at their floors,
    so the composed fitness is exactly 0.0. This is the ONLY route to a zeroed cell: a *measured*
    cycle reaches −1 only by collapsing the inner ability to nothing, so a zero means "the
    meta-prompt broke its own measurement", never "this seed drew a strong origin"."""
    return OuterSampleProxies(
        first_round_delta=0.0,
        after_N_rounds_delta=0.0,
        normalized_gain=-1.0,
        cleanliness=0.0,
        diversity_health=0.0,
        rounds_improved_frac=0.0,
        delta_per_dollar=0.0,
        delta_per_candidate=0.0,
        delta_per_second=0.0,
    )


def compute_outer_proxies(result: CycleResult, elapsed: float) -> OuterSampleProxies:
    """The composed outer signal from a finished inner cycle — subset-invariant, bounded raw
    terms the outer scoring formula re-weights (the backend never hides the composite).

    Endpoint deltas: ``first_round_delta`` = round-1 discovered lift over origin;
    ``after_N_rounds_delta`` = best discovered lift over origin. Both difference the single-scale
    θ-LCB trajectory (``origin_level`` / ``round_discovered_levels``, built upstream in
    ``discovered_level_trajectory``) — one estimator, so no delta ever subtracts across scales.

    Composed terms, each carrying a candidate gradient (a flat term earns nothing and gets cut):

    - ``normalized_gain`` — best depth as a fraction of the room available to move,
      ``after_n / max(origin, 1−origin)``. Normalized by the room to the REAL ceiling, never by a
      declared target. Dividing by ``(target−origin)`` measured an UPWARD room while a regression
      falls toward zero, so a mild regression on a strong-origin seed detonated to the −1 clamp
      and — the lift core being multiplicative — zeroed the whole cell.
    - ``cleanliness`` / ``diversity_health`` — bounded quality: ``1 − mean`` per-round problem /
      mode-collapse rate. The formula uses them as a modulator that discounts a warning-riddled
      or collapsing campaign without diluting the lift core.
    - ``rounds_improved_frac`` — share of L1 rounds that beat their predecessor.
    - ``delta_per_dollar`` / ``delta_per_candidate`` / ``delta_per_second`` — efficiency: best
      depth over spend / candidates / wall-time. A verbose meta-prompt burns more for the same
      lift and scores lower.

    Deltas may be negative on a regressing meta-prompt (levels are not floored at origin), so the
    efficiency ratios can be negative too; the formula recentres and clamps those.

    Raises :class:`InnerCycleUnscoreableError` when the cycle carries no evidence for a no-fault
    reason; a meta-prompt-owned evidence kill returns the floor instead. Asking those two first is
    what makes "absent" unrepresentable here."""
    if (floor := floor_reason(result)) is not None:
        logger.warning("inner cycle scored at the floor: %s", floor)
        return _floor_proxies()
    if (reason := no_evidence_reason(result)) is not None:
        # Loud, never silent: this drops a panel cell, and a dropped cell that reads as "covered"
        # is worse than no cell at all.
        logger.warning("inner cycle EXCLUDED (no evidence about the meta-prompt): %s", reason)
        raise InnerCycleUnscoreableError(reason)

    assert result.origin_level is not None  # guaranteed by no_evidence_reason
    origin = result.origin_level
    levels = result.round_discovered_levels
    first = levels[0] - origin
    after_n = max(levels) - origin

    # Levels are in [0,1], so `max(origin, 1-origin) >= 0.5` and this is bounded in [-1,1] BY
    # CONSTRUCTION — structural, so there is no clamp and no degenerate denominator.
    normalized_gain = after_n / max(origin, 1.0 - origin)

    # Rounds carrying no evidence are dropped, not scored — an all-tooling cycle already left via
    # `no_evidence_reason`, so `evidential` is non-empty here.
    evidential = [r for r in result.rounds if _is_evidential(r)]
    cleanliness = 1.0 - _mean([_round_problem_rate(r) for r in evidential])
    diversity_health = 1.0 - _mean([_round_mode_collapse_rate(r) for r in evidential])
    rounds_improved_frac = _mean([1.0 if r.improved else 0.0 for r in evidential])

    # No `max(cost, eps)` floor: `no_evidence_reason` already refused a cycle whose spend is zero
    # or under-priced, so the divisor is a real, fully-priced cost.
    assert result.spend is not None  # guaranteed by no_evidence_reason
    cost = result.spend.cost_usd
    n_cand = sum(r.candidates_scored for r in result.rounds)
    delta_per_dollar = after_n / cost
    # n_cand == 0 only when no candidate was ever scored — and then the trajectory never rose
    # above origin, so `after_n` is 0.0 and this is 0/0. Say 0.0 outright instead of letting a
    # `max(n_cand, 1)` divisor pretend one candidate existed.
    delta_per_candidate = after_n / n_cand if n_cand else 0.0
    delta_per_second = after_n / max(elapsed, 1e-6)

    return OuterSampleProxies(
        first_round_delta=first,
        after_N_rounds_delta=after_n,
        normalized_gain=normalized_gain,
        cleanliness=cleanliness,
        diversity_health=diversity_health,
        rounds_improved_frac=rounds_improved_frac,
        delta_per_dollar=delta_per_dollar,
        delta_per_candidate=delta_per_candidate,
        delta_per_second=delta_per_second,
    )


__all__ = [
    "OUTER_PROXY_KEYS",
    "InnerCycleUnscoreableError",
    "OuterSampleProxies",
    "compute_outer_proxies",
    "floor_reason",
    "no_evidence_reason",
]
