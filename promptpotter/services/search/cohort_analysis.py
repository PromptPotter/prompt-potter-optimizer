"""Search analysis utilities — stats, previews, and cohort sensitivity.

Requires ``scipy`` for statistical functions (``pip install -e ".[stats]"``).
"""

from __future__ import annotations

import logging
import math
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Any

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

    from scipy.stats import norm

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

    from scipy.stats import norm

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

    from scipy.stats import norm

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

    from scipy.stats import norm

    z_alpha = norm.ppf(1 - alpha / 2)
    z_beta = norm.ppf(power)
    n = math.ceil(((z_alpha + z_beta) / target_mde) ** 2 * 0.25)
    return max(n, 1)


@dataclass
class CohortSensitivity:
    """Sensitivity of one axis for one failure cohort."""

    axis: str
    cohort: str  # failure_mode name
    cohort_size: int
    baseline_hit_rate: float
    best_hit_rate: float
    best_value: str
    delta: float  # best - baseline


@dataclass
class CohortAnalysisResult:
    """Full cohort sensitivity analysis."""

    cohort_sensitivities: list[CohortSensitivity] = field(default_factory=list)
    cohorts: dict[str, list[str]] = field(default_factory=dict)  # {failure_mode: [queries]}


def cohort_sensitivity(
    scan_rows: list[dict],
    failure_clusters: list[Any],
) -> CohortAnalysisResult:
    """Compute per-cohort sensitivity from per-query scan results.

    Args:
        scan_rows: Rows from sensitivity_scan() — each must have ``per_query_hits``
            dict mapping query → hit bool.
        failure_clusters: FailureCluster objects with ``failure_mode`` and
            ``example_queries`` attributes.

    Returns:
        CohortAnalysisResult with per-axis, per-cohort sensitivity deltas.
    """
    # Build cohort membership: {failure_mode: set of queries}
    cohorts: dict[str, set[str]] = {}
    for cluster in failure_clusters:
        mode = cluster.failure_mode
        queries = set(cluster.example_queries) if cluster.example_queries else set()
        if queries:
            cohorts[mode] = queries

    if not cohorts or not scan_rows:
        return CohortAnalysisResult()

    # Group rows by axis
    axis_rows: dict[str, list[dict]] = defaultdict(list)
    for row in scan_rows:
        axis_rows[row.get("axis", "")].append(row)

    sensitivities: list[CohortSensitivity] = []

    for axis, rows in axis_rows.items():
        for cohort_name, cohort_queries in cohorts.items():
            # Compute per-value hit rate for this cohort
            baseline_rate = 0.0
            best_rate = 0.0
            best_val = ""

            for row in rows:
                pq = row.get("per_query_hits", {})
                cohort_hits = [pq[q] for q in cohort_queries if q in pq]
                if not cohort_hits:
                    continue
                rate = sum(cohort_hits) / len(cohort_hits)

                if row.get("delta", 0) == 0.0:  # baseline value
                    baseline_rate = rate

                if rate > best_rate:
                    best_rate = rate
                    best_val = row.get("value_preview", "")

            delta = best_rate - baseline_rate
            if abs(delta) > 0.01:  # skip negligible
                sensitivities.append(
                    CohortSensitivity(
                        axis=axis,
                        cohort=cohort_name,
                        cohort_size=len(cohort_queries),
                        baseline_hit_rate=round(baseline_rate, 4),
                        best_hit_rate=round(best_rate, 4),
                        best_value=best_val,
                        delta=round(delta, 4),
                    )
                )

    sensitivities.sort(key=lambda s: -abs(s.delta))

    return CohortAnalysisResult(
        cohort_sensitivities=sensitivities,
        cohorts={m: sorted(qs) for m, qs in cohorts.items()},
    )
