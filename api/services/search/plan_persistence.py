"""Smart search plan persistence and candidate coverage analysis.

Plan identity hashing, serialization, and deserialization for smart search
plans.  Also contains candidate coverage analysis (ground truth presence
in candidate lists).
"""
from __future__ import annotations

import hashlib
import json
import statistics
from typing import Any

from api.models.opt_search_point import OptSearchPoint


SSPLAN_PREFIX = "ssplan_"


def smart_search_plan_identity(
    baseline_instruction: str,
    variant_library: dict,
    smart_search_config: dict,
    improvement_areas: str,
    seed: int = 42,
) -> str:
    """Compute a stable identity hash for a smart search plan."""
    payload = json.dumps(
        {
            "baseline_instruction": baseline_instruction,
            "variant_library": variant_library,
            "n_diagnostic": smart_search_config.get("n_diagnostic", 6),
            "max_rounds": smart_search_config.get("max_rounds", 3),
            "stop_threshold": smart_search_config.get("stop_threshold", 0.0),
            "improvement_areas": improvement_areas,
            "seed": seed,
        },
        sort_keys=True,
        default=str,
    )
    digest = hashlib.sha256(payload.encode()).hexdigest()[:12]
    return f"{SSPLAN_PREFIX}{digest}"


def serialize_smart_search_plan(
    plan_id: str,
    config: dict,
    baseline_opt: OptSearchPoint,
    search_baseline_opt: OptSearchPoint,
    layer1_fields: dict,
    diagnostic: list,
    diag_summary: dict,
    variant_library_hash: str,
) -> dict:
    """Serialize a smart search plan to a JSON-safe dict."""
    return {
        "plan_id": plan_id,
        "status": "diagnostic_built",
        "config": config,
        "baseline_ps": baseline_opt.model_dump(),
        "search_baseline_ps": search_baseline_opt.model_dump(),
        "layer1_fields": layer1_fields,
        "diagnostic": diagnostic,
        "diag_summary": diag_summary,
        "variant_library_hash": variant_library_hash,
    }


def deserialize_smart_search_plan(plan_data: dict) -> dict:
    """Reconstruct smart search plan objects from saved data."""
    return {
        "plan_id": plan_data["plan_id"],
        "status": plan_data["status"],
        "config": plan_data.get("config", {}),
        "baseline_ps": OptSearchPoint.from_prompt_fields(plan_data["baseline_ps"]),
        "search_baseline_ps": OptSearchPoint.from_prompt_fields(plan_data["search_baseline_ps"]),
        "layer1_fields": plan_data.get("layer1_fields", {}),
        "diagnostic": plan_data.get("diagnostic", []),
        "diag_summary": plan_data.get("diag_summary", {}),
        "variant_library_hash": plan_data.get("variant_library_hash", ""),
        "scan_results": plan_data.get("scan_results"),
        "search_results": plan_data.get("search_results"),
    }


def analyze_candidate_coverage(replay_results: list) -> dict:
    """Analyze ground truth presence in candidate lists.

    Returns dict with keys: rows (list of dicts), covered, total, coverage_pct,
    rank_distribution (dict), viable (bool).
    """
    rows = []
    for r in replay_results:
        if r.get("error"):
            continue
        pd_data = r.get("pipeline_data", {})
        candidates = pd_data.get("token_matched_candidates", [])
        gt = r["ground_truth"]

        candidate_names = []
        for c in candidates:
            if isinstance(c, (list, tuple)):
                candidate_names.append(c[0])
            else:
                candidate_names.append(str(c))

        gt_rank = None
        for i, name in enumerate(candidate_names):
            if name == gt:
                gt_rank = i + 1
                break

        rows.append({
            "query": r["query"][:50],
            "ground_truth": gt[:40],
            "in_candidates": gt_rank is not None,
            "gt_rank": gt_rank,
            "num_candidates": len(candidate_names),
        })

    total = len(rows)
    covered = sum(1 for r in rows if r["in_candidates"])
    coverage_pct = covered / total * 100 if total else 0

    # Rank distribution
    found_ranks = [r["gt_rank"] for r in rows if r["gt_rank"] is not None]
    rank_distribution: dict[str, Any] = {}
    if found_ranks:
        rank_distribution = {
            "rank_1": sum(1 for r in found_ranks if r == 1),
            "rank_2_5": sum(1 for r in found_ranks if 2 <= r <= 5),
            "rank_6_10": sum(1 for r in found_ranks if 6 <= r <= 10),
            "rank_11_20": sum(1 for r in found_ranks if 11 <= r <= 20),
            "rank_gt_20": sum(1 for r in found_ranks if r > 20),
            "mean_rank": sum(found_ranks) / len(found_ranks),
            "median_rank": statistics.median(found_ranks),
        }

    return {
        "rows": rows,
        "covered": covered,
        "total": total,
        "coverage_pct": coverage_pct,
        "rank_distribution": rank_distribution,
        "viable": coverage_pct > 50,
    }
