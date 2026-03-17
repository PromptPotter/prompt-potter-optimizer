"""Coverage advisor, data inventory, and prompt result index.

assess_scan_coverage checks whether historical data covers OAT scan needs.
build_data_inventory summarizes what the historical index contains.
build_prompt_result_index builds the cross-run query lookup.
diagnose_scan_variants checks scan variant coverage via prompt alias groups.
"""

import hashlib
import json
import logging
import statistics
from collections import defaultdict
from api.models.pipeline_schema import PipelineSchema, is_result_step_compatible
from api.models.prompt_state import PromptState
from api.services.project_store import ProjectStore
from api.services.search.plan_persistence import (
    deserialize_grid_plan,
    deserialize_smart_search_plan,
)
from api.services.search.smart_search import DEFAULT_DIAGNOSTIC_QUERIES
from api.services.search.utils import preview as _preview

logger = logging.getLogger(__name__)


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
    rank_distribution = {}
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


def summarize_historical_data(
    store: ProjectStore,
    backend_id: str,
    baseline_rp_hash: str = "",
) -> dict:
    """Lightweight historical data summary from the index file only.

    Reads ``store.dataset_runs.list_all()`` (single JSON file) — does NOT
    load individual detail files.

    Returns dict with ``n_runs``, ``n_results``, ``n_unique_prompts``,
    ``n_baseline_matches``, ``pipeline_configs``, and ``n_legacy``.
    """
    summaries = store.dataset_runs.list_all(backend_id)
    if not summaries:
        return {
            "n_runs": 0, "n_results": 0, "n_unique_prompts": 0,
            "n_baseline_matches": 0, "pipeline_configs": [], "n_legacy": 0,
        }

    n_results = 0
    n_baseline = 0
    n_legacy = 0
    prompt_hashes: set[str] = set()
    # pipeline_params key -> {runs, results}
    pp_groups: dict[str, dict] = defaultdict(lambda: {"runs": 0, "results": 0})

    for entry in summaries:
        item_count = entry.get("item_count", 0)
        n_results += item_count

        rp_hash = entry.get("rendered_prompt_hash", "")
        pp = entry.get("pipeline_params")

        if not rp_hash:
            n_legacy += 1
        else:
            prompt_hashes.add(rp_hash)
            if baseline_rp_hash and rp_hash == baseline_rp_hash:
                n_baseline += 1

        # Group by pipeline config
        if pp and isinstance(pp, dict):
            steps = pp.get("steps", [])
            key = ", ".join(sorted(steps)) if steps else "(custom params)"
        elif rp_hash:
            key = "(default)"
        else:
            key = "(default / legacy)"
        pp_groups[key]["runs"] += 1
        pp_groups[key]["results"] += item_count

    pipeline_configs = [
        {"label": k, "runs": v["runs"], "results": v["results"]}
        for k, v in sorted(pp_groups.items(), key=lambda x: -x[1]["results"])
    ]

    return {
        "n_runs": len(summaries),
        "n_results": n_results,
        "n_unique_prompts": len(prompt_hashes),
        "n_baseline_matches": n_baseline,
        "pipeline_configs": pipeline_configs,
        "n_legacy": n_legacy,
    }


def diagnose_scan_variants(
    store: ProjectStore,
    backend_id: str,
    scan_variants: dict[str, list],
    baseline_rp_hashes: set[str],
    prompt_result_index: dict[str, dict[str, dict]] | None = None,
) -> dict:
    """Check scan variant coverage against historical runs via alias groups.

    ``baseline_rp_hashes`` is the expanded alias set (original + restructured).
    Filters index to runs whose ``rendered_prompt_hash`` is in the alias set,
    then counts per-axis coverage for each scan variant value.

    Returns dict with ``n_matching_runs``, ``n_matching_results``,
    ``alias_hashes``, and per-axis ``axes`` detail.
    """
    summaries = store.dataset_runs.list_all(backend_id)

    # Filter to runs matching the alias group
    matching = [
        e for e in summaries
        if e.get("rendered_prompt_hash", "") in baseline_rp_hashes
    ]

    # Compute cached results from prompt_result_index
    n_cached_results = 0
    if prompt_result_index:
        for h in baseline_rp_hashes:
            n_cached_results += len(prompt_result_index.get(h, {}))

    # Per-axis coverage
    axes: dict[str, dict] = {}
    for axis_name, values in scan_variants.items():
        value_counts: dict[str, int] = {}
        other_values: set[str] = set()
        # Canonical keys for matching (JSON for dicts, str for scalars)
        target_keys = {}
        for v in values:
            key = json.dumps(v, sort_keys=True) if isinstance(v, dict) else str(v)
            target_keys[key] = v
            value_counts[key] = 0

        # Track best scores per variant value for scan cache
        value_scores: dict[str, dict] = {}

        for entry in matching:
            pp = entry.get("pipeline_params") or {}
            hist_val = pp.get(axis_name)
            if hist_val is None:
                continue
            hist_key = (
                json.dumps(hist_val, sort_keys=True)
                if isinstance(hist_val, dict) else str(hist_val)
            )
            if hist_key in value_counts:
                value_counts[hist_key] += 1
                # Keep the first matching run's scores (most recent by list order)
                if hist_key not in value_scores:
                    scores = entry.get("scores", {})
                    if scores:
                        value_scores[hist_key] = scores
            else:
                other_values.add(hist_key)

        axes[axis_name] = {
            "values": value_counts,
            "value_scores": value_scores,
            "other_count": len(other_values),
            "total_matching": sum(value_counts.values()),
        }

    return {
        "n_total_runs": len(summaries),
        "n_matching_runs": len(matching),
        "n_matching_results": n_cached_results,
        "alias_hashes": sorted(baseline_rp_hashes),
        "axes": axes,
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
    pipeline_schema: PipelineSchema | None = None,
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

    # --- Pipeline-param axes: discover historical data instead of blanket reject ---
    pp_calls_needed = 0
    base_params = dict(pipeline_params or {})
    target_steps = set(base_params.get("steps", []))
    baseline_rp_hash = hashlib.sha256(
        baseline_ps.render().encode(),
    ).hexdigest()[:16]
    for axis_name, values in pipeline_param_defs.items():
        current_val = base_params.get(axis_name)
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
