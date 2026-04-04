"""Cohort sensitivity analysis — Wave 3e.

Slices per-query scan results by failure mode cohort to answer:
"Which axes matter most for which failure types?"
"""

from __future__ import annotations

import logging
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)


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
                sensitivities.append(CohortSensitivity(
                    axis=axis,
                    cohort=cohort_name,
                    cohort_size=len(cohort_queries),
                    baseline_hit_rate=round(baseline_rate, 4),
                    best_hit_rate=round(best_rate, 4),
                    best_value=best_val,
                    delta=round(delta, 4),
                ))

    sensitivities.sort(key=lambda s: -abs(s.delta))

    return CohortAnalysisResult(
        cohort_sensitivities=sensitivities,
        cohorts={m: sorted(qs) for m, qs in cohorts.items()},
    )
