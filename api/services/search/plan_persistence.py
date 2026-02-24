"""Grid plan and smart search plan persistence.

Stable identity hashing, serialization, and deserialization for
grid search and smart search plans.
"""

import hashlib
import json
from typing import Any

from api.models.prompt_state import PromptState

GRIDPLAN_PREFIX = "gridplan_"
SSPLAN_PREFIX = "ssplan_"


# ---------------------------------------------------------------------------
# Grid plan persistence
# ---------------------------------------------------------------------------


def grid_plan_identity(
    grid_axes: dict,
    baseline_instruction: str,
    context_input: Any,
    grid_budget: int,
    exploration_rate: float,
    seed: int,
) -> str:
    """Compute a stable identity hash for a grid search plan.

    The hash covers user-controlled inputs only so the same config
    produces the same plan ID across kernel restarts.
    """
    payload = json.dumps(
        {
            "grid_axes": grid_axes,
            "baseline_instruction": baseline_instruction,
            "context_input": context_input
            if isinstance(context_input, str)
            else json.dumps(context_input, sort_keys=True),
            "grid_budget": grid_budget,
            "exploration_rate": exploration_rate,
            "seed": seed,
        },
        sort_keys=True,
    )
    digest = hashlib.sha256(payload.encode()).hexdigest()[:12]
    return f"{GRIDPLAN_PREFIX}{digest}"


def serialize_grid_plan(
    plan_id: str,
    grid_axes: dict,
    baseline_ps: PromptState,
    layer1_fields: dict,
    grid_points: list,
    state_lookup: dict,
    sampling_meta: dict,
) -> dict:
    """Serialize a grid search plan to a JSON-safe dict."""
    return {
        "plan_id": plan_id,
        "status": "in_progress",
        "grid_axes": grid_axes,
        "baseline_ps": baseline_ps.model_dump(),
        "layer1_fields": layer1_fields,
        "grid_points": grid_points,
        "state_lookup": {
            ps_id: ps.model_dump() for ps_id, ps in state_lookup.items()
        },
        "sampling_meta": sampling_meta,
    }


def deserialize_grid_plan(
    plan_data: dict,
) -> tuple:
    """Reconstruct grid plan objects from saved data.

    Returns:
        (grid_points, state_lookup, sampling_meta, grid_axes,
         layer1_fields, baseline_ps)
    """
    baseline_ps = PromptState(**plan_data["baseline_ps"])
    state_lookup = {
        ps_id: PromptState(**ps_data)
        for ps_id, ps_data in plan_data.get(
            "state_lookup", plan_data.get("ps_lookup", {})
        ).items()
    }
    return (
        plan_data.get("grid_points", plan_data.get("combinations", [])),
        state_lookup,
        plan_data["sampling_meta"],
        plan_data["grid_axes"],
        plan_data.get("layer1_fields", plan_data.get("structured_fields", {})),
        baseline_ps,
    )


# ---------------------------------------------------------------------------
# Smart search plan persistence
# ---------------------------------------------------------------------------


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
    prompt_keys = sorted(variant_library.get("prompt_fields", {}).keys())
    param_keys = sorted(variant_library.get("pipeline_params", {}).keys())
    payload = json.dumps(
        {
            "baseline_instruction": baseline_instruction,
            "prompt_field_keys": prompt_keys,
            "pipeline_param_keys": param_keys,
            "n_diagnostic": smart_search_config.get("n_diagnostic", 6),
            "max_rounds": smart_search_config.get("max_rounds", 3),
            "stop_threshold": smart_search_config.get("stop_threshold", 0.0),
            "improvement_areas": improvement_areas,
            "seed": seed,
        },
        sort_keys=True,
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
