"""DORMANT — do not maintain. See ./README.md.

Failure-group sensitivity analysis for the recon scan.
Cross-tabulates per-axis scan results against failure clusters from
SearchMemory to reveal which axes specifically help which failure modes.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from typing import Any


def preview(value: Any, max_len: int = 40) -> str:
    """Truncated preview of a variant value."""
    if isinstance(value, dict) and "properties" in value:
        n = len(value["properties"])
        return f"schema({n} fields)"
    s = str(value)
    if not s:
        return "(empty)"
    return s[:max_len] + ("..." if len(s) > max_len else "")


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
        scan_rows: Rows from run_recon() — each must have ``per_query_hits``
            dict mapping query → hit bool.
        failure_clusters: FailureCluster objects with ``failure_mode`` and
            ``example_queries`` attributes.

    Returns:
        FailureGroupResult with per-axis, per-group sensitivity deltas.
    """
    groups: dict[str, set[str]] = {}
    for cluster in failure_clusters:
        mode = cluster.failure_mode
        queries = set(cluster.example_queries) if cluster.example_queries else set()
        if queries:
            groups[mode] = queries

    if not groups or not scan_rows:
        return FailureGroupResult()

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
