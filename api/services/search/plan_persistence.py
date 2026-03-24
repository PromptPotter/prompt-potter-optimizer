"""Smart search plan persistence.

Stable identity hashing, serialization, and deserialization for
smart search plans.
"""

import hashlib
import json

from api.models.prompt_state import PromptState

SSPLAN_PREFIX = "ssplan_"


def smart_search_plan_identity(
    baseline_instruction: str,
    variant_library: dict,
    smart_search_config: dict,
    improvement_areas: str,
    seed: int = 42,
) -> str:
    """Compute a stable identity hash for a smart search plan.

    Hashes user-controlled inputs so the same config produces the same
    plan ID across kernel restarts.
    """
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
    baseline_ps: PromptState,
    search_baseline_ps: PromptState,
    layer1_fields: dict,
    diagnostic: list,
    diag_summary: dict,
    variant_library_hash: str,
) -> dict:
    """Serialize a smart search plan to a JSON-safe dict.

    Returns a dict with status ``"diagnostic_built"``.
    """
    return {
        "plan_id": plan_id,
        "status": "diagnostic_built",
        "config": config,
        "baseline_ps": baseline_ps.model_dump(),
        "search_baseline_ps": search_baseline_ps.model_dump(),
        "layer1_fields": layer1_fields,
        "diagnostic": diagnostic,
        "diag_summary": diag_summary,
        "variant_library_hash": variant_library_hash,
    }


def deserialize_smart_search_plan(plan_data: dict) -> dict:
    """Reconstruct smart search plan objects from saved data.

    Returns a dict with all fields for easy access, including
    reconstructed PromptState objects.
    """
    return {
        "plan_id": plan_data["plan_id"],
        "status": plan_data["status"],
        "config": plan_data.get("config", {}),
        "baseline_ps": PromptState(**plan_data["baseline_ps"]),
        "search_baseline_ps": PromptState(**plan_data["search_baseline_ps"]),
        "layer1_fields": plan_data.get("layer1_fields", {}),
        "diagnostic": plan_data.get("diagnostic", []),
        "diag_summary": plan_data.get("diag_summary", {}),
        "variant_library_hash": plan_data.get("variant_library_hash", ""),
        "scan_results": plan_data.get("scan_results"),
        "search_results": plan_data.get("search_results"),
    }
