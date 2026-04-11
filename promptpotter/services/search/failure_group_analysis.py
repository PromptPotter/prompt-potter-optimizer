"""Search analysis utilities — stats, previews, failure groups, and sequential elimination.

Requires ``scipy`` for statistical functions (``pip install -e ".[stats]"``).
"""

from __future__ import annotations

import logging
import math
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Any

from promptpotter.models.analysis import EscalationSignal, EscalationTarget

logger = logging.getLogger(__name__)


def preview(value: Any, max_len: int = 40) -> str:
    """Truncated preview of a variant value."""
    if isinstance(value, dict) and "properties" in value:
        n = len(value["properties"])
        return f"schema({n} fields)"
    s = str(value)
    if not s:
        return "(empty)"
    return s[:max_len] + ("..." if len(s) > max_len else "")


def wilson_ci(hits: int, total: int, alpha: float = 0.05) -> tuple[float, float]:
    """Wilson score confidence interval for a binomial proportion.

    Returns (lower, upper) as fractions in [0, 1].
    """
    if total == 0:
        return (0.0, 0.0)

    from scipy.stats import norm  # type: ignore[import-untyped]

    z = norm.ppf(1 - alpha / 2)
    p_hat = hits / total
    denom = 1 + z**2 / total
    center = (p_hat + z**2 / (2 * total)) / denom
    margin = z * math.sqrt(p_hat * (1 - p_hat) / total + z**2 / (4 * total**2)) / denom
    return (max(0.0, center - margin), min(1.0, center + margin))


def proportion_test(
    hits_a: int,
    total_a: int,
    hits_b: int,
    total_b: int,
) -> float:
    """Two-proportion z-test p-value (two-sided).

    Returns p-value, or 1.0 when the test is degenerate.
    """
    if total_a == 0 or total_b == 0:
        return 1.0

    from scipy.stats import norm  # type: ignore[import-untyped]

    p_pool = (hits_a + hits_b) / (total_a + total_b)
    se = math.sqrt(p_pool * (1 - p_pool) * (1 / total_a + 1 / total_b))
    if se < 1e-12:
        return 1.0

    z = (hits_a / total_a - hits_b / total_b) / se
    return float(2 * norm.sf(abs(z)))


def min_detectable_effect(n: int, alpha: float = 0.05, power: float = 0.8) -> float:
    """Minimum detectable effect size for a given sample size.

    Uses worst-case variance (p=0.5). Returns MDE as a fraction.
    """
    if n <= 0:
        return 1.0

    from scipy.stats import norm  # type: ignore[import-untyped]

    z_alpha = norm.ppf(1 - alpha / 2)
    z_beta = norm.ppf(power)
    mde = (z_alpha + z_beta) * math.sqrt(0.25 / n)
    return min(mde, 1.0)


def min_sample_size(target_mde: float, alpha: float = 0.05, power: float = 0.8) -> int:
    """Minimum sample size to detect a given effect size.

    Algebraic inverse of ``min_detectable_effect()``:
    ``n = ceil(((z_alpha + z_beta) / mde)^2 * 0.25)``

    Uses worst-case variance (p=0.5).
    """
    if target_mde <= 0:
        return 1

    from scipy.stats import norm  # type: ignore[import-untyped]

    z_alpha = norm.ppf(1 - alpha / 2)
    z_beta = norm.ppf(power)
    n = math.ceil(((z_alpha + z_beta) / target_mde) ** 2 * 0.25)
    return max(n, 1)


# ---------------------------------------------------------------------------
# Failure group sensitivity — cross-tabulate scan results with failure groups
# ---------------------------------------------------------------------------


@dataclass
class FailureGroupSensitivity:
    """Sensitivity of one axis for one failure group."""

    axis: str
    failure_group: str  # failure_mode name (e.g., "web_search")
    group_size: int
    baseline_hit_rate: float
    best_hit_rate: float
    best_value: str
    delta: float  # best - baseline


@dataclass
class FailureGroupResult:
    """Full failure group sensitivity analysis."""

    sensitivities: list[FailureGroupSensitivity] = field(default_factory=list)
    groups: dict[str, list[str]] = field(default_factory=dict)  # {failure_mode: [queries]}


def failure_group_sensitivity(
    scan_rows: list[dict],
    failure_clusters: list[Any],
) -> FailureGroupResult:
    """Cross-tabulate scan per-query results with failure groups.

    For each (axis, failure_group) pair, computes the delta between baseline
    and best-value hit rates within that group. This reveals which axes
    specifically help which failure modes.

    Args:
        scan_rows: Rows from sensitivity_scan() — each must have ``per_query_hits``
            dict mapping query → hit bool.
        failure_clusters: FailureCluster objects with ``failure_mode`` and
            ``example_queries`` attributes.

    Returns:
        FailureGroupResult with per-axis, per-group sensitivity deltas.
    """
    # Build group membership: {failure_mode: set of queries}
    groups: dict[str, set[str]] = {}
    for cluster in failure_clusters:
        mode = cluster.failure_mode
        queries = set(cluster.example_queries) if cluster.example_queries else set()
        if queries:
            groups[mode] = queries

    if not groups or not scan_rows:
        return FailureGroupResult()

    # Group rows by axis
    axis_rows: dict[str, list[dict]] = defaultdict(list)
    for row in scan_rows:
        axis_rows[row.get("axis", "")].append(row)

    sensitivities: list[FailureGroupSensitivity] = []

    for axis, rows in axis_rows.items():
        for group_name, group_queries in groups.items():
            baseline_rate = 0.0
            best_rate = 0.0
            best_val = ""

            for row in rows:
                pq = row.get("per_query_hits", {})
                group_hits = [pq[q] for q in group_queries if q in pq]
                if not group_hits:
                    continue
                rate = sum(group_hits) / len(group_hits)

                if row.get("delta", 0) == 0.0:  # baseline value
                    baseline_rate = rate

                if rate > best_rate:
                    best_rate = rate
                    best_val = row.get("value_preview", "")

            delta = best_rate - baseline_rate
            if abs(delta) > 0.01:  # skip negligible
                sensitivities.append(
                    FailureGroupSensitivity(
                        axis=axis,
                        failure_group=group_name,
                        group_size=len(group_queries),
                        baseline_hit_rate=round(baseline_rate, 4),
                        best_hit_rate=round(best_rate, 4),
                        best_value=best_val,
                        delta=round(delta, 4),
                    )
                )

    sensitivities.sort(key=lambda s: -abs(s.delta))

    return FailureGroupResult(
        sensitivities=sensitivities,
        groups={m: sorted(qs) for m, qs in groups.items()},
    )


# ---------------------------------------------------------------------------
# Sequential candidate elimination via paired Welch's t-test
# ---------------------------------------------------------------------------


def _paired_ttest_pvalue(current: list[float], prior: list[float]) -> float:
    """One-sided paired t-test p-value (H_a: prior is better than current).

    Uses ``scipy.stats.ttest_rel`` on the shared prefix, then converts
    the two-sided p-value to one-sided.  Returns 1.0 when variance of
    differences is zero (identical score vectors).
    """
    from scipy.stats import ttest_rel  # type: ignore[import-untyped]

    n = min(len(current), len(prior))
    if n < 2:
        return 1.0

    stat, p_two = ttest_rel(prior[:n], current[:n])

    if math.isnan(p_two):
        return 1.0

    # One-sided: H_a is "prior mean > current mean" → reject when t > 0
    if stat > 0:
        return p_two / 2
    return 1 - p_two / 2


def _holm_bonferroni(p_values: list[float], alpha: float) -> list[bool]:
    """Holm-Bonferroni step-down correction.

    Returns a list of booleans (same order as *p_values*) indicating
    which null hypotheses are rejected at family-wise *alpha*.
    """
    m = len(p_values)
    if m == 0:
        return []

    indexed = sorted(enumerate(p_values), key=lambda x: x[1])
    rejected = [False] * m

    for rank, (orig_idx, p) in enumerate(indexed):
        threshold = alpha / (m - rank)
        if p < threshold:
            rejected[orig_idx] = True
        else:
            break  # step-down: stop at first non-rejection

    return rejected


def should_stop_early(
    current_scores: list[float],
    prior_populations: list[list[float]],
    alpha: float = 0.05,
) -> tuple[bool, dict[str, Any]]:
    """Decide whether to eliminate the current candidate.

    Runs a one-sided paired t-test against each prior population on the
    shared query prefix, then applies Holm-Bonferroni correction.

    Returns:
        ``(stop, context)`` where *context* carries p-values and the
        index of the prior that triggered rejection (if any).
    """
    if not prior_populations:
        return False, {}

    p_values = [_paired_ttest_pvalue(current_scores, prior) for prior in prior_populations]
    rejections = _holm_bonferroni(p_values, alpha)

    ctx: dict[str, Any] = {"p_values": p_values, "rejections": rejections}

    if any(rejections):
        trigger_idx = rejections.index(True)
        ctx["triggered_by_prior"] = trigger_idx
        ctx["triggered_p"] = p_values[trigger_idx]
        return True, ctx

    return False, ctx


class EliminationCheck:
    """Stateful check that eliminates inferior candidates mid-evaluation.

    Conforms to the ``DegradationCheck`` protocol (``.enabled``, ``.name``,
    ``.evaluate(results_so_far, candidate_idx, n_total_candidates)``), so
    it plugs directly into ``_run_degradation_checks()`` in
    ``dataset_scoring.py``.
    """

    name: str = "elimination"

    def __init__(self, *, n_min: int = 20, alpha: float = 0.05, n_queries: int) -> None:
        self.n_min = n_min
        self.alpha = alpha
        self.n_queries = n_queries
        self.enabled = n_queries >= 2 * n_min
        self._prior_scores: list[list[float]] = []

    def register_completed(self, scores: list[float]) -> None:
        """Register a fully-evaluated candidate's per-query scores as a prior."""
        self._prior_scores.append(scores)

    def evaluate(
        self,
        results_so_far: list[dict],
        candidate_idx: int,
        n_total_candidates: int,
    ) -> EscalationSignal | None:
        """Check whether the current candidate should be eliminated.

        Returns ``None`` to continue evaluation, or an ``EscalationSignal``
        to stop the candidate early.
        """
        if not self._prior_scores:
            return None

        n = len(results_so_far)
        if n < self.n_min:
            return None

        current_scores = [r["score"] for r in results_so_far]
        stop, ctx = should_stop_early(current_scores, self._prior_scores, self.alpha)

        if not stop:
            return None

        logger.info(
            "Elimination: candidate %d stopped at query %d/%d (p=%.4f vs prior %d)",
            candidate_idx,
            n,
            self.n_queries,
            ctx.get("triggered_p", 0.0),
            ctx.get("triggered_by_prior", -1),
        )
        return EscalationSignal(
            check_name=self.name,
            target=EscalationTarget.ABORT,
            check_result={
                "queries_evaluated": n,
                "total_queries": self.n_queries,
                "n_priors": len(self._prior_scores),
                **ctx,
            },
            candidate_idx=candidate_idx,
            candidates_scored=candidate_idx + 1,
            candidates_skipped=n_total_candidates - candidate_idx - 1,
        )
