"""L1Stats — per-cycle L1 statistics, pure aggregation over the round documents. Every number
here is ``review.md``'s: the header table, and ``round_1_verdict`` as its conformance line."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal

from promptpotter.application.optimizers.nodes import CheckResult, ReviewReading, ReviewStat
from promptpotter.application.optimizers.potter.escalation.state import l1_stall_depth
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
from promptpotter.domain.results import OptimizerFact, RoundResult

__all__ = ["L1Stats", "compute_l1_stats", "review_reading", "round_facts"]

# The four the verdict can take, typed rather than described: an arm nothing emits is a reading
# nobody can get, and one nothing checks is a typo that ships.
RoundOneVerdict = Literal["healthy", "degraded", "broken", "unknown"]


@dataclass(frozen=True)
class L1Stats:
    """``None`` on any rate means NOT MEASURED and renders as a dash. Never 0.0 (nothing yielded) or 1.0 (all passed): a
    cycle with no rounds did not fail to yield, and a rate over zero checks is not a clean bill of health."""

    yield_rate: float | None
    top_reference_lift_mean: float | None
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
    behavior_results: list[list[CheckResult]],
    l2_behavior_results: list[list[CheckResult]] | None = None,
) -> L1Stats:
    yield_rate = _mean_yield_rate(rounds)
    top_lifts = _top_reference_lifts(rounds)
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
        top_reference_lift_mean=sum(top_lifts) / len(top_lifts) if top_lifts else None,
        behavior_pass_rate=behavior_pass_rate,
        # The stall the escalation ladder paces on, at its deepest: one rule, read per prefix.
        stagnation_max=max(
            (l1_stall_depth(rounds[: i + 1]) for i in range(len(rounds))), default=0
        ),
        l2_fires=l2_fires,
        l2_behavior_pass_rate=l2_behavior_pass_rate,
        round_1_verdict=round_1_verdict,
    )


def review_reading(
    rounds: list[RoundResult],
    audits: list[dict[str, Any] | None],
    *,
    context_object: list[str],
) -> ReviewReading:
    audits = [*audits, *[None] * (len(rounds) - len(audits))]
    l1_checks, l2_checks = _behavior_per_round(rounds, audits, context_object)
    stats = compute_l1_stats(rounds, behavior_results=l1_checks, l2_behavior_results=l2_checks)
    return ReviewReading(
        checks=l1_checks,
        check_ids=tuple(CHECK_REGISTRY),
        verdict=ReviewStat("round-1 conformance", stats.round_1_verdict),
        stats=(
            ReviewStat("yield_rate", stats.yield_rate, ".2f"),
            ReviewStat("top_reference_lift_mean", stats.top_reference_lift_mean, "+.4f"),
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
    """Potter's words about a round: L1's yield where a proposal was rejected — a full yield is
    no news — and the critique the round hands its next generation."""
    state = round_result.optimizer_state.payload_as(PotterRoundState)
    facts: list[OptimizerFact] = []
    if state.l1_rejected:
        live = state.l1_proposed - sum(state.l1_rejected.values())
        bits = ", ".join(
            f"{n} {_COLLAPSE_WORDS.get(reason, reason.replace('_', ' '))}"
            for reason, n in state.l1_rejected.items()
        )
        text = f"{live}/{state.l1_proposed} ({bits})"
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
) -> tuple[list[list[CheckResult]], list[list[CheckResult]]]:
    """Per-round L1 + L2 behaviour-check results (same length as ``rounds``).
    L2 returns ``[]`` for rounds where L2 didn't fire — absent fire ≠ conformance failure."""
    l1_out: list[list[CheckResult]] = []
    l2_out: list[list[CheckResult]] = []
    prior_audits: list[dict[str, Any]] = []
    for round_data, audit in zip(rounds, audits, strict=True):
        if audit is None:
            l1_out.append([])
            l2_out.append([])
            continue
        # What the round's own call offered, as the round banked it — never derived again here.
        payload = round_data.optimizer_state.payload_as(PotterRoundState)
        ctx = ValidatorContext(
            prior_rounds=list(prior_audits),
            citable=None if payload.l1_citable is None else tuple(payload.l1_citable),
            context_object=context_object,
            exploration_budget=payload.l1_exploration_budget,
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
    # a CHARGE against the optimizer prompt, and a round that never reached one earns none.
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


def _top_reference_lifts(rounds: list[RoundResult]) -> list[float]:
    """Each round's best arm against its parent, on the cells both read — the paired lift the
    round already banked. A round whose arms carry none (the origin, an unread panel) adds none."""
    tops: list[float] = []
    for r in rounds:
        lifts = [cs.reference_lift for cs in r.candidate_scores if cs.reference_lift is not None]
        if lifts:
            tops.append(max(lifts))
    return tops


def _behavior_pass_rate(behavior_results: list[list[CheckResult]]) -> float | None:
    """``None`` when no check ran — a conformance rate over zero checks is not a pass."""
    total = sum(len(r) for r in behavior_results)
    if not total:
        return None
    passed = sum(1 for r in behavior_results for c in r if c.passed)
    return passed / total
