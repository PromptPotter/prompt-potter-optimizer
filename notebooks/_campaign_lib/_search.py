"""Smart search: scan advisor, sensitivity scan, adaptive search, coverage."""

from __future__ import annotations

import logging
import os
from pathlib import Path
from api.models.pipeline_schema import PipelineSchema

from api.config.settings import load_variant_library
from api.services.search import (
    build_diagnostic_set,
    sensitivity_scan as _sensitivity_scan,
    adaptive_search as _adaptive_search,
    select_scan_winner as _select_scan_winner,
    build_prompt_result_index as build_historical_index,
    synthesize_sensitivity_from_grid as synthesize_sensitivity,
    assess_scan_coverage as _assess_scan_coverage,
    build_data_inventory as _build_data_inventory,
    resume_or_build_diagnostic as _resume_or_build_diagnostic,
    advise_scan_config as _advise_scan_config,
    filter_variant_library as _filter_variant_library,
)

from ._display import _fmt_query_result, display_axis_profiles, display_progress
from ._setup import setup_llm

logger = logging.getLogger(__name__)

__all__ = [
    # Smart search
    "build_diagnostic_set", "sensitivity_scan", "adaptive_search",
    "display_axis_profiles", "resume_or_build_diagnostic", "scan_advisor",
    "advisory_to_scan_variants", "resolve_scan_variants",
    "select_scan_winner_notebook", "build_historical_index", "load_task_description",
    "synthesize_sensitivity", "show_scan_coverage", "show_data_inventory",
    "audit_historical_data",
    # Notebook-facing wrappers
    "run_scan_advisor", "seed_campaign_from_scan",
]


# ---------------------------------------------------------------------------
# Smart Prompt Search wrappers
# ---------------------------------------------------------------------------


async def resume_or_build_diagnostic(
    campaign_config: dict,
    baseline,
    baseline_results: list,
    svc: dict,
    eval_data: list,
    scan_variants: dict | None = None,
) -> tuple[str, object, list, list, dict]:
    """Resume or build smart search diagnostic set.

    Prepares LLM client and variant library internally, then delegates to the
    service-layer ``_resume_or_build_diagnostic()``.

    Returns:
        (plan_id, search_baseline, diagnostic, cached_profiles, variant_library)
    """
    # Prepare variant library: base + scan_variants merge
    variant_library = load_variant_library()
    pipeline_params = campaign_config.get("pipeline_params")
    pipeline_schema = svc.get("pipeline_schema")
    if pipeline_params and pipeline_schema:
        variant_library = _filter_variant_library(
            variant_library, pipeline_params, schema=pipeline_schema,
        )
    if scan_variants:
        variant_library["pipeline_params"] = scan_variants

    # Prepare LLM client
    api_key = os.environ.get("GROQ_API_KEY", "")
    llm_client, llm_model = setup_llm(campaign_config, api_key)

    result = await _resume_or_build_diagnostic(
        campaign_config, baseline, baseline_results, llm_client, llm_model,
        svc["store"], svc["backend_id"], eval_data,
        improvement_areas=campaign_config.get("improvement_areas", ""),
        variant_library=variant_library,
    )
    plan_id, search_baseline, diagnostic, _diag_summary, axis_profiles = result

    if axis_profiles:
        print(f"[RESUME] Plan {plan_id}: {len(axis_profiles)} axis profiles available")
    else:
        print(f"  search_baseline: {search_baseline.id[:12]} "
              f"(render: {len(search_baseline.render())} chars)")

    return plan_id, search_baseline, diagnostic, axis_profiles, variant_library


def show_scan_coverage(
    baseline_ps,
    variant_library: dict,
    diagnostic: list,
    prompt_index: dict,
    pipeline_params: dict | None = None,
    min_queries: int = 6,
    axis_requirements: dict[str, int] | None = None,
    pipeline_schema=None,
) -> dict:
    """Print a formatted coverage report and return the raw dict."""
    result = _assess_scan_coverage(
        baseline_ps, variant_library, diagnostic, prompt_index,
        pipeline_params=pipeline_params,
        min_queries=min_queries,
        axis_requirements=axis_requirements,
        pipeline_schema=pipeline_schema,
    )

    bl = result["baseline_coverage"]
    axes = result["axes"]
    summary = result["summary"]

    print("=" * 70)
    print(f"  COVERAGE ADVISOR  (min_queries={result['min_queries']})")
    print("=" * 70)
    print(f"  Baseline: {bl['n_cached']}/{bl['n_needed']} queries cached"
          f" {'[ok]' if bl['sufficient'] else '[!!]'}")
    print()

    pf_axes = [a for a in axes if a["axis_type"] == "prompt_field"]
    pp_axes = [a for a in axes if a["axis_type"] == "pipeline_param"]

    if pf_axes:
        print("  Prompt field axes:")
        for a in pf_axes:
            label = "value" if a["n_values"] == 1 else "values"
            usable_parts = []
            if a.get("variants"):
                n_partial = sum(
                    1 for v in a["variants"]
                    if 0 < v["n_cached"] < min_queries
                )
                usable_parts.append(f"{a['n_usable']} usable")
                if n_partial:
                    usable_parts.append(f"{n_partial} partial")
                n_uncovered = sum(
                    1 for v in a["variants"] if v["n_cached"] == 0
                )
                if n_uncovered:
                    usable_parts.append(f"{n_uncovered} uncovered")
            detail = f"  ({', '.join(usable_parts)})" if usable_parts else ""
            mark = "[ok]" if a["sufficient"] else "[!!]"
            print(f"    {a['axis']:<22s} {a['n_values']} {label:<6s} "
                  f"| {a['n_usable']}/{a['n_required']} required  "
                  f"{mark}{detail}")
        print()

    if pp_axes:
        print("  Pipeline params:")
        n_diag = result["n_diagnostic"]
        for a in pp_axes:
            n_hist = a.get("n_cached_historical", 0)
            n_compat = a.get("n_step_compatible", 0)
            n_calls = a["n_values"] * n_diag
            if n_hist:
                print(f"    {a['axis']:<22s} {a['n_values']} variants "
                      f"| {n_hist} cached ({n_compat} step-compatible)")
            else:
                print(f"    {a['axis']:<22s} {a['n_values']} variants "
                      f"x {n_diag} queries = {n_calls} calls (no historical data)")
        print()

    saved = summary["backend_calls_saved"]
    needed = summary["backend_calls_needed"]
    pf_needed = summary.get("prompt_field_calls_needed", 0)
    pp_needed = summary.get("pipeline_param_calls_needed", 0)
    parts = []
    if pf_needed:
        parts.append(f"{pf_needed} prompt-field")
    if pp_needed:
        parts.append(f"{pp_needed} pipeline-param")
    breakdown = f" ({' + '.join(parts)})" if parts else ""
    print(f"  Summary: {saved} cached, {needed} still needed{breakdown}")
    print(f"  >> {result['recommendation']}")

    if (summary["backend_calls_saved"] == 0
            and sum(len(v) for v in prompt_index.values()) > 0):
        n_total = sum(len(v) for v in prompt_index.values())
        print(f"\n  Note: {n_total} results exist in the index under other baselines.")
        print("  The current search_baseline was rebuilt and has no cached data yet.")

    print("=" * 70)

    return result


def show_data_inventory(
    prompt_index: dict,
    store,
    backend_id: str,
) -> dict:
    """Print a formatted data inventory table and return the raw dict."""
    inv = _build_data_inventory(prompt_index, store, backend_id)

    tp = inv["total_prompts"]
    tr = inv["total_results"]
    print("=" * 70)
    print(f"  DATA INVENTORY  ({tp} prompts, {tr} query results)")
    print("=" * 70)

    bp = inv["baseline_prompts"]
    bq = inv["baseline_queries"]
    print(f"  Baselines: {bp} plan baseline(s) -- {bq} queries cached")
    print()

    axes = inv["axes"]
    if axes:
        print(f"  {'Axis':<22s} {'Prompts':>8s} {'Queries':>8s} {'Distinct values':>16s}")
        for axis_name, info in axes.items():
            print(f"  {axis_name:<22s} {info['n_prompts']:>8d} "
                  f"{info['n_queries']:>8d} {info['distinct_values']:>16d}")
        print()
    else:
        print("  No axis variations found in stored plans.")
        print()

    pp = inv.get("pipeline_params", {})
    if pp:
        print("  Pipeline parameters (from sensitivity scans):")
        for pname, info in pp.items():
            card = info["cardinality"]
            sens = info["sensitivity_range"]
            budget = info["exploration_budget"]
            print(f"    {pname:<24s} {card} values scanned  "
                  f"sensitivity: {sens:.3f}  [{budget}]")
        print()
    else:
        print("  Pipeline parameters: not yet scanned")
        print()

    mp = inv["matched_prompts"]
    mr = inv["matched_results"]
    up = inv["unmatched_prompts"]
    ur = inv["unmatched_results"]
    print(f"  Identified: {mp}/{tp} prompts ({mr}/{tr} queries) via stored plans")
    print(f"  Unmatched:  {up} prompts ({ur} queries)")
    print("=" * 70)

    return inv


def audit_historical_data(
    store,
    backend_id: str,
    diagnostic: list,
    cached_profiles: list,
) -> tuple[dict, list]:
    """Build historical index, try sensitivity synthesis, and display inventory.

    Combines the historical-data audit and data-inventory display into one call.

    Returns:
        (prompt_index, cached_profiles) -- profiles may be updated from synthesis.
    """
    prompt_index = build_historical_index(store, backend_id)

    # Try to synthesize sensitivity from grid data
    if not cached_profiles:
        synth = synthesize_sensitivity(
            store, backend_id, prompt_index, diagnostic,
        )
        if synth:
            _scan_df, axis_profiles = synth
            cached_profiles = axis_profiles
            print("Sensitivity derived from grid data -- scan may be skippable.")

    # Display data inventory
    inv = _build_data_inventory(prompt_index, store, backend_id)

    tp = inv["total_prompts"]
    tr = inv["total_results"]
    print("=" * 70)
    print(f"  DATA INVENTORY  ({tp} prompts, {tr} query results)")
    print("=" * 70)

    bp = inv["baseline_prompts"]
    bq = inv["baseline_queries"]
    print(f"  Baselines: {bp} plan baseline(s) -- {bq} queries cached")
    print()

    axes = inv["axes"]
    if axes:
        print(f"  {'Axis':<22s} {'Prompts':>8s} {'Queries':>8s} {'Distinct values':>16s}")
        for axis_name, info in axes.items():
            print(f"  {axis_name:<22s} {info['n_prompts']:>8d} "
                  f"{info['n_queries']:>8d} {info['distinct_values']:>16d}")
        print()
    else:
        print("  No axis variations found in stored plans.")
        print()

    pp = inv.get("pipeline_params", {})
    if pp:
        print("  Pipeline parameters (from sensitivity scans):")
        for pname, info in pp.items():
            card = info["cardinality"]
            sens = info["sensitivity_range"]
            budget = info["exploration_budget"]
            print(f"    {pname:<24s} {card} values scanned  "
                  f"sensitivity: {sens:.3f}  [{budget}]")
        print()
    else:
        print("  Pipeline parameters: not yet scanned")
        print()

    mp = inv["matched_prompts"]
    mr = inv["matched_results"]
    up = inv["unmatched_prompts"]
    ur = inv["unmatched_results"]
    print(f"  Identified: {mp}/{tp} prompts ({mr}/{tr} queries) via stored plans")
    print(f"  Unmatched:  {up} prompts ({ur} queries)")
    print("=" * 70)

    return prompt_index, cached_profiles


def load_task_description(path: str | None) -> str:
    """Load task description from a file path.

    Returns the file content, or empty string if path is None/empty or
    the file doesn't exist.
    """
    if not path:
        return ""
    p = Path(path)
    if not p.exists():
        print(f"Warning: {path} not found")
        return ""
    text = p.read_text(encoding="utf-8")
    print(f"Loaded task description: {len(text)} chars from {p.name}")
    return text


def _display_scan_advisory(advisory: dict) -> None:
    """Print the scan advisor results (priority axes, budget, warnings)."""
    print(f"{'-' * 70}")
    print("PRIORITY AXES (ranked by importance)")
    print(f"{'-' * 70}")
    for i, ax in enumerate(advisory.get("priority_axes", []), 1):
        imp = ax.get("importance", "?").upper()
        src = ax.get("source", "?")
        label = f"[{imp}] {ax.get('axis', '?')} ({src})"
        if ax.get("step"):
            label += f" -- step: {ax['step']}"
        print(f"  {i}. {label}")
        print(f"     {ax.get('rationale', '')}")
        if ax.get("suggested_values"):
            print(f"     Values: {ax['suggested_values']}")

    skipped = advisory.get("axes_to_skip", [])
    if skipped:
        print(f"\n{'-' * 70}")
        print("AXES TO SKIP")
        print(f"{'-' * 70}")
        for ax in skipped:
            print(f"  - {ax.get('axis', '?')}: {ax.get('reason', '?')}")

    budget = advisory.get("budget_breakdown", {})
    if budget:
        print(f"\n{'-' * 70}")
        print("BUDGET BREAKDOWN")
        print(f"{'-' * 70}")
        for k, v in budget.items():
            print(f"  {k}: {v}")

    n_diag = advisory.get("suggested_n_diagnostic", 6)
    print(f"\n  Suggested n_diagnostic: {n_diag}")

    reasoning = advisory.get("reasoning", "")
    if reasoning:
        print(f"\n{'-' * 70}")
        print("REASONING")
        print(f"{'-' * 70}")
        print(f"  {reasoning}")

    warnings = advisory.get("validation_warnings", [])
    if warnings:
        print(f"\n{'-' * 70}")
        print("VALIDATION WARNINGS")
        print(f"{'-' * 70}")
        for w in warnings:
            print(f"  [!] {w}")

    print("=" * 70)


async def scan_advisor(
    campaign_config: dict,
    svc: dict,
    baseline_results: list,
    eval_data: list,
    *,
    query_budget: int = 120,
    coverage_stats: dict | None = None,
    task_description: str = "",
) -> dict:
    """LLM-powered scan configuration advice.

    Accepts high-level ``campaign_config`` + ``svc`` (same shape as
    ``resume_or_build_diagnostic``) and internalizes pipeline_schema
    resolution, variant library loading/filtering, and LLM setup.

    Returns:
        Advisory dict with priority_axes, suggested_n_diagnostic,
        axes_to_skip, budget_breakdown, and reasoning.
    """
    from api.services.pipeline_discovery import TERMNORM_DEFAULT_SCHEMA

    # --- Internalized prep (matches resume_or_build_diagnostic pattern) ---
    pipeline_schema = svc.get("pipeline_schema") or TERMNORM_DEFAULT_SCHEMA
    pipeline_params = campaign_config.get("pipeline_params")
    user_excluded = campaign_config.get("exclude_steps", [])

    variant_library = load_variant_library()
    if pipeline_params and pipeline_schema:
        variant_library = _filter_variant_library(
            variant_library, pipeline_params, schema=pipeline_schema,
        )

    api_key = os.environ.get("GROQ_API_KEY", "")
    llm_client, model = setup_llm(campaign_config, api_key)

    eval_data_size = len(eval_data)

    # --- Display ---
    print("=" * 70)
    print("SCAN ADVISOR -- pipeline-aware sensitivity setup")
    print("=" * 70)
    print(f"  Pipeline: {pipeline_schema.name} ({pipeline_schema.version})")
    print(f"  Steps: {[s.name for s in pipeline_schema.steps]}")
    if user_excluded:
        print(f"  Excluded: {user_excluded}")
    print(f"  Eval data size: {eval_data_size} queries")
    print(f"  Query budget: {query_budget}")
    if task_description:
        print(f"  Task context: {task_description[:80].strip()}...")
    print(f"  Calling {model or '?'} ...")
    print()

    advisory = await _advise_scan_config(
        pipeline_schema=pipeline_schema,
        variant_library=variant_library,
        baseline_results=baseline_results,
        eval_data_size=eval_data_size,
        llm_client=llm_client,
        model=model,
        query_budget=query_budget,
        coverage_stats=coverage_stats,
        pipeline_params=pipeline_params,
        task_description=task_description,
        exclude_steps=user_excluded or None,
    )

    _display_scan_advisory(advisory)

    return advisory


# ---------------------------------------------------------------------------
# Schema resolution helpers
# ---------------------------------------------------------------------------


def _resolve_schema_axes(
    raw_variants: dict,
    pipeline_schema: PipelineSchema | None,
) -> tuple[dict, dict[str, list[str]]]:
    """Resolve schema mutation tuples in *raw_variants* into concrete JSON Schema dicts.

    Non-schema axes pass through unchanged.  Returns ``(resolved, schema_labels)``.
    """
    from api.models.schema_mutation import (
        baseline_schema_from_step,
        parse_mutation_tuples,
        resolve_schema_variants,
    )

    resolved: dict = {}
    schema_labels: dict[str, list[str]] = {}

    for axis_name, vals in raw_variants.items():
        if (
            axis_name.endswith("_schema")
            and pipeline_schema is not None
            and vals
            and isinstance(vals[0], list)
        ):
            step_name = pipeline_schema.step_for_flat_param(axis_name)
            step = pipeline_schema.get_step(step_name) if step_name else None
            if step and step.output_schema:
                try:
                    variants = [parse_mutation_tuples(sv) for sv in vals]
                    baseline = baseline_schema_from_step(step.output_schema)
                    resolved[axis_name] = resolve_schema_variants(baseline, variants)
                    schema_labels[axis_name] = (
                        ["(baseline)"] + [v.render_label() for v in variants]
                    )
                    continue
                except (ValueError, KeyError) as e:
                    logger.warning(
                        "Schema axis '%s' mutation parse failed, "
                        "falling through to raw values: %s",
                        axis_name, e,
                    )
        resolved[axis_name] = vals

    return resolved, schema_labels


def advisory_to_scan_variants(
    advisory: dict,
    pipeline_schema: PipelineSchema | None = None,
) -> tuple[dict, dict[str, list[str]]]:
    """Convert scan advisory into editable pipeline_params dict.

    Extracts ``priority_axes`` entries whose ``source`` is ``"pipeline_param"``
    and that carry ``suggested_values``.

    For schema axes (name ends with ``_schema``), parses mutation tuples,
    resolves against the baseline JSON Schema, and returns concrete dicts.

    Returns ``(scan_variants, schema_labels)`` where ``schema_labels`` maps
    schema axis names to human-readable labels (``"(baseline)"`` at index 0).
    """
    raw: dict[str, list] = {}
    for ax in advisory.get("priority_axes", []):
        if ax.get("source") != "pipeline_param" or not ax.get("suggested_values"):
            continue
        raw[ax["axis"]] = ax["suggested_values"]

    return _resolve_schema_axes(raw, pipeline_schema)


async def run_scan_advisor(
    campaign_config: dict,
    svc: dict,
    baseline_results: list,
    eval_data: list,
    *,
    query_budget: int = 120,
    task_description: str = "",
    coverage_df=None,
) -> tuple[dict, dict, dict]:
    """Run scan advisor + extract/display proposed variants.

    Builds coverage_stats from *coverage_df* (handles empty gracefully),
    calls scan_advisor(), then advisory_to_scan_variants(), prints summary.
    Returns (advisory, scan_variants, schema_labels).
    """
    cov_stats = None
    if coverage_df is not None and hasattr(coverage_df, "empty") and not coverage_df.empty:
        covered = int(coverage_df["in_candidates"].sum())
        total = len(coverage_df)
        cov_stats = {
            "covered": covered,
            "total": total,
            "coverage_pct": covered / total * 100 if total else 0,
        }

    advisory = await scan_advisor(
        campaign_config, svc, baseline_results, eval_data,
        query_budget=query_budget,
        coverage_stats=cov_stats,
        task_description=task_description,
    )

    proposed, schema_labels = advisory_to_scan_variants(
        advisory, pipeline_schema=svc.get("pipeline_schema"),
    )

    print("\n--- PROPOSED SCAN VARIANTS (edit in next cell) ---")
    for axis, values in proposed.items():
        if axis in schema_labels:
            print(f"  {axis}: (baseline + {len(values) - 1} mutations)")
            for i, label in enumerate(schema_labels[axis]):
                print(f"    [{i}] {label}")
        else:
            print(f"  {axis}: {values}")

    return advisory, proposed if proposed else {}, schema_labels


def resolve_scan_variants(
    scan_variants: dict,
    pipeline_schema: PipelineSchema | None = None,
    *,
    svc: dict | None = None,
) -> tuple[dict, dict[str, list[str]]]:
    """Resolve schema mutation tuples and display the resolved variants.

    Convenience wrapper for notebook use — resolves schema axes via
    ``_resolve_schema_axes`` and prints a summary.

    If *pipeline_schema* is ``None`` and *svc* is provided, falls back to
    ``svc["pipeline_schema"]`` then ``TERMNORM_DEFAULT_SCHEMA``.

    Returns ``(resolved_variants, schema_labels)``.
    """
    if pipeline_schema is None and svc is not None:
        from api.services.pipeline_discovery import TERMNORM_DEFAULT_SCHEMA
        pipeline_schema = svc.get("pipeline_schema") or TERMNORM_DEFAULT_SCHEMA

    resolved, schema_labels = _resolve_schema_axes(scan_variants, pipeline_schema)

    for axis, vals in resolved.items():
        if axis in schema_labels:
            print(f"  {axis}: (baseline + {len(vals) - 1} mutations)")
            for i, label in enumerate(schema_labels[axis]):
                print(f"    [{i}] {label}")
        else:
            print(f"  {axis}: {vals}")

    return resolved, schema_labels


# ---------------------------------------------------------------------------
# Scan resume + execution
# ---------------------------------------------------------------------------


def _load_partial_scan(store, backend_id: str, plan_id: str) -> dict | None:
    """Load partial scan checkpoint if scan was interrupted."""
    plan_data = store.smart_search.load(backend_id, plan_id)
    status = plan_data.get("status", "") if plan_data else ""
    if status == "scan_partial":
        sr = plan_data.get("scan_results", {})
        return {
            "rows": sr.get("rows", []),
            "completed_axes": sr.get("completed_axes", []),
        }
    return None


async def sensitivity_scan(
    baseline_ps,
    variant_library: dict,
    eval_data: list,
    backend_client,
    user_focus: str = "",
    store=None,
    backend_id: str = "",
    pipeline_params: dict | None = None,
    session_terms: list | None = None,
    plan_id: str = "",
    prompt_result_index: dict | None = None,
    partial_scan: dict | None = None,
    pipeline_schema=None,
) -> tuple:
    """Run a sensitivity scan over all axes with progress output.

    Returns (per_variant_df, axis_profiles).
    """
    # Auto-detect partial checkpoint when no explicit partial_scan provided
    if partial_scan is None and store and backend_id and plan_id:
        partial_scan = _load_partial_scan(store, backend_id, plan_id)
        if partial_scan:
            print(
                f"[RESUME] Found partial scan: "
                f"{len(partial_scan['completed_axes'])} axes done"
            )

    if partial_scan:
        n_done = len(partial_scan.get("completed_axes", []))
        print(f"Resuming sensitivity scan ({n_done} axes already done)...")
    else:
        print("Running sensitivity scan...")
    if user_focus:
        print(f"  User focus: {user_focus}")

    print("\n  Baseline field values:")
    for field in ("persona", "task_intent", "problem_description",
                  "instruction", "thinking_style", "answer_format"):
        val = getattr(baseline_ps, field, "")
        if val:
            print(f"    {field}: {val[:80]}{'...' if len(val) > 80 else ''}")
        else:
            print(f"    {field}: (empty)")
    print()

    n_configs = sum(
        len(v)
        for v in variant_library.get("prompt_fields", {}).values()
        if len(v) > 1
    ) + sum(
        len(v)
        for v in variant_library.get("pipeline_params", {}).values()
        if len(v) > 1
    )
    print(f"  Estimated configs: ~{n_configs} x {len(eval_data)} queries")

    cb, on_result_cb = _make_scan_progress_cb()

    _httpx_log = logging.getLogger("httpx")
    _httpcore_log = logging.getLogger("httpcore")
    _prev_httpx = _httpx_log.level
    _prev_httpcore = _httpcore_log.level
    _httpx_log.setLevel(logging.WARNING)
    _httpcore_log.setLevel(logging.WARNING)

    # Rebuild prompt_result_index from current disk state to avoid ghost cache hits
    if store and backend_id:
        from api.services.search.coverage import build_prompt_result_index
        prompt_result_index = build_prompt_result_index(store, backend_id)

    try:
        print("  Evaluating baseline...")
        df, profiles = await _sensitivity_scan(
            baseline_ps, variant_library, eval_data, backend_client,
            user_focus=user_focus,
            store=store, backend_id=backend_id,
            pipeline_params=pipeline_params,
            session_terms=session_terms,
            progress_cb=cb,
            prompt_result_index=prompt_result_index,
            plan_id=plan_id,
            partial_scan=partial_scan,
            pipeline_schema=pipeline_schema,
            on_result=on_result_cb,
        )
    finally:
        _httpx_log.setLevel(_prev_httpx)
        _httpcore_log.setLevel(_prev_httpcore)

    print(f"\nSensitivity scan complete: {len(df)} variants evaluated")
    display_axis_profiles(profiles)

    return df, profiles


def _make_scan_progress_cb():
    """Build a progress callback for sensitivity_scan with flip tracking.

    Returns:
        Tuple of (progress_cb, on_result_cb).
    """
    baseline_results: list = []

    def _on_result(result: dict, index: int, total: int) -> None:
        """Print each query result as it arrives from the backend."""
        is_cached = result.get("cached", False)
        print(_fmt_query_result(result, cached=is_cached), flush=True)

    def _cb(event: dict) -> None:
        t = event["type"]

        if t == "baseline_done":
            baseline_results.clear()
            baseline_results.extend(event.get("results", []))
            is_cached = event.get("cached", False)
            cached = " [cached]" if is_cached else ""
            print(f"  Baseline: {event['hits']}/{event['total']} "
                  f"({event['accuracy']:.1%}){cached}")
            # Per-query lines already printed by _on_result in real-time

        elif t == "axis_start":
            ai = event["axis_index"] + 1
            total = event["total_axes"]
            card = event["cardinality"]
            print(f"\n{'=' * 70}")
            print(f"  Axis {ai}/{total}: {event['axis']} "
                  f"({event['axis_type']}, {card} values)")
            print(f"{'=' * 70}")

        elif t == "variant_done":
            vi = event["value_idx"]
            preview = event["value_preview"]
            hits = event["hits"]
            total = event["total"]
            acc = event["accuracy"]
            delta = event["delta"]
            is_bl = event["is_baseline_value"]
            cached = event.get("cached", False)

            if is_bl:
                delta_str = "(baseline)"
                marker = ""
            elif delta > 0:
                delta_str = f"+{delta:.1%}"
                marker = " ^"
            elif delta < 0:
                delta_str = f"{delta:.1%}"
                marker = " v"
            else:
                delta_str = "+0.0%"
                marker = ""

            cache_str = " [cached]" if cached else ""
            print(f"  [{vi}] {preview:<42s} {hits}/{total}  "
                  f"{acc:.1%}  {delta_str}{marker}{cache_str}")
            # Per-query lines already printed by _on_result in real-time

        elif t == "axis_done":
            budget = event["exploration_budget"]
            sr = event["sensitivity_range"]
            bd = event["best_delta"]
            wd = event["worst_delta"]
            print(f"  >> {event['axis']}: range={sr:.1%}, "
                  f"best={bd:+.1%}, worst={wd:+.1%}, budget={budget}")

    return _cb, _on_result


async def adaptive_search(
    baseline_ps,
    variant_library: dict,
    eval_data: list,
    backend_client,
    axis_profiles: list[dict],
    max_rounds: int = 3,
    stop_threshold: float = 0.0,
    store=None,
    backend_id: str = "",
    pipeline_params: dict | None = None,
    session_terms: list | None = None,
    plan_id: str = "",
    prompt_result_index: dict | None = None,
) -> tuple:
    """Run adaptive coordinate descent search with progress output.

    Returns (best_ps, best_pipeline_params, search_log_df).
    """
    active = [p for p in axis_profiles if p["exploration_budget"] != "skip"]
    print(f"Adaptive search: {len(active)} active axes, max {max_rounds} rounds")
    for p in active:
        print(
            f"  {p['axis']} ({p['axis_type']}): "
            f"card={p['cardinality']}, budget={p['exploration_budget']}"
        )

    cb = _make_search_progress_cb()

    _httpx_log = logging.getLogger("httpx")
    _httpcore_log = logging.getLogger("httpcore")
    _prev_httpx = _httpx_log.level
    _prev_httpcore = _httpcore_log.level
    _httpx_log.setLevel(logging.WARNING)
    _httpcore_log.setLevel(logging.WARNING)
    try:
        best_ps, best_params, log_df = await _adaptive_search(
            baseline_ps, variant_library, eval_data, backend_client,
            axis_profiles,
            max_rounds=max_rounds,
            stop_threshold=stop_threshold,
            store=store, backend_id=backend_id,
            pipeline_params=pipeline_params,
            session_terms=session_terms,
            progress_cb=cb,
            prompt_result_index=prompt_result_index,
            plan_id=plan_id,
        )
    finally:
        _httpx_log.setLevel(_prev_httpx)
        _httpcore_log.setLevel(_prev_httpcore)

    if not log_df.empty:
        print(f"\nSearch log: {len(log_df)} evaluations across "
              f"{log_df['round'].nunique()} rounds")
        best_row = log_df.loc[log_df["accuracy"].idxmax()]
        print(
            f"  Best found: {best_row['axis']}={best_row['value_preview']} "
            f"({best_row['accuracy']:.1%})"
        )
    else:
        print("\nNo evaluations performed (all axes skipped).")

    return best_ps, best_params, log_df


def _make_search_progress_cb():
    """Build a progress callback for adaptive_search."""

    def _cb(event: dict) -> None:
        t = event["type"]

        if t == "round_start":
            r = event["round"]
            max_r = event["max_rounds"]
            acc = event["current_accuracy"]
            axes = event["active_axes"]
            print(f"\n{'=' * 70}")
            print(f"  Round {r}/{max_r} | current accuracy: {acc:.1%}")
            print(f"  Active axes: {', '.join(axes)}")
            print(f"{'=' * 70}")

        elif t == "axis_start":
            axis = event["axis"]
            card = event["cardinality"]
            budget = event["budget"]
            print(f"\n  -- {axis} ({event['axis_type']}, "
                  f"{card} values, budget={budget}) --")

        elif t == "variant_done":
            preview = event["value_preview"]
            hits = event["hits"]
            total = event["total"]
            acc = event["accuracy"]
            delta = event["delta"]
            cached = event.get("cached", False)

            if delta > 0:
                delta_str = f"+{delta:.1%}"
                marker = " ^"
            elif delta < 0:
                delta_str = f"{delta:.1%}"
                marker = " v"
            else:
                delta_str = "+0.0%"
                marker = ""

            cache_str = " [cached]" if cached else ""
            print(f"    {preview:<42s} {hits}/{total}  "
                  f"{acc:.1%}  {delta_str}{marker}{cache_str}")

            results = event.get("results", [])
            for r in results:
                print(_fmt_query_result(r, cached=cached))

        elif t == "axis_resolved":
            action = event["action"]
            axis = event["axis"]
            if action == "improved":
                imp = event["improvement"]
                bv = event["best_value"]
                new_acc = event["new_accuracy"]
                print(f"  ** {axis} IMPROVED +{imp:.1%} -> "
                      f"{new_acc:.1%} (best: {bv})")
            else:
                print(f"  -- {axis}: no improvement, resolved")

        elif t == "round_done":
            r = event["round"]
            acc = event["accuracy"]
            if event["improved"]:
                print(f"\n  Round {r} done: accuracy now {acc:.1%}")
            else:
                print(f"\n  Round {r}: no improvement, stopping.")

    return _cb


def select_scan_winner_notebook(
    scan_df,
    axis_profiles: list[dict],
    search_baseline,
    variant_library: dict,
    pipeline_params: dict | None = None,
    store=None,
    backend_id: str = "",
    plan_id: str = "",
) -> tuple:
    """Pick best from scan and print summary. No backend needed.

    If *scan_df* is ``None`` (resumed from cache), loads scan rows from the
    plan store automatically.

    Returns:
        (best_ps, best_params).
    """
    # Load scan rows from plan if scan_df not in memory
    if scan_df is None and store and backend_id and plan_id:
        from api.services.search.smart_search import load_scan_results_from_plan
        scan_df = load_scan_results_from_plan(store, backend_id, plan_id)

    if scan_df is None or (hasattr(scan_df, "empty") and scan_df.empty):
        print("No scan data available. Run sensitivity scan (4.5b) first.")
        return search_baseline, dict(pipeline_params or {})

    best_ps, best_params = _select_scan_winner(
        scan_df, axis_profiles, search_baseline, variant_library,
        pipeline_params=pipeline_params,
    )

    # Print summary
    improving = [
        p for p in axis_profiles
        if p["best_delta"] > 0 and p["exploration_budget"] != "skip"
    ]
    if improving:
        print(f"Selected best from {len(improving)} improving axes:")
        for p in improving:
            axis_rows = scan_df[scan_df["axis"] == p["axis"]]
            if not axis_rows.empty:
                best_row = axis_rows.loc[axis_rows["accuracy"].idxmax()]
                print(
                    f"  {p['axis']:<25s} best_delta=+{p['best_delta']:.1%}  "
                    f"value_idx={int(best_row['value_idx'])}  "
                    f"acc={best_row['accuracy']:.1%}"
                )
    else:
        print("No axes improved over baseline -- using baseline as-is.")

    if best_ps.id != search_baseline.id:
        print(f"\nComposed PromptState: {best_ps.id[:12]} "
              f"(parent: {best_ps.parent_id[:12] if best_ps.parent_id else 'root'})")
    if best_params != dict(pipeline_params or {}):
        print(f"Pipeline params updated: {best_params}")

    return best_ps, best_params


def seed_campaign_from_scan(
    scan_df,
    axis_profiles: list,
    search_baseline,
    variant_library: dict,
    campaign_rounds: list,
    campaign_config: dict,
    *,
    store=None,
    backend_id: str = "",
    plan_id: str = "",
) -> tuple:
    """Select scan winner, update pipeline_params, seed campaign_rounds.

    Calls select_scan_winner_notebook(), updates campaign_config["pipeline_params"],
    appends round entry, displays progress.
    Returns (best_ps, best_params).
    """
    best_ps, best_params = select_scan_winner_notebook(
        scan_df, axis_profiles, search_baseline, variant_library,
        pipeline_params=campaign_config.get("pipeline_params"),
        store=store, backend_id=backend_id, plan_id=plan_id,
    )

    if best_params:
        campaign_config["pipeline_params"] = best_params
        print(f"Updated pipeline_params: {best_params}")

    bl = campaign_rounds[0] if campaign_rounds else {}
    campaign_rounds.append({
        "round": "search",
        "label": f"smart_search ({best_ps.changes_description or best_ps.id[:12]})",
        "prompt_state": best_ps,
        "accuracy": bl.get("accuracy", 0.0),
        "hits": bl.get("hits", 0),
        "total": bl.get("total", 0),
        "results": bl.get("results", []),
    })
    display_progress(campaign_rounds)

    return best_ps, best_params
