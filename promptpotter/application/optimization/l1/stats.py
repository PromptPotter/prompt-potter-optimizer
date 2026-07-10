"""L1Stats — per-cycle L1 fitness statistics. Pure aggregation over round_data + behaviour checks.

Headline `rounds_to_95` (first round ≥ 0.95). `round_1_verdict` is the gate the
`potter-l1-meta-campaign` skill reads after the round-1 halt.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from promptpotter.application.optimization.validators.l1_behavior import CheckResult

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
    rounds: list[dict[str, Any]],
    *,
    origin_composite_fitness: float,
    behavior_results: list[list[CheckResult]],
    l2_behavior_results: list[list[CheckResult]] | None = None,
) -> L1Stats:
    """Aggregate round_data + behaviour checks → L1Stats."""
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
    rounds: list[dict[str, Any]],
    *,
    round_1_behavior: list[CheckResult],
) -> str:
    """Conformance-only round-1 verdict (yield/lift/regression are dataset-headroom-confounded).

    - `healthy` — zero conformance ✗.
    - `degraded` — exactly one ✗.
    - `broken` — ≥ 2 ✗.
    - `unknown` — no round 1 yet.
    """
    if not rounds:
        return "unknown"

    failed_total = sum(1 for c in round_1_behavior if not c.passed)
    if failed_total >= 2:
        return "broken"
    if failed_total == 0:
        return "healthy"
    return "degraded"


# --- aggregation helpers ---------------------------------------------------


def _measured(round_data: dict[str, Any], key: str) -> float | None:
    """A round's *measured* value at *key*, or ``None``. A round that carries no number for a
    metric did not score zero on it — the registry omits an evaluator's key when it had nothing
    to measure. Folding the absence in as 0.0 is what deflates every mean below."""
    value = round_data.get(key)
    return None if value is None else float(value)


def _first_round_at_threshold(rounds: list[dict[str, Any]], threshold: float) -> int | None:
    for r in rounds:
        accuracy = _measured(r, "accuracy")
        if accuracy is not None and accuracy >= threshold:
            round_num = r.get("round")
            return int(round_num) if isinstance(round_num, (int, float)) else None
    return None


def _mean_yield_rate(rounds: list[dict[str, Any]]) -> float | None:
    """Mean of per-round l1_yield (variants beating parent / variants generated).
    ``None`` when no round measured one — a cycle that generated nothing had no yield to fall
    short of, and a round that never recorded a yield must not drag the mean toward zero."""
    yields = [y for r in rounds if (y := _measured(r, "l1_yield")) is not None]
    if not yields:
        return None
    return sum(yields) / len(yields)


def _top_lifts(rounds: list[dict[str, Any]], origin_composite_fitness: float) -> list[float]:
    """Per-round (best variant composite_fitness − parent composite_fitness). Round 0's parent
    is the origin composite_fitness; subsequent rounds inherit the prior round's.

    A round with no recorded composite_fitness contributes no lift and does not become the next
    round's parent. Reading it as 0.0 charged that round a lift of ``−parent`` and then re-based
    every later round on zero — one unscored round, and ``stagnation_max`` reports a stall streak
    that never happened."""
    lifts: list[float] = []
    parent = origin_composite_fitness
    for r in rounds:
        composite_fitness = _measured(r, "composite_fitness")
        if composite_fitness is None:
            continue
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


def _behavior_pass_rate(behavior_results: list[list[CheckResult]]) -> float | None:
    """``None`` when no check ran — a conformance rate over zero checks is not a pass."""
    total = sum(len(r) for r in behavior_results)
    if not total:
        return None
    passed = sum(1 for r in behavior_results for c in r if c.passed)
    return passed / total


def _round_source(round_dict: dict[str, Any]) -> str:
    """Pull the lineage source ('l1_generate' / 'l2_context' / ...) off a round_data."""
    osp = round_dict.get("opt_search_point") or {}
    lineage = osp.get("lineage") or {}
    return str(lineage.get("source") or "")
