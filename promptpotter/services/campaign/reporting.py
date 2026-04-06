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


def _comparison_rows(data: dict[str, Any]) -> list[list[str]]:
    return [
        [
            s["name"],
            _fmt_pct(s["baseline"]),
            _fmt_pct(s["best"]),
            f"+{s['improvement']:.1%}" if s["improvement"] > 0 else _fmt_pct(s["improvement"]),
            _fmt_ci(s["ci_lower"], s["ci_upper"]),
            str(s["rounds_to_best"]),
            str(s["eval_budget"]),
            s.get("stop_reason", ""),
        ]
        for s in data["summary_table"]
    ]


def _significance_rows(data: dict[str, Any]) -> list[list[str]]:
    return [
        [p["campaign_a"], p["campaign_b"], f"{p['p_value']:.4f}", _fmt_pvalue(p["p_value"])]
        for p in data["pairwise_significance"]
    ]


def _convergence_headers(data: dict[str, Any]) -> list[str]:
    return ["Round", *data["convergence"].keys()]


def _convergence_rows(data: dict[str, Any]) -> list[list[str]]:
    convergence = data["convergence"]
    campaign_ids = list(convergence.keys())
    all_rounds: set[int] = set()
    for series in convergence.values():
        all_rounds.update(entry["round"] for entry in series)

    rows = []
    for r in sorted(all_rounds):
        row = [str(r)]
        for cid in campaign_ids:
            series = convergence[cid]
            match = next((e for e in series if e["round"] == r), None)
            row.append(_fmt_pct(match["accuracy"]) if match else "-")
        rows.append(row)
    return rows


def _parameter_impact_rows(data: dict[str, Any]) -> list[list[str]]:
    rows = []
    for ai in data["parameter_impact"]:
        top_val = ai["top_values"][0]["value"] if ai["top_values"] else "-"
        rows.append(
            [
                ai["axis"],
                f"{ai['effect_size']:.3f}",
                _fmt_pct(ai["consistency"]),
                ai["classification"],
                top_val,
                str(ai["sample_count"]),
            ]
        )
    return rows


def _failure_rows(data: list[dict[str, Any]]) -> list[list[str]]:
    return [
        [
            f["pattern"],
            str(f["query_count"]),
            _fmt_pct(f["fraction"]),
            "; ".join(f["example_queries"][:2]),
        ]
        for f in data
    ]


def _difficulty_rows(data: dict[str, Any]) -> list[list[str]]:
    s = data["summary"]
    total = s.get("total", 1) or 1
    return [
        ["Easy (hit rate >= 80%)", str(s.get("n_easy", 0)), _fmt_pct(s["n_easy"] / total)],
        [
            "Discriminating (var >= 0.1)",
            str(s.get("n_discriminating", 0)),
            _fmt_pct(s["n_discriminating"] / total),
        ],
        ["Hard (0 < hit rate < 20%)", str(s.get("n_hard", 0)), _fmt_pct(s["n_hard"] / total)],
        ["Dead (hit rate = 0%)", str(s.get("n_dead", 0)), _fmt_pct(s["n_dead"] / total)],
    ]


def _trend_rows(data: list[dict[str, Any]]) -> list[list[str]]:
    return [
        [
            f"R{t['from_round']}->R{t['to_round']}",
            str(t["n_improved"]),
            str(t["n_regressed"]),
            str(t["n_unchanged"]),
        ]
        for t in data
    ]


_TableConfig = dict[str, Any]

_TABLE_CONFIGS: dict[str, _TableConfig] = {
    "comparison": {
        "headers": ["Strategy", "Baseline", "Best", "Delta", "95% CI", "Rounds", "Budget", "Stop"],
        "rows": _comparison_rows,
        "guard": None,
    },
    "significance": {
        "headers": ["Campaign A", "Campaign B", "p-value", "Significance"],
        "rows": _significance_rows,
        "guard": "pairwise_significance",
    },
    "convergence": {
        "headers": _convergence_headers,
        "rows": _convergence_rows,
        "guard": "convergence",
    },
    "parameter_impact": {
        "headers": ["Axis", "Effect Size", "Consistency", "Classification", "Best Value", "n"],
        "rows": _parameter_impact_rows,
        "guard": "parameter_impact",
    },
    "failure_analysis": {
        "headers": ["Pattern", "Count", "Fraction", "Example Queries"],
        "rows": _failure_rows,
        "guard": None,  # guard is the data itself (list)
    },
    "query_difficulty": {
        "headers": ["Class", "Count", "Fraction"],
        "rows": _difficulty_rows,
        "guard": "summary",
    },
    "trends": {
        "headers": ["Transition", "Improved", "Regressed", "Unchanged"],
        "rows": _trend_rows,
        "guard": None,  # guard is the data itself (list)
    },
}


def render_table(data: Any, config_name: str) -> str:
    """Generic table renderer driven by ``_TABLE_CONFIGS``.

    Args:
        data: The structured data to render (dict or list depending on table).
        config_name: Key into ``_TABLE_CONFIGS``.

    Returns:
        Markdown table string, or ``""`` if the guard path is empty.
    """
    cfg = _TABLE_CONFIGS[config_name]

    # Check guard — either a key path into data, or None (data itself is the guard)
    guard = cfg["guard"]
    if guard is not None:
        guarded = data.get(guard) if isinstance(data, dict) else data
        if not guarded:
            return ""
    elif not data:
        return ""

    headers = cfg["headers"](data) if callable(cfg["headers"]) else cfg["headers"]
    rows = cfg["rows"](data)
    return _md_table(headers, rows)


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
            c for cid in campaign_ids if (c := store.campaigns.load(backend_id, cid)) is not None
        ]
    else:
        summaries = store.campaigns.list_all(backend_id)
        campaigns = [
            c
            for s in summaries
            if (c := store.campaigns.load(backend_id, s["campaign_id"])) is not None
        ]

    if not campaigns:
        return "# Supplemental Materials\n\nNo campaigns found.\n"

    comparison = compare_campaigns(campaigns)

    parts = ["# Supplemental Materials\n"]

    if "comparison" in active_sections:
        parts.append("## Campaign Comparison\n")
        parts.append(render_table(comparison, "comparison"))
        parts.append("")

    if "convergence" in active_sections:
        table = render_table(comparison, "convergence")
        if table:
            parts.append("## Convergence\n")
            parts.append(table)
            parts.append("")

    if "significance" in active_sections:
        table = render_table(comparison, "significance")
        if table:
            parts.append("## Pairwise Significance\n")
            parts.append(table)
            parts.append("")

    if "parameter_impact" in active_sections and search_memory is not None:
        memory_summary = export_search_memory_summary(search_memory)
        parts.append("## Parameter Impact\n")
        parts.append(render_table(memory_summary, "parameter_impact"))
        parts.append("")

        qp = memory_summary["query_patterns"]
        parts.append(
            f"Query distribution: {qp['total_queries']} total "
            f"({qp['n_easy']} easy, {qp['n_discriminating']} discriminating, "
            f"{qp['n_hard']} hard, {qp['n_dead']} dead)\n"
        )

    if "failure_analysis" in active_sections and failure_analysis is not None:
        failures = export_failure_analysis(failure_analysis)
        table = render_table(failures, "failure_analysis")
        if table:
            parts.append("## Failure Analysis\n")
            parts.append(table)
            parts.append("")

    if "query_difficulty" in active_sections and query_difficulty is not None:
        diff = export_query_difficulty(query_difficulty)
        table = render_table(diff, "query_difficulty")
        if table:
            parts.append("## Query Difficulty\n")
            parts.append(table)
            parts.append("")

    if "trends" in active_sections and trend_analysis is not None:
        trends = export_trend_analysis(trend_analysis)
        table = render_table(trends, "trends")
        if table:
            parts.append("## Round Stability\n")
            parts.append(table)
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
            c for cid in campaign_ids if (c := store.campaigns.load(backend_id, cid)) is not None
        ]
    else:
        summaries = store.campaigns.list_all(backend_id)
        campaigns = [
            c
            for s in summaries
            if (c := store.campaigns.load(backend_id, s["campaign_id"])) is not None
        ]

    result: dict[str, Any] = {
        "comparison": compare_campaigns(campaigns) if campaigns else {},
        "campaigns": {
            c["campaign_id"]: {
                "metadata": {k: v for k, v in c.items() if k not in ("trials",)},
                "trials": flatten_campaign_trials(c),
            }
            for c in campaigns
        },
        "reproducibility": build_reproducibility_manifest(
            campaigns,
            backend_id,
            pipeline_schema,
        ),
    }

    if search_memory is not None:
        result["search_memory"] = export_search_memory_summary(search_memory)

    return result
