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

from pydantic import ConfigDict, Field

from promptpotter.domain.escalation_signals import NurseOwner
from promptpotter.domain.phases import StopOutcome, stop_reason_outcome
from promptpotter.domain.results import L1_PARSE_FAILURE_TOOLING, CycleResult, RoundResult
from promptpotter.domain.scoring import is_answer_collapsed
from promptpotter.domain.strict_model import StrictModel

logger = logging.getLogger(__name__)


class InnerCycleUnscoreableError(RuntimeError):
    """This inner cycle carries no evidence about the meta-prompt that ran it.

    Raised by the resolver when the panel's declaration is missing, and by the law when a
    cycle's trajectory was cut short by something the meta-prompt does not own. The caller
    drops the panel cell — loudly. It is never scored on zeros.
    """


class OuterSampleProxies(StrictModel):
    """One outer sample's observation vector — what a finished inner cycle says about the
    meta-prompt that ran it. **This type is the governing law**; nothing restates it.

    **The lift terms read the incumbent the inner search ADOPTED, never its proposals** — what a
    round delivers is what it crowns, and the arms it discarded are the price of finding that.
    ``final_delta`` is what the search ended up with and is the primary reading; the other two
    are the same series sampled differently (``after_N_rounds_delta`` averages it, so a fast
    climber that holds outscores a slow one with the same asymptote; ``first_round_delta`` is
    round 1 alone). The full argument, and the measurement that forced it, sit on
    ``exploration.adopted_level_trajectory``.

    **The lift terms need no denominator.** Every level is an
    ability θ in LOGITS on the cycle's fixed δ ruler, and the delta is one level minus another —
    so it is a difference on one interval scale, which is the property that makes it comparable
    across seeds of different origin strength. It is NOT bounded by construction: the projection
    back through ``ruler_expected_accuracy`` that once made these probabilities in (0,1) was
    dropped precisely because the sigmoid is flat near the ceiling and compressed the same gain
    for a strong origin. ``ge``/``le`` below are therefore a plausibility rail, not a statement
    of structure — read the field note.

    Per-cell difficulty is already modelled where it belongs — the round-winner election and
    PoBB elimination fit a Rasch θ with an explicit per-cell δ, and the outer verdict pairs
    within-cell against the cached origin.

    The quality terms are ``1 − mean(rate ∈ [0,1])`` and ``rounds_improved_frac`` is a share. The
    efficiency ratios are genuinely unbounded — lift-per-dollar has no ceiling — so they carry the
    one bound that always holds: finite. ``allow_inf_nan=False`` keeps a ``0/0`` from reaching the
    scoring clamp, where ``min(1.0, nan)`` short-circuits to ``1.0`` and a sample that measured
    nothing scores perfect.

    ``delta_per_dollar`` divides by ``CycleSpend.incurred_usd`` — what the search would cost
    cold — and NOT by the bill. The two differ because the caches are tenant-global: replaying a
    cycle we already ran bills nothing while doing identical work. That gap is not random. The
    origin arm is the one that replays (a variant meta-prompt writes different prompts, so its
    every content hash is new), so a bill-denominated efficiency term would reward the origin for
    our own measurement history on precisely the cells we know best.

    There is deliberately no ``delta_per_second``. Wall-clock cannot be made cache-invariant the
    way cost can — a replayed cycle really did take four seconds instead of five minutes, and
    there is no "notional" elapsed time to recover. A per-second term therefore measures our cache
    rather than the meta-prompt, and unlike cost there is nothing to divide by instead. Lift per
    unit of *work* survives as ``delta_per_candidate``, which counts candidates and so is
    cache-invariant by construction.

    Every field is a measurement, and none may be defaulted: the absence of a measurement is not
    a value. A cycle that cannot fill this in is excluded or floored, never scored on zeros.

    ``extra="forbid"`` + ``frozen`` fix the emitted key set to the declared one, so it can be
    diffed against the outer dataset's ``observation_mappings`` rather than drifting from it.

    There is deliberately no ``rounds_to_N`` and no *target* anywhere in this vector. Counting
    rounds-to-a-threshold requires asserting up front how much room the inner benchmark has — an
    assumption that carries no candidate gradient and is wrong to make: a task the inner model
    looks bad at is a task it has not been tuned for yet, not a task with no headroom.
    """

    model_config = ConfigDict(frozen=True)

    # `after_N_rounds_delta` is a wire key naming the pipeline_data observation the outer
    # scoring formula reads, so its spelling is not ours to normalize.
    #
    # The ±4 rail is a PLAUSIBILITY bound, not a structural one. These were differences of two
    # probabilities and so bounded in (−1,1) by construction; they are now differences of two
    # abilities in LOGITS, where ±1 is a value a strongly-regressing inner cycle really can
    # exceed — and `extra="forbid"` + `Field` would then raise mid-run and kill the outer sample
    # rather than record the regression. ±4 spans well past the ruler's own reach, so it never
    # binds on live data while still stopping a runaway fit at the scoring clamp.
    first_round_delta: float = Field(ge=-4.0, le=4.0)
    after_N_rounds_delta: float = Field(ge=-4.0, le=4.0)  # noqa: N815
    final_delta: float = Field(ge=-4.0, le=4.0)
    cleanliness: float = Field(ge=0.0, le=1.0)
    diversity_health: float = Field(ge=0.0, le=1.0)
    rounds_improved_frac: float = Field(ge=0.0, le=1.0)
    delta_per_dollar: float = Field(allow_inf_nan=False)
    delta_per_candidate: float = Field(allow_inf_nan=False)


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


def _judged(rnd: RoundResult, n_min: int) -> list[str]:
    """Candidate ids that earned a verdict — those measured on at least ``n_min`` samples.

    The denominator for every ``cleanliness`` rate, and the reason it is not simply "every
    candidate". PoBB cuts a bad arm after a handful of draws, which is the search WORKING; below
    ``elimination_n_min`` we stopped before we could tell a collapse from small-n noise, so
    charging that arm as dirt prices exploration as damage — the same defect that made the lift
    core negative for exploring generators. Measured on ``promptpotter-self__d8b5be``: the seed
    that gained 30 accuracy points sat on the formula's cleanliness FLOOR while the seed that
    gained 4.6 scored 0.79, purely because the first explored harder and its duds were cut sooner.

    Empty when no arm cleared the floor — the round then charges nothing rather than dividing by
    a set nobody measured."""
    return [cid for cid, rows in rnd.all_candidate_results.items() if len(rows) >= n_min]


def _self_heal_rate(rnd: RoundResult) -> float:
    """Share of the round's candidates that tripped the inner self-healing machinery for a
    META-PROMPT-owned reason ∈ [0,1]: a malformed L1 variant (any ``ValidationFailure``) or an
    operator-terminal ``RuntimeFailure`` (a config-deterministic break). A transient-transport
    ``RuntimeFailure`` is provider noise, not the meta-prompt's fault, and is excluded.

    No ``n_min`` gate, unlike :func:`_answer_collapse_rate`: both numerators here are structural
    facts known before a single sample runs — a variant is malformed or it is not — so sample
    count says nothing about whether the verdict is trustworthy, and filtering on it would simply
    hide the arms that broke earliest.

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
    as no-op / duplicate / repeat. A meta-prompt that induces the inner L1 to regurgitate the
    parent — or to keep re-proposing an idea the cycle already measured and lost — is not
    exploring, and the outer loop should steer away from it.

    ``l1_n_repeat`` belongs in this sum precisely BECAUSE it is the subtlest form of the same
    disease: a meta-prompt can score a clean no-op/duplicate rate while driving the inner loop
    to spend every round paraphrasing one dead hypothesis. That is the failure mode L4 exists
    to detect, and until repeats were counted it was the one collapse the proxy could not see.
    """
    collapsed = rnd.l1_n_no_op + rnd.l1_n_duplicate + rnd.l1_n_repeat
    generated = collapsed + rnd.candidates_scored
    return collapsed / generated if generated else 0.0


def _answer_collapse_rate(rnd: RoundResult, n_min: int) -> float:
    """Share of the round's JUDGED candidates that answered ONE label to every sample ∈ [0,1].

    The SEMANTIC half of round dirtiness. Everything else in :func:`_round_problem_rate` counts
    plumbing — degraded transport, unscoreable results, self-heals — and a candidate that emits
    perfectly-formed constant garbage trips none of it. Measured live: a round whose BOTH
    candidates answered ``Uncertain`` to all 8 samples scored ``degraded_rate 0.0``,
    ``no_result_count 0``, ``structural_count 0`` — a flawless 1.0 cleanliness for a round in
    which nothing was actually answered.

    Charged to the ROUND rather than the candidate because that is the fact the outer loop needs:
    the candidates themselves are dropped before the election (``l1/score/winner.py``, where a
    collapsed arm stops being electable), so without this the meta-prompt that induced the
    collapse would pay nothing at all for it.

    Gated on ``_judged`` — see there for why an arm PoBB cut early is not evidence of collapse."""
    judged = _judged(rnd, n_min)
    if not judged:
        return 0.0
    return sum(1 for cid in judged if is_answer_collapsed(rnd.all_candidate_results[cid])) / len(
        judged
    )


def _round_problem_rate(rnd: RoundResult, n_min: int) -> float:
    """Per-round dirtiness ∈ [0,1] for an evidential round: inner samples that degraded or came
    back unscoreable, candidates that collapsed to a constant answer, the meta-fault self-heal
    load, or a round the meta-prompt made unparseable.

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
    struct += _answer_collapse_rate(rnd, n_min)
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
    if not result.round_adopted_levels:
        return "it adopted no levels to difference against its origin"
    # Unmeasured cost is not cheap cost. `delta_per_dollar` divides by it, so a cost of 0.0 pins
    # efficiency to its MAXIMUM — under-reporting would make a run score fitter, which is the
    # incentive exactly backwards. Unpriced tokens are the same harm partially applied.
    #
    # The divisor is INCURRED cost, never the bill. The caches are tenant-global, so an arm that
    # replays a cycle we already ran bills $0 while doing identical work — and it is always the
    # ORIGIN arm that replays (a variant meta-prompt writes new prompts, so every hash is new).
    # Billing the divisor would hand the origin infinite efficiency on exactly the cells we have
    # measured before: a bias, not noise.
    spend = result.spend
    if spend is None or spend.incurred_usd <= 0.0:
        return "it recorded no LLM calls, so its cost-efficiency cannot be measured"
    if spend.incurred_unpriced_tokens > 0:
        return (
            f"{spend.incurred_unpriced_tokens} of its tokens have no USD rate on file, so its "
            f"incurred cost (${spend.incurred_usd:.4f}) understates what the search costs"
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

    ``after_N_rounds_delta = -1`` zeroes the formula's lift core and the modulators sit at their
    floors, so the composed fitness is exactly 0.0. The floor rides the LIFT CORE, whichever field
    that is.

    This is the ONLY route to a zeroed cell: a *measured* cycle reaches −1 only by collapsing the
    inner ability to nothing, so a zero means "the meta-prompt broke its own measurement", never
    "this seed drew a strong origin"."""
    return OuterSampleProxies(
        first_round_delta=-1.0,
        after_N_rounds_delta=-1.0,
        final_delta=-1.0,
        cleanliness=0.0,
        diversity_health=0.0,
        rounds_improved_frac=0.0,
        delta_per_dollar=0.0,
        delta_per_candidate=0.0,
    )


def compute_outer_proxies(result: CycleResult) -> OuterSampleProxies:
    """The composed outer signal from a finished inner cycle — subset-invariant, bounded raw
    terms the outer scoring formula re-weights (the backend never hides the composite).

    Three readings of ONE series, the ability of the incumbent each round adopted, in LOGITS on
    the cycle's locked ruler (``origin_level`` / ``round_adopted_levels``, built upstream in
    ``adopted_level_trajectory``). One estimator and one interval scale, so no delta subtracts
    across scales and none is compressed by where its origin sat:

    - ``final_delta`` — where the search ENDED, minus origin. The primary lift reading: it is
      what the meta-prompt actually delivered.
    - ``after_N_rounds_delta`` — the same series averaged, so it measures RATE: a meta-prompt
      that climbs fast spends more of its rounds high and out-scores a slow climber with the
      same asymptote.
    - ``first_round_delta`` — round 1 alone; a signal several times smaller than one candidate's
      θ_se, so it is emitted for the record rather than leaned on.

    - ``cleanliness`` / ``diversity_health`` — bounded quality: ``1 − mean`` per-round problem /
      mode-collapse rate. The formula uses them as a modulator that discounts a warning-riddled
      or collapsing campaign without diluting the lift. Cleanliness charges only arms that earned
      a verdict — ``result.elimination_n_min`` samples (:func:`_judged`) — because an arm PoBB cut
      after a handful of draws is the search working, not dirt.
    - ``rounds_improved_frac`` — share of L1 rounds that beat their predecessor.
    - ``delta_per_dollar`` / ``delta_per_candidate`` — efficiency: lift over incurred cost / over
      candidates burned. A verbose meta-prompt pays more for the same lift, scores lower. Both
      divisors are cache-invariant; wall-time is not, which is why there is no per-second twin.

    Each composed term must carry a candidate gradient; a flat term earns nothing and gets cut.

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
    levels = result.round_adopted_levels
    # Every level is an ability in LOGITS on the fixed ruler, so a delta is a difference of two
    # unbounded quantities — plausibly within a couple of logits, structurally within none. The
    # field's +/-4 rail is what states that (see `OuterSampleProxies`); there is still nothing to
    # divide by.
    first = levels[0] - origin
    final = levels[-1] - origin
    after_n = _mean(levels) - origin

    # Rounds carrying no evidence are dropped, not scored — an all-tooling cycle already left via
    # `no_evidence_reason`, so `evidential` is non-empty here.
    evidential = [r for r in result.rounds if _is_evidential(r)]
    n_min = result.elimination_n_min
    cleanliness = 1.0 - _mean([_round_problem_rate(r, n_min) for r in evidential])
    diversity_health = 1.0 - _mean([_round_mode_collapse_rate(r) for r in evidential])
    rounds_improved_frac = _mean([1.0 if r.improved else 0.0 for r in evidential])

    # No `max(cost, eps)` floor: `no_evidence_reason` already refused a cycle whose incurred cost
    # is zero or under-priced, so the divisor is a real, fully-priced cost.
    assert result.spend is not None  # guaranteed by no_evidence_reason
    cost = result.spend.incurred_usd
    n_cand = sum(r.candidates_scored for r in result.rounds)
    # Both ratios divide `final` — the lift the search DELIVERED — not the rate-flavoured mean:
    # "improvement per dollar" asks what you got for what you paid, and the mean answers a
    # different question (how fast you got there), which the formula already reads separately.
    delta_per_dollar = final / cost
    # n_cand == 0 only when no candidate was ever scored — and then the trajectory never rose
    # above origin, so `final` is 0.0 and this is 0/0. Say 0.0 outright instead of letting a
    # `max(n_cand, 1)` divisor pretend one candidate existed.
    delta_per_candidate = final / n_cand if n_cand else 0.0

    return OuterSampleProxies(
        first_round_delta=first,
        after_N_rounds_delta=after_n,
        final_delta=final,
        cleanliness=cleanliness,
        diversity_health=diversity_health,
        rounds_improved_frac=rounds_improved_frac,
        delta_per_dollar=delta_per_dollar,
        delta_per_candidate=delta_per_candidate,
    )


__all__ = [
    "OUTER_PROXY_KEYS",
    "InnerCycleUnscoreableError",
    "OuterSampleProxies",
    "compute_outer_proxies",
    "floor_reason",
    "no_evidence_reason",
]
