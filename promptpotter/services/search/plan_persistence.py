"""Smart search plan persistence.

Plan identity hashing, serialization, and deserialization for smart search
plans.
"""
from __future__ import annotations

import hashlib
import json

from promptpotter.models.opt_search_point import OptSearchPoint

SSPLAN_PREFIX = "ssplan_"


def smart_search_plan_identity(
    baseline_instruction: str,
    variant_library: dict,
    smart_search_config: dict,
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
