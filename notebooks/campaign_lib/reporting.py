"""Supplemental materials generation for paper-ready benchmark reporting.

Formatting layer — takes structured dicts from ``campaign.export`` and
produces markdown tables and documents.  Display-only, no persistence.
"""

from __future__ import annotations

import json
import logging
from typing import TYPE_CHECKING, Any

from promptpotter.services.campaign.export import (
    build_reproducibility_manifest,
    compare_campaigns,
    export_failure_analysis,
    export_query_difficulty,
    export_search_memory_summary,
    export_trend_analysis,
    flatten_campaign_trials,
)

if TYPE_CHECKING:
    from promptpotter.models.analysis import FailureAnalysis, QueryDifficulty, TrendAnalysis
    from promptpotter.models.pipeline_schema import PipelineSchema
    from promptpotter.services.project_store import ProjectStore
    from promptpotter.services.search.search_memory import SearchMemory

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Table renderers
# ---------------------------------------------------------------------------


def _fmt_pct(value: float) -> str:
    return f"{value:.1%}"


def _fmt_ci(lower: float, upper: float) -> str:
    return f"[{lower:.1%}-{upper:.1%}]"


def _fmt_pvalue(p: float) -> str:
    if p < 0.001:
        return "p<0.001 ***"
    if p < 0.01:
        return f"p={p:.3f} **"
    if p < 0.05:
        return f"p={p:.2f} *"
    return f"p={p:.2f} (ns)"


def _md_table(headers: list[str], rows: list[list[str]]) -> str:
    """Render a markdown table with header alignment."""
    col_widths = [len(h) for h in headers]
    for row in rows:
        for i, cell in enumerate(row):
            if i < len(col_widths):
                col_widths[i] = max(col_widths[i], len(cell))

    def _pad(cells: list[str]) -> str:
        parts = [c.ljust(col_widths[i]) for i, c in enumerate(cells)]
        return "| " + " | ".join(parts) + " |"

    lines = [
        _pad(headers),
        "| " + " | ".join("-" * w for w in col_widths) + " |",
    ]
    for row in rows:
        lines.append(_pad(row))
    return "\n".join(lines)


def render_comparison_table(comparison: dict[str, Any]) -> str:
    """Render campaign comparison as a markdown table.

    Columns: Strategy | Baseline | Best | Delta | 95% CI | Rounds | Budget | Stop Reason
    """
    headers = ["Strategy", "Baseline", "Best", "Delta", "95% CI", "Rounds", "Budget", "Stop"]
    rows = []
    for s in comparison["summary_table"]:
        rows.append([
            s["name"],
            _fmt_pct(s["baseline"]),
            _fmt_pct(s["best"]),
            f"+{s['improvement']:.1%}" if s["improvement"] > 0 else _fmt_pct(s["improvement"]),
            _fmt_ci(s["ci_lower"], s["ci_upper"]),
            str(s["rounds_to_best"]),
            str(s["eval_budget"]),
            s.get("stop_reason", ""),
        ])
    return _md_table(headers, rows)


def render_significance_table(comparison: dict[str, Any]) -> str:
    """Render pairwise significance tests between campaigns."""
    pairs = comparison.get("pairwise_significance", [])
    if not pairs:
        return ""

    headers = ["Campaign A", "Campaign B", "p-value", "Significance"]
    rows = [
        [p["campaign_a"], p["campaign_b"], f"{p['p_value']:.4f}", _fmt_pvalue(p["p_value"])]
        for p in pairs
    ]
    return _md_table(headers, rows)


def render_convergence_table(comparison: dict[str, Any]) -> str:
    """Per-round accuracy for all campaigns, side by side."""
    convergence = comparison.get("convergence", {})
    if not convergence:
        return ""

    campaign_ids = list(convergence.keys())
    all_rounds: set[int] = set()
    for series in convergence.values():
        all_rounds.update(entry["round"] for entry in series)

    headers = ["Round", *campaign_ids]
    rows = []
    for r in sorted(all_rounds):
        row = [str(r)]
        for cid in campaign_ids:
            series = convergence[cid]
            match = next((e for e in series if e["round"] == r), None)
            row.append(_fmt_pct(match["accuracy"]) if match else "-")
        rows.append(row)
    return _md_table(headers, rows)


def render_parameter_impact_table(memory_summary: dict[str, Any]) -> str:
    """Axis impact ranking table."""
    impacts = memory_summary.get("parameter_impact", [])
    if not impacts:
        return ""

    headers = ["Axis", "Effect Size", "Consistency", "Classification", "Best Value", "n"]
    rows = []
    for ai in impacts:
        top_val = ai["top_values"][0]["value"] if ai["top_values"] else "-"
        rows.append([
            ai["axis"],
            f"{ai['effect_size']:.3f}",
            _fmt_pct(ai["consistency"]),
            ai["classification"],
            top_val,
            str(ai["sample_count"]),
        ])
    return _md_table(headers, rows)


def render_failure_analysis_table(failures: list[dict[str, Any]]) -> str:
    """Failure pattern table."""
    if not failures:
        return ""

    headers = ["Pattern", "Count", "Fraction", "Example Queries"]
    rows = [
        [
            f["pattern"],
            str(f["query_count"]),
            _fmt_pct(f["fraction"]),
            "; ".join(f["example_queries"][:2]),
        ]
        for f in failures
    ]
    return _md_table(headers, rows)


def render_query_difficulty_summary(difficulty: dict[str, Any]) -> str:
    """Query classification summary table."""
    s = difficulty.get("summary", {})
    if not s:
        return ""

    headers = ["Class", "Count", "Fraction"]
    total = s.get("total", 1) or 1
    rows = [
        ["Easy (hit rate >= 80%)", str(s.get("n_easy", 0)),
         _fmt_pct(s["n_easy"] / total)],
        ["Discriminating (var >= 0.1)", str(s.get("n_discriminating", 0)),
         _fmt_pct(s["n_discriminating"] / total)],
        ["Hard (0 < hit rate < 20%)", str(s.get("n_hard", 0)),
         _fmt_pct(s["n_hard"] / total)],
        ["Dead (hit rate = 0%)", str(s.get("n_dead", 0)),
         _fmt_pct(s["n_dead"] / total)],
    ]
    return _md_table(headers, rows)


def render_trend_table(trends: list[dict[str, Any]]) -> str:
    """Round-over-round stability table."""
    if not trends:
        return ""

    headers = ["Transition", "Improved", "Regressed", "Unchanged"]
    rows = [
        [
            f"R{t['from_round']}->R{t['to_round']}",
            str(t["n_improved"]),
            str(t["n_regressed"]),
            str(t["n_unchanged"]),
        ]
        for t in trends
    ]
    return _md_table(headers, rows)


# ---------------------------------------------------------------------------
# Supplemental document assembly
# ---------------------------------------------------------------------------

_DEFAULT_SECTIONS = [
    "comparison",
    "convergence",
    "significance",
    "parameter_impact",
    "failure_analysis",
    "query_difficulty",
    "reproducibility",
]


def generate_supplemental(
    store: ProjectStore,
    backend_id: str,
    *,
    campaign_ids: list[str] | None = None,
    search_memory: SearchMemory | None = None,
    pipeline_schema: PipelineSchema | None = None,
    failure_analysis: FailureAnalysis | None = None,
    query_difficulty: QueryDifficulty | None = None,
    trend_analysis: TrendAnalysis | None = None,
    sections: list[str] | None = None,
) -> str:
    """Assemble full supplemental materials document.

    Loads campaigns from store, combines with optional pre-computed analysis,
    and renders each section as markdown.

    Args:
        store: ProjectStore for loading campaign data.
        backend_id: Backend identifier.
        campaign_ids: Specific campaigns to include (default: all).
        search_memory: Pre-loaded SearchMemory (skips memory sections if None).
        pipeline_schema: Pipeline schema for reproducibility manifest.
        failure_analysis: Pre-computed FailureAnalysis.
        query_difficulty: Pre-computed QueryDifficulty.
        trend_analysis: Pre-computed TrendAnalysis.
        sections: Which sections to include (default: all).

    Returns:
        Complete markdown string.
    """
    active_sections = sections or _DEFAULT_SECTIONS

    # Load campaigns
    if campaign_ids:
        campaigns = [
            c for cid in campaign_ids
            if (c := store.campaigns.load(backend_id, cid)) is not None
        ]
    else:
        summaries = store.campaigns.list_all(backend_id)
        campaigns = [
            c for s in summaries
            if (c := store.campaigns.load(backend_id, s["campaign_id"])) is not None
        ]

    if not campaigns:
        return "# Supplemental Materials\n\nNo campaigns found.\n"

    comparison = compare_campaigns(campaigns)

    parts = ["# Supplemental Materials\n"]

    if "comparison" in active_sections:
        parts.append("## Campaign Comparison\n")
        parts.append(render_comparison_table(comparison))
        parts.append("")

    if "convergence" in active_sections:
        table = render_convergence_table(comparison)
        if table:
            parts.append("## Convergence\n")
            parts.append(table)
            parts.append("")

    if "significance" in active_sections:
        table = render_significance_table(comparison)
        if table:
            parts.append("## Pairwise Significance\n")
            parts.append(table)
            parts.append("")

    if "parameter_impact" in active_sections and search_memory is not None:
        memory_summary = export_search_memory_summary(search_memory)
        parts.append("## Parameter Impact\n")
        parts.append(render_parameter_impact_table(memory_summary))
        parts.append("")

        qp = memory_summary["query_patterns"]
        parts.append(
            f"Query distribution: {qp['total_queries']} total "
            f"({qp['n_easy']} easy, {qp['n_discriminating']} discriminating, "
            f"{qp['n_hard']} hard, {qp['n_dead']} dead)\n"
        )

    if "failure_analysis" in active_sections and failure_analysis is not None:
        failures = export_failure_analysis(failure_analysis)
        if failures:
            parts.append("## Failure Analysis\n")
            parts.append(render_failure_analysis_table(failures))
            parts.append("")

    if "query_difficulty" in active_sections and query_difficulty is not None:
        diff = export_query_difficulty(query_difficulty)
        parts.append("## Query Difficulty\n")
        parts.append(render_query_difficulty_summary(diff))
        parts.append("")

    if "trends" in active_sections and trend_analysis is not None:
        trends = export_trend_analysis(trend_analysis)
        if trends:
            parts.append("## Round Stability\n")
            parts.append(render_trend_table(trends))
            parts.append("")

    if "reproducibility" in active_sections:
        manifest = build_reproducibility_manifest(campaigns, backend_id, pipeline_schema)
        parts.append("## Reproducibility\n")
        parts.append("```json")
        parts.append(json.dumps(manifest, indent=2, default=str))
        parts.append("```\n")

    return "\n".join(parts)


def generate_export_json(
    store: ProjectStore,
    backend_id: str,
    *,
    campaign_ids: list[str] | None = None,
    search_memory: SearchMemory | None = None,
    pipeline_schema: PipelineSchema | None = None,
) -> dict[str, Any]:
    """Export all campaign data as a single JSON-serializable dict.

    Suitable for inclusion in a paper repository or supplemental data package.
    """
    if campaign_ids:
        campaigns = [
            c for cid in campaign_ids
            if (c := store.campaigns.load(backend_id, cid)) is not None
        ]
    else:
        summaries = store.campaigns.list_all(backend_id)
        campaigns = [
            c for s in summaries
            if (c := store.campaigns.load(backend_id, s["campaign_id"])) is not None
        ]

    result: dict[str, Any] = {
        "comparison": compare_campaigns(campaigns) if campaigns else {},
        "campaigns": {
            c["campaign_id"]: {
                "metadata": {
                    k: v for k, v in c.items()
                    if k not in ("trials",)
                },
                "trials": flatten_campaign_trials(c),
            }
            for c in campaigns
        },
        "reproducibility": build_reproducibility_manifest(
            campaigns, backend_id, pipeline_schema,
        ),
    }

    if search_memory is not None:
        result["search_memory"] = export_search_memory_summary(search_memory)

    return result
