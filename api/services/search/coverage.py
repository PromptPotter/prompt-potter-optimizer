"""Coverage advisor, data inventory, and prompt result index.

assess_scan_coverage checks whether historical data covers OAT scan needs.
build_data_inventory summarizes what the historical index contains.
build_prompt_result_index builds the cross-run query lookup.
"""

import hashlib
import logging
from collections import defaultdict
from typing import Any

from api.models.prompt_state import PromptState
from api.services.project_store import ProjectStore
from api.services.search.plan_persistence import (
    deserialize_grid_plan,
    deserialize_smart_search_plan,
)
from api.services.search.smart_search import DEFAULT_DIAGNOSTIC_QUERIES

logger = logging.getLogger(__name__)


def _preview(value: Any, max_len: int = 40) -> str:
    """Truncated preview of a variant value."""
    s = str(value)
    if not s:
        return "(empty)"
    return s[:max_len] + ("..." if len(s) > max_len else "")


def build_prompt_result_index(
    store: ProjectStore,
    backend_id: str,
) -> dict[str, dict[str, dict]]:
    """Index: rendered_prompt_hash -> {query_string -> result_dict}.

    Scans all completed dataset runs, loads their items, and builds
    a cross-run lookup.  Enables accuracy estimation on any query subset
    for any previously evaluated prompt -- regardless of which plan or
    run originally produced the result.
    """
    index: dict[str, dict[str, dict]] = {}
    summaries = store.dataset_runs.list_all(backend_id)
    loaded = 0
    for summary in summaries:
        run_id = summary.get("run_id", "")
        if not run_id:
            continue
        detail = store.dataset_runs.load_by_id(backend_id, run_id)
        if not detail:
            continue
        rp_hash = detail.get("rendered_prompt_hash", "")
        if not rp_hash:
            continue
        items = detail.get("dataset_run_items", [])
        if not items:
            continue
        if rp_hash not in index:
            index[rp_hash] = {}
        for item in items:
            query = item.get("query", "")
            if query:
                index[rp_hash][query] = item
        loaded += 1

    logger.info(
        "build_prompt_result_index: %d runs -> %d unique prompts, %d total query results",
        loaded,
        len(index),
        sum(len(v) for v in index.values()),
    )
    return index


def assess_scan_coverage(
    baseline_ps: PromptState,
    variant_library: dict,
    diagnostic_queries: list,
    prompt_result_index: dict[str, dict[str, dict]],
    pipeline_params: dict | None = None,
    min_queries: int = DEFAULT_DIAGNOSTIC_QUERIES,
    axis_requirements: dict[str, int] | None = None,
) -> dict:
    """Check whether the historical index already covers the OAT scan needs.

    For each prompt-field axis: derive perturbed PromptStates, render them,
    hash, and check the index for cached diagnostic-query results.  Pipeline
    param axes always require fresh backend calls (same rendered prompt,
    different params -> index can't distinguish).

    Args:
        baseline_ps: The search baseline PromptState.
        variant_library: Full variant library dict.
        diagnostic_queries: List of diagnostic query dicts (must have ``"query"``).
        prompt_result_index: Historical index from ``build_prompt_result_index``.
        pipeline_params: Base pipeline parameters (for pipeline-param axes).
        min_queries: Minimum cached queries for a variant to count as "usable".
        axis_requirements: Per-axis: how many non-baseline values must be usable.
            ``None`` -> require all non-baseline values.

    Returns:
        Dict with per-axis detail, summary counts, and recommendation string.
    """
    axis_requirements = axis_requirements or {}
    diag_query_strings = {q["query"] for q in diagnostic_queries if q.get("query")}
    n_diagnostic = len(diag_query_strings)

    # --- Baseline coverage ---
    baseline_rendered = baseline_ps.render()
    baseline_rp_hash = hashlib.sha256(baseline_rendered.encode()).hexdigest()[:16]
    baseline_cached = prompt_result_index.get(baseline_rp_hash, {})
    baseline_hits = sum(1 for q in diag_query_strings if q in baseline_cached)

    axes_detail: list[dict] = []
    total_calls_saved = 0
    total_calls_needed = 0

    prompt_fields = variant_library.get("prompt_fields", {})
    pipeline_param_defs = variant_library.get("pipeline_params", {})

    # --- Prompt-field axes ---
    for axis_name, values in prompt_fields.items():
        current_val = getattr(baseline_ps, axis_name, "")
        non_baseline = [v for v in values if v != current_val]
        if not non_baseline:
            continue

        variants_detail: list[dict] = []
        usable_count = 0
        axis_saved = 0
        axis_needed = 0

        for value in non_baseline:
            perturbed = baseline_ps.derive(**{axis_name: value})
            rendered = perturbed.render()
            rp_hash = hashlib.sha256(rendered.encode()).hexdigest()[:16]
            cached = prompt_result_index.get(rp_hash, {})
            n_cached = sum(1 for q in diag_query_strings if q in cached)
            is_usable = n_cached >= min_queries
            if is_usable:
                usable_count += 1
            axis_saved += n_cached
            axis_needed += max(0, n_diagnostic - n_cached)
            variants_detail.append({
                "value_preview": _preview(value),
                "n_cached": n_cached,
                "usable": is_usable,
            })

        required = axis_requirements.get(axis_name, len(non_baseline))
        sufficient = usable_count >= required
        total_calls_saved += axis_saved
        if not sufficient:
            total_calls_needed += axis_needed
        axes_detail.append({
            "axis": axis_name,
            "axis_type": "prompt_field",
            "n_values": len(non_baseline),
            "n_usable": usable_count,
            "n_required": required,
            "sufficient": sufficient,
            "variants": variants_detail,
        })

    # --- Pipeline-param axes ---
    pp_calls_needed = 0
    base_params = dict(pipeline_params or {})
    for axis_name, values in pipeline_param_defs.items():
        current_val = base_params.get(axis_name)
        non_baseline = [v for v in values if v != current_val]
        if not non_baseline:
            continue
        n_calls = len(non_baseline) * n_diagnostic
        pp_calls_needed += n_calls
        total_calls_needed += n_calls
        required = axis_requirements.get(axis_name, len(non_baseline))
        axes_detail.append({
            "axis": axis_name,
            "axis_type": "pipeline_param",
            "n_values": len(non_baseline),
            "n_usable": 0,
            "n_required": required,
            "sufficient": False,
            "note": "Pipeline params require fresh backend calls",
        })

    # --- Summary ---
    pf_axes = [a for a in axes_detail if a["axis_type"] == "prompt_field"]
    pp_axes = [a for a in axes_detail if a["axis_type"] == "pipeline_param"]
    pf_satisfied = sum(1 for a in pf_axes if a["sufficient"])
    all_satisfied = all(a["sufficient"] for a in axes_detail)

    # Recommendation text
    if all_satisfied:
        recommendation = (
            f"All {len(pf_axes)} prompt field axes covered. "
            "Scan can be skipped — historical data is sufficient."
        )
    else:
        gap_names = [a["axis"] for a in axes_detail if not a["sufficient"]]
        recommendation = (
            f"{pf_satisfied}/{len(pf_axes)} prompt field axes covered. "
            f"Run scan to fill gaps on: {', '.join(gap_names)}."
        )
        if total_calls_needed > 0:
            recommendation += (
                " Tip: lower min_queries or reduce axis_requirements"
                " to accept sparser coverage."
            )

    return {
        "n_diagnostic": n_diagnostic,
        "min_queries": min_queries,
        "baseline_coverage": {
            "n_cached": baseline_hits,
            "n_needed": n_diagnostic,
            "sufficient": baseline_hits >= n_diagnostic,
        },
        "axes": axes_detail,
        "summary": {
            "prompt_field_axes_satisfied": pf_satisfied,
            "prompt_field_axes_total": len(pf_axes),
            "pipeline_param_axes": len(pp_axes),
            "backend_calls_needed": total_calls_needed,
            "prompt_field_calls_needed": total_calls_needed - pp_calls_needed,
            "pipeline_param_calls_needed": pp_calls_needed,
            "backend_calls_saved": total_calls_saved,
            "all_satisfied": all_satisfied,
        },
        "recommendation": recommendation,
    }


# ---------------------------------------------------------------------------
# Data inventory
# ---------------------------------------------------------------------------

# Layer 1 fields worth tracking as axes (skip instruction — always changes
# between baselines due to the decomposition, and few_shot_examples).
_INVENTORY_AXES = (
    "persona", "task_intent", "problem_description",
    "thinking_style", "answer_format",
)


def build_data_inventory(
    prompt_result_index: dict[str, dict[str, dict]],
    store: ProjectStore,
    backend_id: str,
) -> dict:
    """Summarise what the historical index contains, organised by axis.

    Discovers plan baselines from stored grid / smart-search plans, then
    for every indexed prompt that belongs to a plan, compares it to **its
    own plan baseline** and tallies which Layer 1 axes were changed.

    Returns a dict with total counts, per-axis breakdown, and unmatched
    prompt counts (prompts in the index that don't belong to any plan).
    """
    # 1. Collect PromptStates + their plan baselines from stored plans ------
    #    rp_hash -> (PromptState, plan_baseline_ps)
    rp_to_ps: dict[str, tuple[PromptState, PromptState]] = {}
    baseline_rp_hashes: set[str] = set()

    # Grid plans
    for summary in store.grid_plans.list_all(backend_id):
        plan_id = summary.get("plan_id", "")
        if not plan_id:
            continue
        plan_data = store.grid_plans.load(backend_id, plan_id)
        if not plan_data:
            continue
        (_, state_lookup, _, _, _, baseline_ps) = deserialize_grid_plan(plan_data)
        bl_hash = hashlib.sha256(baseline_ps.render().encode()).hexdigest()[:16]
        baseline_rp_hashes.add(bl_hash)
        rp_to_ps.setdefault(bl_hash, (baseline_ps, baseline_ps))
        for ps in state_lookup.values():
            h = hashlib.sha256(ps.render().encode()).hexdigest()[:16]
            rp_to_ps.setdefault(h, (ps, baseline_ps))

    # Smart search plans
    pipeline_params: dict[str, dict] = {}
    for summary in store.smart_search.list_all(backend_id):
        plan_id = summary.get("plan_id", "")
        if not plan_id:
            continue
        plan_data = store.smart_search.load(backend_id, plan_id)
        if not plan_data:
            continue
        ss = deserialize_smart_search_plan(plan_data)
        search_bl = ss["search_baseline_ps"]
        bl_hash = hashlib.sha256(search_bl.render().encode()).hexdigest()[:16]
        baseline_rp_hashes.add(bl_hash)
        rp_to_ps.setdefault(bl_hash, (search_bl, search_bl))

        # Collect pipeline param profiles from scan results
        scan = ss.get("scan_results") or {}
        for profile in scan.get("axis_profiles", []):
            if profile.get("axis_type") != "pipeline_param":
                continue
            axis_name = profile["axis"]
            pipeline_params[axis_name] = {
                "scanned": True,
                "cardinality": profile.get("cardinality", 0),
                "sensitivity_range": profile.get("sensitivity_range", 0.0),
                "best_delta": profile.get("best_delta", 0.0),
                "exploration_budget": profile.get("exploration_budget", "skip"),
            }

    # 2. Walk the index and classify each prompt --------------------------
    total_prompts = len(prompt_result_index)
    total_results = sum(len(v) for v in prompt_result_index.values())

    matched_prompts = 0
    matched_results = 0
    baseline_prompts = 0
    baseline_queries = 0

    axis_prompts: dict[str, int] = defaultdict(int)
    axis_queries: dict[str, int] = defaultdict(int)
    axis_values: dict[str, set] = defaultdict(set)

    for rp_hash, queries in prompt_result_index.items():
        n_queries = len(queries)
        entry = rp_to_ps.get(rp_hash)
        if entry is None:
            continue  # unmatched

        ps, plan_baseline = entry
        matched_prompts += 1
        matched_results += n_queries

        if rp_hash in baseline_rp_hashes:
            baseline_prompts += 1
            baseline_queries += n_queries

        # Identify changed axes vs the plan's own baseline
        for field in _INVENTORY_AXES:
            ps_val = getattr(ps, field)
            bl_val = getattr(plan_baseline, field)
            if ps_val != bl_val:
                axis_prompts[field] += 1
                axis_queries[field] += n_queries
                axis_values[field].add(ps_val)

    # 3. Build return shape -----------------------------------------------
    axes_dict: dict[str, dict] = {}
    for field in _INVENTORY_AXES:
        if axis_prompts.get(field, 0) > 0:
            axes_dict[field] = {
                "n_prompts": axis_prompts[field],
                "n_queries": axis_queries[field],
                "distinct_values": len(axis_values[field]),
            }

    return {
        "total_prompts": total_prompts,
        "total_results": total_results,
        "matched_prompts": matched_prompts,
        "matched_results": matched_results,
        "baseline_prompts": baseline_prompts,
        "baseline_queries": baseline_queries,
        "axes": axes_dict,
        "unmatched_prompts": total_prompts - matched_prompts,
        "unmatched_results": total_results - matched_results,
        "pipeline_params": pipeline_params,
    }
