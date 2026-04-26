"""Markdown rendering for campaign reports — supplemental tables + document.

Pure display layer over the data transforms in
``application/campaign/reporting.py``. Safe to import from CLI, notebook,
and future webapp. No persistence, no logging.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any

from promptpotter.application.campaign.reporting import (
    build_reproducibility_manifest,
    compare_campaigns,
    export_failure_analysis,
    export_query_difficulty,
    export_search_memory_summary,
    load_campaigns,
)
from promptpotter.presentation.views.display_primitives import (
    DIM,
    RESET,
    _box_bottom,
    _box_line,
    _box_top,
)


def render_markdown_box(title: str, content: str, empty_label: str, *, width: int = 74) -> str:
    """Render a titled box around ``content``, or a dim empty label."""
    if not content:
        return f"  {DIM}{empty_label}{RESET}"
    out = [f"  {_box_top(title, width=width)}"]
    for line in content.split("\n"):
        out.append(f"  {_box_line(line, width=width)}")
    out.append(f"  {_box_bottom(width=width)}")
    return "\n".join(out)


if TYPE_CHECKING:
    from promptpotter.application.intelligence.search_memory import SearchMemory
    from promptpotter.domain.analysis import FailureAnalysis, QueryDifficulty
    from promptpotter.domain.pipeline_schema import PipelineSchema
    from promptpotter.infrastructure.store import Stores

__all__ = [
    "fmt_ci",
    "fmt_pct",
    "fmt_pvalue",
    "generate_supplemental",
    "render_pipeline_overrides",
    "render_table",
]


# ---------------------------------------------------------------------------
# Primitive formatters — public, shared by views, dashboard, notebook UI
# ---------------------------------------------------------------------------


def fmt_pct(value: Any) -> str:
    """Render a fraction in [0, 1] as ``"XX.X%"``; non-numerics → ``"-"``."""
    try:
        return f"{float(value):.1%}"
    except (TypeError, ValueError):
        return "-"


def fmt_ci(lower: float, upper: float) -> str:
    """Format a 95% CI bracket: ``[X.X%-Y.Y%]``."""
    return f"[{lower:.1%}-{upper:.1%}]"


def fmt_pvalue(p: float) -> str:
    """Format a p-value with significance tier (***, **, *, ns)."""
    if p < 0.001:
        return "p<0.001 ***"
    if p < 0.01:
        return f"p={p:.3f} **"
    if p < 0.05:
        return f"p={p:.2f} *"
    return f"p={p:.2f} (ns)"


def render_pipeline_overrides(
    pipeline_params: dict | None,
    pipeline_schema: PipelineSchema | None = None,
) -> str:
    """Render ``pipeline_params`` as a copy-paste-ready ``pipeline_overrides`` block.

    Nested format ``{"node_name": {"param": value}}``.  When ``pipeline_schema``
    is given, only keys listed in each node's ``param_keys`` are shown; nodes
    without a schema entry fall back to all key/value pairs.  Returns an empty
    string when there is nothing to render.
    """
    if not pipeline_params:
        return ""

    node_entries: list[tuple[str, dict]] = []
    for key, val in pipeline_params.items():
        if key == "steps" or not isinstance(val, dict):
            continue
        tunable: dict = {}
        if pipeline_schema:
            node = pipeline_schema.get_node(key)
            if node:
                tunable = {k: v for k, v in val.items() if k in node.param_keys}
        if not tunable:
            tunable = val
        if tunable:
            node_entries.append((key, tunable))

    if not node_entries:
        return ""

    rule = "─" * 60
    parts = [
        "  Copy-paste pipeline_overrides:",
        f"  {rule}",
        '  "pipeline_overrides": {',
    ]
    for node_name, params in node_entries:
        parts.append(f'      "{node_name}": {{')
        for param, val in params.items():
            parts.append(f'          "{param}": {val!r},')
        parts.append("      },")
    parts.append("  }")
    parts.append(f"  {rule}")
    return "\n".join(parts)


# ---------------------------------------------------------------------------
# Per-table row builders
# ---------------------------------------------------------------------------


def _comparison_rows(data: dict[str, Any]) -> list[list[str]]:
    return [
        [
            s["name"],
            fmt_pct(s["baseline"]),
            fmt_pct(s["best"]),
            f"+{s['improvement']:.1%}" if s["improvement"] > 0 else fmt_pct(s["improvement"]),
            fmt_ci(s["ci_lower"], s["ci_upper"]),
            str(s["rounds_to_best"]),
            str(s["scoring_budget"]),
            s.get("stop_reason", ""),
        ]
        for s in data["summary_table"]
    ]


def _significance_rows(data: dict[str, Any]) -> list[list[str]]:
    return [
        [p["campaign_a"], p["campaign_b"], f"{p['p_value']:.4f}", fmt_pvalue(p["p_value"])]
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
            row.append(fmt_pct(match["accuracy"]) if match else "-")
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
                fmt_pct(ai["consistency"]),
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
            fmt_pct(f["fraction"]),
            "; ".join(f["example_queries"][:2]),
        ]
        for f in data
    ]


def _difficulty_rows(data: dict[str, Any]) -> list[list[str]]:
    s = data["summary"]
    total = s.get("total", 1) or 1
    return [
        ["Easy (hit rate >= 80%)", str(s.get("n_easy", 0)), fmt_pct(s["n_easy"] / total)],
        [
            "Discriminating (var >= 0.1)",
            str(s.get("n_discriminating", 0)),
            fmt_pct(s["n_discriminating"] / total),
        ],
        ["Hard (0 < hit rate < 20%)", str(s.get("n_hard", 0)), fmt_pct(s["n_hard"] / total)],
        ["Dead (hit rate = 0%)", str(s.get("n_dead", 0)), fmt_pct(s["n_dead"] / total)],
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
}


def render_table(data: Any, config_name: str) -> str:
    """Generic table renderer driven by ``_TABLE_CONFIGS``.

    Returns the rendered markdown, or ``""`` if the guard path is empty.
    """
    cfg = _TABLE_CONFIGS[config_name]

    guard = cfg["guard"]
    if guard is not None:
        guarded = data.get(guard) if isinstance(data, dict) else data
        if not guarded:
            return ""
    elif not data:
        return ""

    headers = cfg["headers"](data) if callable(cfg["headers"]) else cfg["headers"]
    rows = cfg["rows"](data)

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


# ---------------------------------------------------------------------------
# Document assembly
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
    store: Stores,
    backend_id: str,
    *,
    campaign_ids: list[str] | None = None,
    search_memory: SearchMemory | None = None,
    pipeline_schema: PipelineSchema | None = None,
    failure_analysis: FailureAnalysis | None = None,
    query_difficulty: QueryDifficulty | None = None,
    sections: list[str] | None = None,
) -> str:
    """Assemble full supplemental materials document.

    Loads campaigns from store, combines with optional pre-computed analysis,
    and renders each section as markdown.
    """
    active_sections = sections or _DEFAULT_SECTIONS
    campaigns = load_campaigns(store, backend_id, campaign_ids)

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

    if "reproducibility" in active_sections:
        manifest = build_reproducibility_manifest(campaigns, backend_id, pipeline_schema)
        parts.append("## Reproducibility\n")
        parts.append("```json")
        parts.append(json.dumps(manifest, indent=2, default=str))
        parts.append("```\n")

    return "\n".join(parts)
