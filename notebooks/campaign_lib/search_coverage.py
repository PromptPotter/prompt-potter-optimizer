"""Historical diagnostics and coverage display."""

from __future__ import annotations

import logging

from api.services.search import (
    assess_scan_coverage as _assess_scan_coverage,
)
from api.services.search import (
    build_data_inventory as _build_data_inventory,
)
from api.services.search import (
    build_prompt_result_index as build_historical_index,
)

from .display import BOX_WIDTH, CYAN, DIM, RESET

logger = logging.getLogger(__name__)

__all__ = [
    "audit_historical_data",
    "show_data_inventory",
    "show_scan_coverage",
]

_BLOCK_WIDTH = BOX_WIDTH  # single source of truth
_FOOTNOTE_LEN = 50  # values longer than this get a footnote code


def _print_historical_diagnostic(
    prompt_index: dict[str, dict[str, dict]],
    *,
    scan_diagnosis: dict | None = None,
) -> None:
    """Print historical data diagnostic with sp_hash scan coverage."""
    n_prompts = len(prompt_index)
    n_results = sum(len(v) for v in prompt_index.values())
    if n_results == 0:
        print(f"{DIM}Historical data: (none){RESET}")
        return

    print(
        f"{CYAN}Historical data:{RESET}"
        f" {n_results} results across {n_prompts} unique prompts"
    )

    if scan_diagnosis:
        n_matching = scan_diagnosis["n_matching_runs"]
        n_cached = scan_diagnosis["n_matching_results"]
        print(f"Matching runs (step sequence): {n_matching},"
              f" {n_cached} cached results")

    # Scan variant coverage
    if not (scan_diagnosis and scan_diagnosis["axes"]):
        return

    n_matching = scan_diagnosis["n_matching_runs"]
    print(f"{CYAN}Scan variant coverage"
          f" ({n_matching} matching runs):{RESET}")

    # Footnote lookup for very long values
    legend: list[tuple[str, str]] = []
    code_idx = 0
    codes: dict[str, str] = {}

    def _code(val: str) -> str:
        nonlocal code_idx
        if val in codes:
            return codes[val]
        c = chr(ord("a") + code_idx)
        code_idx += 1
        codes[val] = f"[{c}]"
        legend.append((f"[{c}]", val))
        return f"[{c}]"

    indent = "  "
    gap = "  "

    for axis_name, info in scan_diagnosis["axes"].items():
        values = info["values"]
        n_covered = sum(1 for c in values.values() if c > 0)
        n_total = len(values)
        label = f"{DIM}{axis_name:<24s}{RESET}"

        if n_covered == 0:
            print(f"{label} {DIM}(not tested){RESET}")
            continue

        # Build chunks: "value→N ✓" or "value→✗"
        chunks: list[str] = []
        for key, count in values.items():
            skey = str(key)
            display = _code(skey) if len(skey) > _FOOTNOTE_LEN else skey
            if count > 0:
                chunks.append(f"{display}\u2192{count} \u2713")
            else:
                chunks.append(
                    f"{display}\u2192{DIM}\u2717{RESET}"
                )

        # Try single-line after axis label
        joined = gap.join(chunks)
        # 24 = raw label width (without ANSI)
        if 24 + 1 + len(joined) <= _BLOCK_WIDTH:
            print(f"{label} {joined}")
            continue

        # Multi-line: header + block-filled rows
        print(f"{label} {n_covered}/{n_total} values tested")
        line = indent
        for chunk in chunks:
            addition = chunk if line == indent else gap + chunk
            if len(line) + len(addition) > _BLOCK_WIDTH:
                if line != indent:
                    print(line)
                line = indent + chunk
            else:
                line += addition
        if line != indent:
            print(line)

    if legend:
        print(f"{CYAN}Values:{RESET}")
        for code, full in legend:
            print(f"  {code} {full}")


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
) -> dict:
    """Build historical index and display data inventory.

    Returns:
        prompt_index -- the historical prompt result index.
    """
    prompt_index = build_historical_index(store, backend_id)

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

    return prompt_index
