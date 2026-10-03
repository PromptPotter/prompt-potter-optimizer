"""L1Stats — per-cycle L1 fitness statistics, pure aggregation. ``round_1_verdict`` is the round-1 halt gate, read by
``review.md`` and the L4 outer loop."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Literal

from promptpotter.application.optimizers.nodes import CheckResult, ReviewReading, ReviewStat
from promptpotter.application.optimizers.potter.escalation.state import exploration_budget
from promptpotter.application.optimizers.potter.knobs import potter_knobs
from promptpotter.application.optimizers.potter.records import PotterRoundState
from promptpotter.application.optimizers.potter.validators.behavior_base import ValidatorContext
from promptpotter.application.optimizers.potter.validators.l1_behavior import (
    CHECK_REGISTRY,
    extract_l1_variants,
    run_all_checks,
)
from promptpotter.application.optimizers.potter.validators.l2_behavior import run_all_l2_checks
from promptpotter.application.views.render.optimizer_prompt_text import (
    format_l1_critique_for_prompt,
)
from promptpotter.domain.optimizer_state import PARSE_FAILURE_CHARGED
from promptpotter.domain.results import OptimizerFact, RoundResult, invariant_collapses

if TYPE_CHECKING:
    from promptpotter.application.optimizer_manifest import SelectedOptimizer

__all__ = ["L1Stats", "compute_l1_stats", "review_reading", "round_facts"]

# The four the verdict can take, typed rather than described: the L4 outer loop reads this, so an
# arm nothing emits is a measurement nobody can get and one nothing checks is a typo that ships.
RoundOneVerdict = Literal["healthy", "degraded", "broken", "unknown"]


@dataclass(frozen=True)
class L1Stats:
    """``None`` on any rate means NOT MEASURED and renders as a dash. Never 0.0 (nothing yielded) or 1.0 (all passed): a
    cycle with no rounds did not fail to yield, and a rate over zero checks is not a clean bill of health."""

    yield_rate: float | None
    top_lift_mean: float | None
    behavior_pass_rate: float | None
    stagnation_max: int
    l2_fires: int
    # `l2_context` optimizer prompt conformance — None when L2 never fired, so the reader
    # doesn't have to cross-check `l2_fires` to know a 1.0 was vacuous.
    l2_behavior_pass_rate: float | None
    round_1_verdict: RoundOneVerdict


def compute_l1_stats(
    rounds: list[RoundResult],
    *,
    origin_composite_fitness: float | None,
    behavior_results: list[list[CheckResult]],
    l2_behavior_results: list[list[CheckResult]] | None = None,
) -> L1Stats:
    yield_rate = _mean_yield_rate(rounds)
    top_lifts = _top_lifts(rounds, origin_composite_fitness)
    top_lift_mean = sum(top_lifts) / len(top_lifts) if top_lifts else None
    stagnation_max = _max_stagnation_streak(top_lifts)
    behavior_pass_rate = _behavior_pass_rate(behavior_results)
    l2_behavior_pass_rate = _behavior_pass_rate(l2_behavior_results or [])
    # A round's L2 checks are empty exactly where L2 did not fire (`_behavior_per_round`).
    l2_fires = sum(1 for checks in l2_behavior_results or [] if checks)
    round_1_verdict = _compute_round_1_verdict(
        rounds,
        round_1_behavior=behavior_results[0] if behavior_results else [],
    )
    return L1Stats(
        yield_rate=yield_rate,
        top_lift_mean=top_lift_mean,
        behavior_pass_rate=behavior_pass_rate,
        stagnation_max=stagnation_max,
        l2_fires=l2_fires,
        l2_behavior_pass_rate=l2_behavior_pass_rate,
        round_1_verdict=round_1_verdict,
    )


def review_reading(
    selected: SelectedOptimizer,
    rounds: list[RoundResult],
    audits: list[dict[str, Any] | None],
    *,
    context_object: list[str],
    origin_composite_fitness: float | None,
) -> ReviewReading:
    audits = [*audits, *[None] * (len(rounds) - len(audits))]
    l1_checks, l2_checks = _behavior_per_round(
        rounds, audits, context_object, potter_knobs(selected).escalation.l1_patience
    )
    stats = compute_l1_stats(
        rounds,
        origin_composite_fitness=origin_composite_fitness,
        behavior_results=l1_checks,
        l2_behavior_results=l2_checks,
    )
    return ReviewReading(
        checks=l1_checks,
        check_ids=tuple(CHECK_REGISTRY),
        verdict=ReviewStat("round-1 conformance", stats.round_1_verdict),
        stats=(
            ReviewStat("yield_rate", stats.yield_rate, ".2f"),
            ReviewStat("top_lift_mean", stats.top_lift_mean, "+.4f"),
            ReviewStat("behavior_pass_rate", stats.behavior_pass_rate, ".2f"),
            ReviewStat("l2_behavior_pass_rate", stats.l2_behavior_pass_rate, ".2f"),
            ReviewStat("stagnation_max", stats.stagnation_max),
            ReviewStat("l2_fires", stats.l2_fires),
        ),
        variants=[extract_l1_variants(audit) for audit in audits],
        feedback=[
            format_l1_critique_for_prompt(
                r.optimizer_state.payload_as(PotterRoundState).critique
            ).strip()
            for r in rounds
        ],
    )


_COLLAPSE_WORDS = {"no_op_variant": "no-op", "duplicate_variant": "dup", "repeat_variant": "repeat"}


def round_facts(round_result: RoundResult) -> list[OptimizerFact]:
    """Potter's words about a round: L1's yield where a proposal collapsed — a full yield is no
    news — and the critique the round hands its next generation."""
    state = round_result.optimizer_state.payload_as(PotterRoundState)
    facts: list[OptimizerFact] = []
    if state.l1_yield < 1.0:
        collapses = invariant_collapses(round_result.candidate_scores)
        n_total = round_result.candidates_scored
        n_valid = max(0, n_total - sum(collapses.get(r, 0) for r in _COLLAPSE_WORDS))
        bits = ", ".join(
            f"{collapses[r]} {w}" for r, w in _COLLAPSE_WORDS.items() if collapses.get(r)
        )
        text = f"{n_valid}/{n_total} ({bits})"
        facts.append(
            OptimizerFact(
                key="l1_yield", label="L1 yield", text=text, value=state.l1_yield, kind="stat"
            )
        )
    if critique := format_l1_critique_for_prompt(state.critique):
        facts.append(
            OptimizerFact(key="critique", label="Critique", text=critique, value=None, kind="note")
        )
    return facts


def _behavior_per_round(
    rounds: list[RoundResult],
    audits: list[dict[str, Any] | None],
    context_object: list[str],
    l1_patience: int,
) -> tuple[list[list[CheckResult]], list[list[CheckResult]]]:
    """Per-round L1 + L2 behaviour-check results (same length as ``rounds``).
    L2 returns ``[]`` for rounds where L2 didn't fire — absent fire ≠ conformance failure."""
    l1_out: list[list[CheckResult]] = []
    l2_out: list[list[CheckResult]] = []
    prior_audits: list[dict[str, Any]] = []
    # Stall depth entering each round, reconstructed from the persisted ``improved``
    # flags (the round file doesn't carry the live l1_stall_count). Same recurrence as
    # ``EscalationFSM.observe_round``: reset to 0 on improvement, else +1. Read BEFORE
    # the update so each round's exploration_budget matches what its L1 generation saw.
    stall = 0
    for i, round_data in enumerate(rounds):
        round_num = round_data.round
        budget = exploration_budget(stall, l1_patience).value if round_num >= 1 else None
        audit = audits[i] if i < len(audits) else None
        if round_num >= 1:
            stall = 0 if round_data.improved else stall + 1
        if audit is None:
            l1_out.append([])
            l2_out.append([])
            continue
        payload = round_data.optimizer_state.payload_as(PotterRoundState)
        ctx = ValidatorContext(
            round_num=round_num,
            prior_rounds=list(prior_audits),
            l1_layout=payload.memory.l1_layout,
            context_object=context_object,
            exploration_budget=budget,
            peaked_axes=frozenset(payload.axis_memory_peaked),
        )
        l1_out.append(run_all_checks(audit, ctx))
        l2_out.append(run_all_l2_checks(audit, ctx))
        prior_audits.append(audit)
    return l1_out, l2_out


def _compute_round_1_verdict(
    rounds: list[RoundResult],
    *,
    round_1_behavior: list[CheckResult],
) -> RoundOneVerdict:
    """Conformance-only round-1 verdict — yield and lift are dataset-headroom-confounded. Zero failures out of
    ZERO checks is not ``healthy``: L1 emitting no variants at all is the worst outcome, not the cleanest."""
    if not rounds:
        return "unknown"

    parse_failure = rounds[0].optimizer_state.payload_as(PotterRoundState).l1_parse_failure
    if parse_failure in PARSE_FAILURE_CHARGED:
        return "broken"
    # The remaining reason is TOOLING — an empty or truncated provider response. This verdict is
    # a CHARGE against the optimizer prompt and the L4 outer loop scores it, so grading provider
    # flakiness `broken` would bank a prompt as bad for a round that never reached one.
    if parse_failure is not None:
        return "unknown"
    if not round_1_behavior:
        return "unknown"

    failed_total = sum(1 for c in round_1_behavior if not c.passed)
    if failed_total >= 2:
        return "broken"
    if failed_total == 0:
        return "healthy"
    return "degraded"


# --- aggregation helpers ---------------------------------------------------


def _mean_yield_rate(rounds: list[RoundResult]) -> float | None:
    """Mean of per-round l1_yield. ``None`` when the cycle ran no round — a cycle that generated nothing had no yield to
    fall short of."""
    if not rounds:
        return None
    yields = [r.optimizer_state.payload_as(PotterRoundState).l1_yield for r in rounds]
    return sum(yields) / len(yields)


def _top_lifts(rounds: list[RoundResult], origin_composite_fitness: float | None) -> list[float]:
    """Per-round (best variant composite_fitness − parent composite_fitness). Round 0's parent
    is the origin composite_fitness; subsequent rounds inherit the prior round's.

    An origin that was never scored has NO bar, so the first round contributes no lift at all.
    Measured against a stand-in 0.0 it reports its whole composite as improvement, and that
    fabricated number then reaches both the mean and the stagnation streak."""
    lifts: list[float] = []
    parent = origin_composite_fitness
    for r in rounds:
        if parent is not None:
            lifts.append(r.composite_fitness - parent)
        parent = r.composite_fitness
    return lifts


def _max_stagnation_streak(top_lifts: list[float]) -> int:
    longest = current = 0
    for lift in top_lifts:
        if lift <= 0.0:
            current += 1
            longest = max(longest, current)
        else:
            current = 0
    return longest


def _behavior_pass_rate(behavior_results: list[list[CheckResult]]) -> float | None:
    """``None`` when no check ran — a conformance rate over zero checks is not a pass."""
    total = sum(len(r) for r in behavior_results)
    if not total:
        return None
    passed = sum(1 for r in behavior_results for c in r if c.passed)
    return passed / total
