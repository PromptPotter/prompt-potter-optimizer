"""L1Stats — per-cycle L1 fitness statistics. Pure aggregation over round_data + behaviour checks.

Headline `rounds_to_95` (first round ≥ 0.95). `round_1_verdict` is the gate the
`potter-l1-meta-campaign` skill reads after the round-1 halt.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from promptpotter.application.optimization.validators.l1_behavior import (
    CheckResult,
    extract_l1_variants,
)
from promptpotter.domain.search_point import PARAM_FORBIDDEN_KEYS

__all__ = ["L1Stats", "compute_l1_stats", "compute_round_1_verdict"]


# Headline-accuracy threshold for ``rounds_to_95``.
HEADLINE_ACC = 0.95


@dataclass(frozen=True)
class L1Stats:
    rounds_to_95: int | None
    yield_rate: float
    top_lift_mean: float
    behavior_pass_rate: float
    stagnation_max: int
    l2_fires: int
    # `l2_context` meta-prompt conformance — vacuous 1.0 when `l2_fires == 0` (read both).
    l2_behavior_pass_rate: float
    round_1_verdict: str  # "healthy" | "degraded" | "broken" | "unknown"
    # Wound-1 heal trail. Validator rejects model/provider mutations pre-population; counting
    # attempts distinguishes a healed cycle from a persistent violation without OSP re-parse.
    forbidden_axis_attempts: int
    forbidden_axis_healed: bool


def compute_l1_stats(
    rounds: list[dict[str, Any]],
    *,
    origin_composite_fitness: float,
    behavior_results: list[list[CheckResult]],
    l2_behavior_results: list[list[CheckResult]] | None = None,
    audits: list[dict[str, Any] | None] | None = None,
) -> L1Stats:
    """Aggregate round_data + behaviour checks → L1Stats. *audits[i]* is read to count
    forbidden-axis attempts (validator rejects them, but the attempted intent proves the heal chain ran).
    """
    rounds_to_95 = _first_round_at_threshold(rounds, HEADLINE_ACC)
    yield_rate = _mean_yield_rate(rounds)
    top_lifts = _top_lifts(rounds, origin_composite_fitness)
    top_lift_mean = sum(top_lifts) / len(top_lifts) if top_lifts else 0.0
    stagnation_max = _max_stagnation_streak(top_lifts)
    behavior_pass_rate = _behavior_pass_rate(behavior_results)
    l2_behavior_pass_rate = _behavior_pass_rate(l2_behavior_results or [])
    l2_fires = sum(1 for r in rounds if _round_source(r) == "l2_context")
    per_round_forbidden = _forbidden_axis_attempts_per_round(audits or [])
    forbidden_axis_attempts = sum(per_round_forbidden)
    forbidden_axis_healed = _forbidden_axis_healed(per_round_forbidden)
    round_1_verdict = compute_round_1_verdict(
        rounds,
        round_1_behavior=behavior_results[0] if behavior_results else [],
        forbidden_axis_healed=forbidden_axis_healed,
        forbidden_axis_attempts=forbidden_axis_attempts,
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
        forbidden_axis_attempts=forbidden_axis_attempts,
        forbidden_axis_healed=forbidden_axis_healed,
    )


def compute_round_1_verdict(
    rounds: list[dict[str, Any]],
    *,
    round_1_behavior: list[CheckResult],
    forbidden_axis_healed: bool = True,
    forbidden_axis_attempts: int = 0,
) -> str:
    """Conformance-only round-1 verdict (yield/lift/regression are dataset-headroom-confounded).

    - `healthy` — zero conformance ✗ (a healed forbidden_axes_honored ✗ doesn't count).
    - `degraded` — exactly one ✗ (not absorbed by heal).
    - `broken` — ≥ 2 ✗ (after discounting a healed forbidden-axes ✗) OR persistent forbidden-axes
      violation (attempts > 0 AND heal didn't converge).
    - `unknown` — no round 1 yet.
    """
    if not rounds:
        return "unknown"

    failed_total = sum(1 for c in round_1_behavior if not c.passed)
    forbidden_failed_r1 = any(
        not c.passed and c.check_id == "forbidden_axes_honored" for c in round_1_behavior
    )
    # Healed forbidden-axes ✗ doesn't count — validator caught it, no spend wasted.
    failed_for_verdict = failed_total
    if forbidden_failed_r1 and forbidden_axis_healed:
        failed_for_verdict -= 1

    persistent_forbidden = forbidden_axis_attempts > 0 and not forbidden_axis_healed

    if failed_for_verdict >= 2 or persistent_forbidden:
        return "broken"
    if failed_for_verdict == 0:
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


def _top_lifts(rounds: list[dict[str, Any]], origin_composite_fitness: float) -> list[float]:
    """Per-round (best variant composite_fitness − parent composite_fitness). Round 0's parent
    is the origin composite_fitness; subsequent rounds inherit the prior round's."""
    lifts: list[float] = []
    parent = float(origin_composite_fitness or 0.0)
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


def _forbidden_axis_attempts_per_round(audits: list[dict[str, Any] | None]) -> list[int]:
    """Count L1 variants proposing a `PARAM_FORBIDDEN_KEYS` override per round.
    Validator rejects them pre-population but the attempt rides the audit — that's the
    heal-chain-exercised signal.
    """
    counts: list[int] = []
    for audit in audits:
        variants = extract_l1_variants(audit)
        count = 0
        for v in variants:
            if _has_forbidden_keys(v.get("pipeline_params_override") or {}):
                count += 1
        counts.append(count)
    return counts


def _has_forbidden_keys(override: Any) -> bool:
    """Recursively check whether ``override`` mentions a forbidden axis."""
    if not isinstance(override, dict):
        return False
    for k, v in override.items():
        if k in PARAM_FORBIDDEN_KEYS:
            return True
        if isinstance(v, dict) and _has_forbidden_keys(v):
            return True
    return False


def _forbidden_axis_healed(per_round_attempts: list[int]) -> bool:
    """Heal verdict — last round with attempts must have a zero-attempt successor (1-round look-ahead).
    No attempts anywhere ⇒ vacuously True. Attempts in the final round ⇒ not healed.
    """
    last_with_attempts = -1
    for i, n in enumerate(per_round_attempts):
        if n > 0:
            last_with_attempts = i
    if last_with_attempts == -1:
        return True
    next_idx = last_with_attempts + 1
    if next_idx >= len(per_round_attempts):
        return False
    return per_round_attempts[next_idx] == 0
