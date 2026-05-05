"""L1Stats — per-cycle L1 fitness statistics for the M10 review surface.

Pure aggregation over a list of round_data dicts (loaded from
``campaigns/{cycle_id}/rounds/trial_NNNN.json``) plus the per-round
behaviour-check results from ``l1_behavior_checks.run_all_checks``.

Headline metric is ``rounds_to_95`` — first round where best accuracy
≥ 0.95. The diagnostics flank it: yield, lift, behaviour pass rate,
stagnation streak, L2 fire count. ``round_1_verdict`` is the gate signal
the ``potter-review`` skill keys off after the round-1 halt.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from promptpotter.application.l1_behavior_checks import CheckResult

__all__ = ["L1Stats", "compute_l1_stats", "compute_round_1_verdict"]


# Round-1 verdict thresholds (M10 spec § Track 5 rule table).
HEALTHY_YIELD_RATE = 0.20
HEADLINE_ACC = 0.95


@dataclass(frozen=True)
class L1Stats:
    rounds_to_95: int | None
    yield_rate: float
    top_lift_mean: float
    behavior_pass_rate: float
    stagnation_max: int
    l2_fires: int
    round_1_verdict: str  # "healthy" | "degraded" | "broken" | "unknown"


def compute_l1_stats(
    rounds: list[dict[str, Any]],
    *,
    baseline_composite_fitness: float,
    behavior_results: list[list[CheckResult]],
) -> L1Stats:
    """Aggregate per-round round_data dicts + behaviour check results into L1Stats.

    ``rounds`` is the list of round_data dicts in round order (round 0 first).
    ``behavior_results[i]`` is the list of CheckResults for ``rounds[i]``;
    empty when no checks ran for that round.
    """
    rounds_to_95 = _first_round_at_threshold(rounds, HEADLINE_ACC)
    yield_rate = _mean_yield_rate(rounds)
    top_lifts = _top_lifts(rounds, baseline_composite_fitness)
    top_lift_mean = sum(top_lifts) / len(top_lifts) if top_lifts else 0.0
    stagnation_max = _max_stagnation_streak(top_lifts)
    behavior_pass_rate = _behavior_pass_rate(behavior_results)
    l2_fires = sum(1 for r in rounds if _round_source(r) == "l2_context")
    round_1_verdict = compute_round_1_verdict(
        rounds,
        baseline_composite_fitness=baseline_composite_fitness,
        round_1_behavior=behavior_results[0] if behavior_results else [],
        round_1_top_lift=top_lifts[0] if top_lifts else 0.0,
        round_1_yield_rate=_round_yield_rate(rounds[0]) if rounds else 0.0,
    )
    return L1Stats(
        rounds_to_95=rounds_to_95,
        yield_rate=yield_rate,
        top_lift_mean=top_lift_mean,
        behavior_pass_rate=behavior_pass_rate,
        stagnation_max=stagnation_max,
        l2_fires=l2_fires,
        round_1_verdict=round_1_verdict,
    )


def compute_round_1_verdict(
    rounds: list[dict[str, Any]],
    *,
    baseline_composite_fitness: float,
    round_1_behavior: list[CheckResult],
    round_1_top_lift: float,
    round_1_yield_rate: float,
) -> str:
    """Spec § Track 5 rule table.

    - ``healthy`` — all behaviour checks ✓, yield ≥ HEALTHY_YIELD_RATE, lift > 0.
    - ``degraded`` — exactly one check ✗, OR yield < HEALTHY_YIELD_RATE, OR lift ≤ 0.
    - ``broken`` — ≥ 2 checks ✗, OR baseline regression at round 1.
    - ``unknown`` — no round 1 yet.
    """
    if not rounds:
        return "unknown"

    failed = sum(1 for c in round_1_behavior if not c.passed)
    round_1_composite_fitness = float(rounds[0].get("composite_fitness") or 0.0)
    baseline_regression = round_1_composite_fitness < baseline_composite_fitness

    if failed >= 2 or baseline_regression:
        return "broken"
    healthy = failed == 0 and round_1_yield_rate >= HEALTHY_YIELD_RATE and round_1_top_lift > 0.0
    if healthy:
        return "healthy"
    return "degraded"


# --- aggregation helpers ---------------------------------------------------


def _first_round_at_threshold(rounds: list[dict[str, Any]], threshold: float) -> int | None:
    for r in rounds:
        if float(r.get("accuracy") or 0.0) >= threshold:
            round_num = r.get("round")
            return int(round_num) if isinstance(round_num, (int, float)) else None
    return None


def _round_yield_rate(round_dict: dict[str, Any]) -> float:
    """Variants beating parent / variants generated — pulled off the round_data."""
    return float(round_dict.get("l1_yield") or 0.0)


def _mean_yield_rate(rounds: list[dict[str, Any]]) -> float:
    if not rounds:
        return 0.0
    return sum(_round_yield_rate(r) for r in rounds) / len(rounds)


def _top_lifts(rounds: list[dict[str, Any]], baseline_composite_fitness: float) -> list[float]:
    """Per-round (best variant composite_fitness − parent composite_fitness). Round 0's parent
    is the baseline composite_fitness; subsequent rounds inherit the prior round's."""
    lifts: list[float] = []
    parent = float(baseline_composite_fitness or 0.0)
    for r in rounds:
        composite_fitness = float(r.get("composite_fitness") or 0.0)
        lifts.append(composite_fitness - parent)
        parent = composite_fitness
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


def _behavior_pass_rate(behavior_results: list[list[CheckResult]]) -> float:
    total = sum(len(r) for r in behavior_results)
    if not total:
        return 1.0
    passed = sum(1 for r in behavior_results for c in r if c.passed)
    return passed / total


def _round_source(round_dict: dict[str, Any]) -> str:
    """Pull the lineage source ('l1_generate' / 'l2_context' / ...) off a round_data."""
    osp = round_dict.get("opt_search_point") or {}
    lineage = osp.get("lineage") or {}
    return str(lineage.get("source") or "")
