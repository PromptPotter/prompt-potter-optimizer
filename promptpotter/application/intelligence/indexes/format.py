"""Shared formatters for index digests — pure string projections."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from promptpotter.application.intelligence.indexes.axis import AxisImpact
    from promptpotter.application.intelligence.indexes.sample import FailureCluster, SampleRecord


def _value_preview(value: Any) -> str:
    s = str(value)
    return s[:80] if len(s) > 80 else s


def _fmt_axis_rankings(
    rankings: list[AxisImpact], peaked_axes: frozenset[str] | None = None
) -> str:
    """Render the top-axes line. When ``peaked_axes`` is provided, axes whose
    trend has converged on a measured peak are tagged inline so the LLM
    consuming the digest can't read the effect rank without also seeing
    the peakedness — the prior split (effect ranks here, ``value_trends``
    line elsewhere) let L1 latch onto "highest effect → mutate" while
    silently ignoring "peaked → don't mutate". Single annotated line
    removes the contradiction.
    """
    peaked = peaked_axes or frozenset()
    parts: list[str] = []
    for a in rankings:
        base = f"{a.axis} (effect={a.effect_size:.3f}, {a.classification}"
        if a.axis in peaked:
            base += ", PEAKED — do not mutate without sibling_yield>0 or exploration_budget=wide rebut"
        base += ")"
        parts.append(base)
    return "; ".join(parts)


def _fmt_clusters(clusters: list[FailureCluster], *, with_counts: bool) -> str:
    if with_counts:
        return "; ".join(
            f"{c.failure_mode} ({c.fraction:.0%}, {c.sample_count} samples)" for c in clusters
        )
    return "; ".join(f"{c.failure_mode} ({c.fraction:.0%})" for c in clusters)


def _fmt_bottleneck(bottleneck: dict[str, float] | None) -> str | None:
    if not bottleneck:
        return None
    return "; ".join(f"{step}: {frac:.0%}" for step, frac in bottleneck.items())


def _fmt_persistent_failures(persistent: list[SampleRecord], *, terse: bool = False) -> str:
    intractable = [q for q in persistent if q.hit_rate == 0]
    chronic = [q for q in persistent if q.hit_rate > 0]
    parts: list[str] = []
    if intractable:
        suffix = "(never hit)" if terse else "(never hit in any config)"
        parts.append(f"{len(intractable)} intractable {suffix}")
    if chronic:
        suffix = "failures" if terse else "(recently failing but hit_rate > 0)"
        parts.append(f"{len(chronic)} chronic {suffix}")
    return "; ".join(parts)
