"""Campaign reporting — pure data transforms over stored campaigns.

Returns JSON-serializable dicts. No markdown, no I/O beyond the Stores
reads explicitly requested. Markdown rendering of these structures lives
in ``presentation/views/formatting.py``.
"""

from __future__ import annotations

import platform
import sys
from dataclasses import asdict
from typing import TYPE_CHECKING, Any

from promptpotter.shared.statistics import proportion_test, wilson_ci

if TYPE_CHECKING:
    from promptpotter.application.intelligence.search_memory import SearchMemory
    from promptpotter.domain.analysis import FailureAnalysis, QueryDifficulty
    from promptpotter.domain.pipeline_schema import PipelineSchema
    from promptpotter.infrastructure.store import Stores

__all__ = [
    "build_reproducibility_manifest",
    "compare_campaigns",
    "export_failure_analysis",
    "export_query_difficulty",
    "export_search_memory_summary",
    "flatten_campaign_trials",
    "generate_export_json",
    "load_campaigns",
]


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
    """Extract paper-relevant fields from SearchMemory."""
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
    """Build reproducibility metadata for paper supplemental."""
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


def load_campaigns(
    store: Stores,
    backend_id: str,
    campaign_ids: list[str] | None = None,
) -> list[dict]:
    """Load campaigns from store, filtering by IDs if provided."""
    return store.campaigns.load_many(backend_id, campaign_ids)


def generate_export_json(
    store: Stores,
    backend_id: str,
    *,
    campaign_ids: list[str] | None = None,
    search_memory: SearchMemory | None = None,
    pipeline_schema: PipelineSchema | None = None,
) -> dict[str, Any]:
    """Export all campaign data as a single JSON-serializable dict."""
    campaigns = load_campaigns(store, backend_id, campaign_ids)

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
