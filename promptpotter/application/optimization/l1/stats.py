"""L1Stats — per-cycle L1 fitness statistics. Pure aggregation over round_data + behaviour checks.

Headline `rounds_to_95` (first round ≥ 0.95). `round_1_verdict` is the gate the
`potter-l1-meta-campaign` skill reads after the round-1 halt.
"""

from __future__ import annotations

from dataclasses import dataclass

from promptpotter.application.optimization.validators.l1_behavior import CheckResult
from promptpotter.domain.results import RoundResult

__all__ = ["L1Stats", "compute_l1_stats"]


# Headline-accuracy threshold for ``rounds_to_95``.
HEADLINE_ACC = 0.95


@dataclass(frozen=True)
class L1Stats:
    """``None`` on any rate means *not measured*, and renders as ``—``. It is never a 0.0
    (nothing yielded) or a 1.0 (everything passed): a cycle with no rounds did not fail to
    yield, and a conformance rate over zero checks is not a clean bill of health."""

    rounds_to_95: int | None
    yield_rate: float | None
    top_lift_mean: float | None
    behavior_pass_rate: float | None
    stagnation_max: int
    l2_fires: int
    # `l2_context` meta-prompt conformance — None when L2 never fired, so the reader
    # doesn't have to cross-check `l2_fires` to know a 1.0 was vacuous.
    l2_behavior_pass_rate: float | None
    round_1_verdict: str  # "healthy" | "degraded" | "broken" | "unknown"


def compute_l1_stats(
    rounds: list[RoundResult],
    *,
    origin_composite_fitness: float,
    behavior_results: list[list[CheckResult]],
    l2_behavior_results: list[list[CheckResult]] | None = None,
) -> L1Stats:
    """Aggregate rounds + behaviour checks → L1Stats."""
    rounds_to_95 = _first_round_at_threshold(rounds, HEADLINE_ACC)
    yield_rate = _mean_yield_rate(rounds)
    top_lifts = _top_lifts(rounds, origin_composite_fitness)
    top_lift_mean = sum(top_lifts) / len(top_lifts) if top_lifts else None
    stagnation_max = _max_stagnation_streak(top_lifts)
    behavior_pass_rate = _behavior_pass_rate(behavior_results)
    l2_behavior_pass_rate = _behavior_pass_rate(l2_behavior_results or [])
    l2_fires = sum(1 for r in rounds if _round_source(r) == "l2_context")
    round_1_verdict = _compute_round_1_verdict(
        rounds,
        round_1_behavior=behavior_results[0] if behavior_results else [],
    )
    return L1Stats(
        rounds_to_95=rounds_to_95,
        yield_rate=yield_rate,
        top_lift_mean=top_lift_mean,
        behavior_pass_rate=behavior_pass_rate,
        stagnation_max=stagnation_max,
        l2_fires=l2_fires,
        l2_behavior_pass_rate=l2_behavior_pass_rate,
        round_1_verdict=round_1_verdict,
    )


def _compute_round_1_verdict(
    rounds: list[RoundResult],
    *,
    round_1_behavior: list[CheckResult],
) -> str:
    """Conformance-only round-1 verdict (yield/lift/regression are dataset-headroom-confounded).

    - `healthy` — the checks RAN and zero failed.
    - `degraded` — exactly one ✗.
    - `broken` — ≥ 2 ✗, **or L1 emitted no variants at all**.
    - `unknown` — no round 1 yet, or its checks never ran.

    The two guards below are not defensive padding — each closes a path that used to reach
    the ``failed_total == 0`` branch and report ``healthy``:

    1. **L1 yielded nothing.** Zero variants is the WORST L1 outcome, not the cleanest — the
       meta-prompt made its own children unreadable. It scored ``healthy`` because each check
       short-circuits to ``passed=True`` on an empty variant list. ``run_all_checks`` now
       returns ``[]`` there, which lands in guard 2, so this reads the round's own signal.
    2. **The checks never ran** (empty list — L1 yielded nothing, or the round's audit dict was
       missing entirely). Zero failures out of zero checks is not a clean bill of health; the
       same rule ``L1Stats`` states for every rate it carries.
    """
    if not rounds:
        return "unknown"

    if rounds[0].l1_parse_failure is not None:
        return "broken"
    if not round_1_behavior:
        return "unknown"

    failed_total = sum(1 for c in round_1_behavior if not c.passed)
    if failed_total >= 2:
        return "broken"
    if failed_total == 0:
        return "healthy"
    return "degraded"


# --- aggregation helpers ---------------------------------------------------


def _first_round_at_threshold(rounds: list[RoundResult], threshold: float) -> int | None:
    return next((r.round for r in rounds if r.accuracy >= threshold), None)


def _mean_yield_rate(rounds: list[RoundResult]) -> float | None:
    """Mean of per-round l1_yield (variants beating parent / variants generated).
    ``None`` when the cycle ran no round — a cycle that generated nothing had no yield to
    fall short of."""
    if not rounds:
        return None
    return sum(r.l1_yield for r in rounds) / len(rounds)


def _top_lifts(rounds: list[RoundResult], origin_composite_fitness: float) -> list[float]:
    """Per-round (best variant composite_fitness − parent composite_fitness). Round 0's parent
    is the origin composite_fitness; subsequent rounds inherit the prior round's."""
    lifts: list[float] = []
    parent = origin_composite_fitness
    for r in rounds:
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


def _round_source(rr: RoundResult) -> str:
    """Pull the lineage source ('l1_generate' / 'l2_context' / ...) off a round."""
    return rr.opt_search_point.lineage.source if rr.opt_search_point else ""
