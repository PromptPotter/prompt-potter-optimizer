"""Coverage advisor, data inventory, and prompt result index.

assess_scan_coverage checks whether historical data covers OAT scan needs.
build_data_inventory summarizes what the historical index contains.
build_prompt_result_index builds the cross-run query lookup.
diagnose_scan_variants checks scan variant coverage via step-sequence matching.
"""
from __future__ import annotations

import hashlib
import json
import logging
from collections import defaultdict
from typing import TYPE_CHECKING, Any

from api.models.opt_search_point import OptSearchPoint
from api.models.pipeline_schema import is_result_step_compatible
from api.services.project_store import ProjectStore
from api.services.search.plan_persistence import deserialize_smart_search_plan
from api.services.search.preview import preview
from api.shared.constants import DEFAULT_DIAGNOSTIC_QUERIES, PROMPT_STRING_FIELDS
from api.shared.hashing import HASH_TRUNCATE

if TYPE_CHECKING:
    from api.models.pipeline_schema import PipelineSchema
    from api.models.search_point import JobSearchPoint

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Step-sequence matching helpers
# ---------------------------------------------------------------------------

def _steps_match(entry: dict, target_steps: frozenset[str]) -> bool:
    """Match by step sequence — which nodes ran."""
    entry_steps = frozenset((entry.get("pipeline_params") or {}).get("steps", []))
    # Both empty → match; otherwise exact set equality
    return entry_steps == target_steps


def _extract_param(
    pp: dict,
    param_name: str,
    pipeline_schema: PipelineSchema | None = None,
) -> Any:
    """Extract a parameter value from pipeline_params.

    Checks three locations (in order):
    1. Flat top-level: ``pp["max_sites"]``  (skips known node names)
    2. Schema-resolved nested: ``pp["web_search"]["max_sites"]``
    3. Brute-force scan of all node dicts
    """
    # Known node names — skip these in flat lookup (they're node config dicts)
    node_names: set[str] = set()
    if pipeline_schema:
        node_names = {n.name for n in pipeline_schema.nodes}
    else:
        # Heuristic: steps list contains node names
        node_names = set(pp.get("steps") or [])

    # 1. Flat top-level (skip node config dicts and "steps")
    if param_name in pp and param_name not in node_names and param_name != "steps":
        return pp[param_name]
    # 2. Schema-resolved
    if pipeline_schema:
        resolved = pipeline_schema.resolve_flat_param(param_name)
        if resolved:
            node, wire_key = resolved
            node_cfg = pp.get(node)
            if isinstance(node_cfg, dict) and wire_key in node_cfg:
                return node_cfg[wire_key]
    # 3. Brute-force: scan all node dicts
    for _k, v in pp.items():
        if isinstance(v, dict) and param_name in v:
            return v[param_name]
    return None


def diagnose_scan_variants(
    store: ProjectStore,
    backend_id: str,
    scan_variants: dict[str, list],
    baseline_sp: JobSearchPoint,
    prompt_node_names: list[str] | None = None,
    pipeline_schema: PipelineSchema | None = None,
) -> dict:
    """Check scan variant coverage using step-sequence matching.

    Matches historical runs by:
    1. **Step sequence** — which nodes ran (frozenset equality)
    2. **Rendered prompt hash** — for prompt-field variants
    3. **Parameter value extraction** — for pipeline-param variants,
       checking both flat and nested formats via ``_extract_param()``

    Returns dict with ``n_matching_runs``, ``n_matching_results``,
    and per-axis ``axes`` detail.
    """
    summaries = store.dataset_runs.list_all(backend_id)

    # Step 1: filter to runs with matching step sequence
    baseline_steps = frozenset(
        (baseline_sp.pipeline_params or {}).get("steps", []),
    )
    step_matches = [e for e in summaries if _steps_match(e, baseline_steps)]

    # Step 2: index step-matching runs by rendered_prompt_hash
    rp_index: dict[str, list[dict]] = defaultdict(list)
    for e in step_matches:
        rp_h = e.get("rendered_prompt_hash", "")
        if rp_h:
            rp_index[rp_h].append(e)

    # Baseline: match by prompt hash among step-matched runs
    baseline_rp_hash = hashlib.sha256(
        baseline_sp.render().encode(),
    ).hexdigest()[:HASH_TRUNCATE]
    baseline_entries = rp_index.get(baseline_rp_hash, [])
    n_baseline_results = sum(e.get("item_count", 0) for e in baseline_entries)

    # Per-axis coverage
    axes: dict[str, dict] = {}
    all_matching: set[str] = set()

    for axis_name, values in scan_variants.items():
        value_counts: dict[str, int] = {}
        value_scores: dict[str, dict] = {}
        is_prompt_field = (
            axis_name in PROMPT_STRING_FIELDS
            and baseline_sp.prompt_fields is not None
        )

        for v in values:
            key = json.dumps(v, sort_keys=True) if isinstance(v, dict) else str(v)

            if is_prompt_field:
                # Prompt variant: derive, render, match by rp_hash
                perturbed = baseline_sp.derive(prompt_fields={axis_name: v})
                rp_h = hashlib.sha256(
                    perturbed.render().encode(),
                ).hexdigest()[:HASH_TRUNCATE]
                entries = rp_index.get(rp_h, [])
            else:
                # Pipeline param variant: match by extracted value
                entries = [
                    e for e in step_matches
                    if _extract_param(
                        e.get("pipeline_params") or {},
                        axis_name, pipeline_schema,
                    ) == v
                ]

            value_counts[key] = sum(e.get("item_count", 0) for e in entries)
            for e in entries:
                all_matching.add(e.get("run_id", ""))
                if key not in value_scores:
                    scores = e.get("scores", {})
                    if scores:
                        value_scores[key] = scores

        axes[axis_name] = {
            "values": value_counts,
            "value_scores": value_scores,
            "other_count": 0,
            "total_matching": sum(value_counts.values()),
        }

    # Add baseline runs to matching set
    for e in baseline_entries:
        all_matching.add(e.get("run_id", ""))

    # Baseline scores
    baseline_scores = None
    if baseline_entries:
        first_scores = baseline_entries[0].get("scores", {})
        if first_scores:
            baseline_scores = first_scores

    return {
        "n_total_runs": len(summaries),
        "n_matching_runs": len(all_matching),
        "n_matching_results": n_baseline_results,
        "axes": axes,
        "baseline_scores": baseline_scores,
    }


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

    logger.debug(
        "build_prompt_result_index: %d runs -> %d unique prompts, %d total query results",
        loaded,
        len(index),
        sum(len(v) for v in index.values()),
    )
    return index


def assess_scan_coverage(
    baseline_opt: OptSearchPoint,
    variant_library: dict,
    diagnostic_queries: list,
    prompt_result_index: dict[str, dict[str, dict]],
    pipeline_params: dict | None = None,
    min_queries: int = DEFAULT_DIAGNOSTIC_QUERIES,
    axis_requirements: dict[str, int] | None = None,
    pipeline_schema: PipelineSchema | None = None,
) -> dict:
    """Check whether the historical index already covers the OAT scan needs.

    For each prompt-field axis: derive perturbed OptSearchPoints, render them,
    hash, and check the index for cached diagnostic-query results.  Pipeline
    param axes always require fresh backend calls (same rendered prompt,
    different params -> index can't distinguish).

    Args:
        baseline_opt: The search baseline OptSearchPoint.
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
    baseline_rendered = baseline_opt.render()
    baseline_rp_hash = hashlib.sha256(baseline_rendered.encode()).hexdigest()[:HASH_TRUNCATE]
    baseline_cached = prompt_result_index.get(baseline_rp_hash, {})
    baseline_hits = sum(1 for q in diag_query_strings if q in baseline_cached)

    axes_detail: list[dict] = []
    total_calls_saved = 0
    total_calls_needed = 0

    prompt_fields = variant_library.get("prompt_fields", {})
    pipeline_param_defs = variant_library.get("pipeline_params", {})

    # --- Prompt-field axes ---
    for axis_name, values in prompt_fields.items():
        current_val = getattr(baseline_opt, axis_name, "")
        non_baseline = [v for v in values if v != current_val]
        if not non_baseline:
            continue

        variants_detail: list[dict] = []
        usable_count = 0
        axis_saved = 0
        axis_needed = 0

        for value in non_baseline:
            perturbed = baseline_opt.derive_candidate(**{axis_name: value})
            rendered = perturbed.render()
            rp_hash = hashlib.sha256(rendered.encode()).hexdigest()[:HASH_TRUNCATE]
            cached = prompt_result_index.get(rp_hash, {})
            n_cached = sum(1 for q in diag_query_strings if q in cached)
            is_usable = n_cached >= min_queries
            if is_usable:
                usable_count += 1
            axis_saved += n_cached
            axis_needed += max(0, n_diagnostic - n_cached)
            variants_detail.append({
                "value_preview": preview(value),
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

    # --- Pipeline-param axes: discover historical data instead of blanket reject ---
    pp_calls_needed = 0
    base_params = dict(pipeline_params or {})
    target_steps = set(base_params.get("steps", []))
    baseline_rp_hash = hashlib.sha256(
        baseline_opt.render().encode(),
    ).hexdigest()[:HASH_TRUNCATE]
    for axis_name, values in pipeline_param_defs.items():
        current_val = _extract_param(base_params, axis_name, pipeline_schema)
        non_baseline = [v for v in values if v != current_val]
        if not non_baseline:
            continue

        n_calls = len(non_baseline) * n_diagnostic
        # Check historical index for cached data (permissive discovery)
        n_cached_total = 0
        n_step_compatible = 0
        if prompt_result_index:
            cached = prompt_result_index.get(baseline_rp_hash, {})
            for q in diag_query_strings:
                if q in cached:
                    n_cached_total += 1
                    if target_steps and is_result_step_compatible(
                        cached[q], target_steps,
                    ):
                        n_step_compatible += 1

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
            "n_cached_historical": n_cached_total,
            "n_step_compatible": n_step_compatible,
            "note": (
                f"{n_cached_total} historical results found"
                f" ({n_step_compatible} step-compatible)"
                if n_cached_total
                else "No historical data — needs fresh backend calls"
            ),
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

    Discovers plan baselines from stored smart-search plans, then
    for every indexed prompt that belongs to a plan, compares it to **its
    own plan baseline** and tallies which Layer 1 axes were changed.

    Returns a dict with total counts, per-axis breakdown, and unmatched
    prompt counts (prompts in the index that don't belong to any plan).
    """
    # 1. Collect OptSearchPoints + their plan baselines from stored plans ----
    #    rp_hash -> (OptSearchPoint, plan_baseline_opt)
    rp_to_ps: dict[str, tuple[OptSearchPoint, OptSearchPoint]] = {}
    baseline_rp_hashes: set[str] = set()

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
        bl_hash = hashlib.sha256(search_bl.render().encode()).hexdigest()[:HASH_TRUNCATE]
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
