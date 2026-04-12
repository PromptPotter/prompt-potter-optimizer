"""Campaign reporting — data export and supplemental materials generation.

Two layers in one module:
- Pure data transforms (flatten, compare, export) for paper-ready analysis
- Markdown formatting for supplemental documents

No persistence — takes already-loaded data, returns dicts/strings.
"""

from __future__ import annotations

__all__ = [
    "build_reproducibility_manifest",
    "compare_campaigns",
    "export_failure_analysis",
    "export_query_difficulty",
    "export_search_memory_summary",
    "flatten_campaign_trials",
    "generate_export_json",
    "generate_supplemental",
    "render_table",
]

import json
import logging
import platform
import sys
from dataclasses import asdict
from typing import TYPE_CHECKING, Any

from promptpotter.services.search.failure_group_analysis import proportion_test, wilson_ci

if TYPE_CHECKING:
    from promptpotter.domain.analysis import FailureAnalysis, QueryDifficulty
    from promptpotter.domain.pipeline_schema import PipelineSchema
    from promptpotter.services.project_store import ProjectStore
    from promptpotter.services.search.search_memory import SearchMemory

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Data transforms — pure functions, no I/O
# ---------------------------------------------------------------------------


def flatten_campaign_trials(campaign: dict[str, Any]) -> list[dict[str, Any]]:
    """Flatten a campaign's trial summaries to tabular rows.

    Each row contains round-level stats plus Wilson CI and improvement delta.
    Operates on the campaign dict returned by ``CampaignStore.load()``.
    """
    baseline_acc = campaign.get("baseline_accuracy", 0.0)
    rows: list[dict[str, Any]] = []

    for trial in campaign.get("trials", []):
        hits = trial.get("hits", 0)
        total = trial.get("total", 0)
        accuracy = trial.get("accuracy", 0.0)
        ci_lower, ci_upper = wilson_ci(hits, total)

        rows.append(
            {
                "campaign_id": campaign["campaign_id"],
                "round": trial.get("round", 0),
                "label": trial.get("label", ""),
                "accuracy": accuracy,
                "hits": hits,
                "total": total,
                "improved": trial.get("improved", False),
                "baseline_accuracy": baseline_acc,
                "improvement_delta": accuracy - baseline_acc,
                "ci_lower": ci_lower,
                "ci_upper": ci_upper,
            }
        )

    rows.sort(key=lambda r: r["round"])
    return rows


def compare_campaigns(campaigns: list[dict[str, Any]]) -> dict[str, Any]:
    """Compare N campaigns side-by-side for paper tables.

    Returns:
        summary_table: per-campaign summary rows
        convergence: per-campaign round-by-round accuracy series
        pairwise_significance: p-values between each pair's best trials
    """
    summary_table: list[dict[str, Any]] = []
    convergence: dict[str, list[dict[str, Any]]] = {}
    best_trials: list[dict[str, Any]] = []

    for campaign in campaigns:
        cid = campaign["campaign_id"]
        trials = sorted(campaign.get("trials", []), key=lambda t: t.get("round", 0))

        baseline = campaign.get("baseline_accuracy", 0.0)
        best_acc = campaign.get("best_accuracy", 0.0)

        # Find round that achieved best
        rounds_to_best = 0
        best_hits, best_total = 0, 0
        for trial in trials:
            if trial.get("accuracy", 0.0) >= best_acc:
                rounds_to_best = trial.get("round", 0)
                best_hits = trial.get("hits", 0)
                best_total = trial.get("total", 0)
                break

        ci_lower, ci_upper = wilson_ci(best_hits, best_total)

        summary_table.append(
            {
                "campaign_id": cid,
                "name": campaign.get("name", cid),
                "baseline": baseline,
                "best": best_acc,
                "improvement": best_acc - baseline,
                "rounds_to_best": rounds_to_best,
                "total_rounds": len(trials),
                "stop_reason": campaign.get("stop_reason", ""),
                "scoring_budget": sum(t.get("total", 0) for t in trials),
                "ci_lower": ci_lower,
                "ci_upper": ci_upper,
            }
        )

        convergence[cid] = [
            {"round": t.get("round", 0), "accuracy": t.get("accuracy", 0.0)} for t in trials
        ]

        best_trials.append(
            {
                "campaign_id": cid,
                "hits": best_hits,
                "total": best_total,
            }
        )

    # Pairwise significance tests
    pairwise: list[dict[str, Any]] = []
    for i, a in enumerate(best_trials):
        for b in best_trials[i + 1 :]:
            p = proportion_test(a["hits"], a["total"], b["hits"], b["total"])
            pairwise.append(
                {
                    "campaign_a": a["campaign_id"],
                    "campaign_b": b["campaign_id"],
                    "p_value": p,
                }
            )

    return {
        "summary_table": summary_table,
        "convergence": convergence,
        "pairwise_significance": pairwise,
    }


def export_search_memory_summary(memory: SearchMemory) -> dict[str, Any]:
    """Extract paper-relevant fields from SearchMemory.

    Returns structured dicts for parameter impact, failure modes,
    and query pattern summaries.
    """
    axis_impacts = memory.axis_rankings()
    parameter_impact = [
        {
            "axis": ai.axis,
            "effect_size": ai.effect_size,
            "consistency": ai.consistency,
            "classification": ai.classification,
            "sample_count": ai.sample_count,
            "top_values": [
                {"value": v.value_preview, "mean_accuracy": v.mean_accuracy, "n": v.sample_count}
                for v in ai.top_values
            ],
        }
        for ai in axis_impacts
    ]

    clusters = memory.failure_clusters()
    failure_modes = [
        {
            "mode": fc.failure_mode,
            "query_count": fc.query_count,
            "fraction": fc.fraction,
            "example_queries": fc.example_queries,
        }
        for fc in clusters
    ]

    tractability = memory.query_tractability()
    n_easy = sum(1 for q in tractability if q.hit_rate >= 0.8)
    n_hard = sum(1 for q in tractability if 0 < q.hit_rate < 0.2)
    n_dead = sum(1 for q in tractability if q.hit_rate == 0.0)
    n_discriminating = sum(1 for q in tractability if q.variance >= 0.1)

    return {
        "parameter_impact": parameter_impact,
        "failure_modes": failure_modes,
        "query_patterns": {
            "total_queries": len(tractability),
            "n_easy": n_easy,
            "n_discriminating": n_discriminating,
            "n_hard": n_hard,
            "n_dead": n_dead,
        },
        "bottleneck_distribution": memory.bottleneck_distribution(),
    }


def export_failure_analysis(analysis: FailureAnalysis) -> list[dict[str, Any]]:
    """Flatten FailureAnalysis to table rows."""
    return [
        {
            "pattern": fp.name,
            "query_count": fp.query_count,
            "fraction": fp.fraction,
            "diagnostic_key": list(fp.diagnostic_key),
            "example_queries": fp.example_queries,
        }
        for fp in analysis.patterns
    ]


def export_query_difficulty(difficulty: QueryDifficulty) -> dict[str, Any]:
    """Export QueryDifficulty as summary + per-query table."""
    return {
        "summary": {
            "n_easy": len(difficulty.easy),
            "n_discriminating": len(difficulty.discriminating),
            "n_hard": len(difficulty.hard),
            "n_dead": len(difficulty.dead),
            "total": len(difficulty.profiles),
        },
        "profiles": [asdict(p) for p in difficulty.profiles],
    }


def build_reproducibility_manifest(
    campaigns: list[dict[str, Any]],
    backend_id: str,
    pipeline_schema: PipelineSchema | None = None,
) -> dict[str, Any]:
    """Build reproducibility metadata for paper supplemental.

    Captures config hashes, pipeline snapshot, runtime environment.
    """
    manifest: dict[str, Any] = {
        "backend_id": backend_id,
        "python_version": sys.version,
        "platform": platform.platform(),
        "campaigns": [
            {
                "campaign_id": c["campaign_id"],
                "config": c.get("config", {}),
                "n_trials": c.get("n_trials", 0),
                "status": c.get("status", ""),
            }
            for c in campaigns
        ],
    }

    if pipeline_schema:
        manifest["pipeline"] = {
            "name": pipeline_schema.name,
            "version": pipeline_schema.version,
            "nodes": list(pipeline_schema.active_steps),
        }

    return manifest


# ---------------------------------------------------------------------------
# Markdown formatting — display-only, no persistence
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


def _comparison_rows(data: dict[str, Any]) -> list[list[str]]:
    return [
        [
            s["name"],
            _fmt_pct(s["baseline"]),
            _fmt_pct(s["best"]),
            f"+{s['improvement']:.1%}" if s["improvement"] > 0 else _fmt_pct(s["improvement"]),
            _fmt_ci(s["ci_lower"], s["ci_upper"]),
            str(s["rounds_to_best"]),
            str(s["scoring_budget"]),
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


def _load_campaigns(
    store: ProjectStore,
    backend_id: str,
    campaign_ids: list[str] | None = None,
) -> list[dict]:
    """Load campaigns from store, filtering by IDs if provided."""
    if campaign_ids:
        return [
            c for cid in campaign_ids if (c := store.campaigns.load(backend_id, cid)) is not None
        ]
    summaries = store.campaigns.list_all(backend_id)
    return [
        c
        for s in summaries
        if (c := store.campaigns.load(backend_id, s["campaign_id"])) is not None
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
        sections: Which sections to include (default: all).

    Returns:
        Complete markdown string.
    """
    active_sections = sections or _DEFAULT_SECTIONS
    campaigns = _load_campaigns(store, backend_id, campaign_ids)

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
    campaigns = _load_campaigns(store, backend_id, campaign_ids)

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
