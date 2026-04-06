"""Search display wrappers — baseline, variants, advisor, coverage, results."""

from __future__ import annotations

import contextlib
import json
import logging
from pathlib import Path
from typing import TYPE_CHECKING

from promptpotter.config.settings import load_variant_library_rich
from promptpotter.models.pipeline_schema import PipelineSchema
from promptpotter.services.search import (
    advise_scan_config as _advise_scan_config,
)
from promptpotter.services.search import (
    assess_scan_coverage as _assess_scan_coverage,
)
from promptpotter.services.search import (
    build_data_inventory as _build_data_inventory,
)
from promptpotter.services.search import (
    build_prompt_result_index as build_historical_index,
)
from promptpotter.services.search import (
    load_filtered_variant_library as _load_filtered_variants,
)
from promptpotter.services.search import (
    preview_advisor_prompt as _preview_advisor_prompt,
)
from promptpotter.services.search import (
    resume_or_build_diagnostic as _resume_or_build_diagnostic,
)
from promptpotter.services.search import (
    select_scan_winner as _select_scan_winner,
)
from promptpotter.services.search.scan_advisor import (
    advisory_to_scan_variants,
    resolve_schema_axes,
)
from promptpotter.services.search.scan_results import (
    decompose_scan_baseline as _decompose_scan_baseline,
)
from promptpotter.services.search.scan_results import (
    seed_campaign_from_scan as _seed_campaign_from_scan,
)
from promptpotter.shared.constants import LAYER1_STRING_FIELDS

from .display import (
    BOX_WIDTH,
    CYAN,
    DIM,
    RESET,
    format_pipeline_overrides,
    show_progress,
    show_scan_leaderboard,
    show_scan_query_difficulty,
)
from .setup import setup_llm

if TYPE_CHECKING:
    from promptpotter.services.campaign.bootstrap import BackendContext
    from promptpotter.services.campaign.config import CampaignConfig

logger = logging.getLogger(__name__)

__all__ = [
    # search_variants
    "advisory_to_scan_variants",
    # search_coverage
    "audit_historical_data",
    # search_baseline
    "decompose_scan_baseline",
    # search_advisor
    "load_task_description",
    "preview_advisor_prompt",
    "resolve_scan_variants",
    # search_results
    "resume_or_build_diagnostic",
    "run_scan_advisor",
    "scan_advisor",
    "seed_campaign_from_scan",
    "select_scan_winner_notebook",
    "show_data_inventory",
    "show_scan_analytics",
    "show_scan_coverage",
    "show_variant_library",
]


# ===========================================================================
# Scan baseline preparation
# ===========================================================================


async def decompose_scan_baseline(
    baseline,
    campaign_config: CampaignConfig,
    pipeline_params: dict | None = None,
    *,
    store=None,
    backend_id: str = "",
    scan_variants: dict | None = None,
    force_restructure: bool = False,
    session: BackendContext | None = None,
):
    """Restructure baseline instruction into PromptPotter's internal fields.

    Delegates to ``promptpotter.services.search.scan_baseline.decompose_scan_baseline()``
    and prints restructured fields + historical diagnostic.

    Returns:
        (baseline_jsp, search_baseline, scan_diag)
    """
    from .setup import configure_pipeline

    # session shorthand: extract store/backend_id if provided
    if session is not None:
        store = store or session.store
        backend_id = backend_id or session.backend_id

    # Fresh pipeline defaults when not explicitly provided
    if pipeline_params is None and session is not None:
        pipeline_params = configure_pipeline(session, campaign_config)

    llm_client, llm_model = setup_llm(campaign_config)

    # Resolve prompt node from pipeline schema — only if the node is active
    prompt_node = ""
    if session is not None:
        ps = session.pipeline_schema
        active_steps = set((pipeline_params or {}).get("steps", []))
        if ps:
            for name in ps.prompt_node_names():
                if name in active_steps:
                    prompt_node = name
                    break

    result = await _decompose_scan_baseline(
        baseline,
        campaign_config,
        llm_client,
        llm_model,
        pipeline_params=pipeline_params,
        store=store,
        backend_id=backend_id,
        scan_variants=scan_variants,
        force_restructure=force_restructure,
        prompt_node=prompt_node,
        pipeline_schema=ps,
    )

    # Print decomposed fields
    cache_tag = " (cached)" if result.was_cached else ""
    print(f"{CYAN}Restructured baseline fields{cache_tag}:{RESET}")
    for field in LAYER1_STRING_FIELDS:
        val = result.restructured_fields.get(field, "")
        if val:
            print(f"  {DIM}{field}:{RESET} {val[:80]}{'...' if len(val) > 80 else ''}")
        else:
            print(f"  {DIM}{field}:{RESET} (empty)")
    print(
        f"Search baseline: {result.search_baseline.id[:12]} "
        f"(render: {len(result.search_baseline.render())} chars)"
    )

    # Historical diagnostic display
    if result.prompt_index is not None:
        _print_historical_diagnostic(
            result.prompt_index,
            scan_diagnosis=result.scan_diag,
        )

    return result.baseline_jsp, result.search_baseline, result.scan_diag


# ===========================================================================
# Variant library display and schema resolution
# ===========================================================================


def show_variant_library(
    session: BackendContext | None = None,
    *,
    axes: list[str] | None = None,
    source: str | None = None,
) -> dict:
    """Display the variant library with provenance metadata.

    Args:
        session: Service dict (optional). When provided with ``store`` and
            ``backend_id``, shows historical coverage per axis.
        axes: Filter to specific axes (e.g. ``["thinking_style", "persona"]``).
        source: Filter to a specific source (e.g. ``"PromptWizard"``).

    Returns:
        The filtered rich variant dict (objects with ``text``/``source``/``year``).
    """
    from .display import BOLD, GREEN, YELLOW

    rich = load_variant_library_rich()
    all_fields = rich.get("prompt_fields", {})

    if axes:
        all_fields = {k: v for k, v in all_fields.items() if k in axes}

    # Collect source summary across all fields
    source_counts: dict[str, int] = {}
    for variants in all_fields.values():
        for v in variants:
            s = v["source"] if isinstance(v, dict) else "PromptPotter"
            source_counts[s] = source_counts.get(s, 0) + 1

    print(f"{BOLD}Variant Library{RESET}")
    print(f"  Sources: {', '.join(f'{s} ({n})' for s, n in source_counts.items())}")
    if source:
        print(f"  Filter: source={source}")
    print()

    filtered: dict[str, list] = {}
    for field_name, variants in all_fields.items():
        field_variants = []
        for v in variants:
            v_source = v.get("source", "") if isinstance(v, dict) else "PromptPotter"

            if source and v_source != source:
                continue
            field_variants.append(v if isinstance(v, dict) else {"text": v, "source": v_source})

        if not field_variants:
            continue

        filtered[field_name] = field_variants

        # Count by source within this field
        by_source: dict[str, int] = {}
        for fv in field_variants:
            s = fv["source"]
            by_source[s] = by_source.get(s, 0) + 1
        src_summary = ", ".join(f"{s}: {n}" for s, n in by_source.items())

        print(f"  {CYAN}{BOLD}{field_name}{RESET} ({len(field_variants)} variants — {src_summary})")
        for i, fv in enumerate(field_variants):
            text = fv["text"]
            tag = fv["source"]
            year = fv.get("year", "")
            year_str = f" {year}" if year else ""
            preview = text[:70] + "..." if len(text) > 70 else text
            if not preview:
                preview = "(empty baseline)"
            color = GREEN if tag == "PromptPotter" else YELLOW
            print(f"    [{i:2d}] {color}[{tag}{year_str}]{RESET} {preview}")
        print()

    return {"prompt_fields": filtered}


def resolve_scan_variants(
    scan_variants: dict,
    pipeline_schema: PipelineSchema | None = None,
    *,
    session: BackendContext | None = None,
) -> tuple[dict, dict[str, list[str]]]:
    """Resolve schema mutation tuples and display the resolved variants.

    Convenience wrapper for notebook use — resolves schema axes via
    ``resolve_schema_axes`` and prints a summary.

    Accepts nested format: ``{"thinking_style": [...], "web_search": {"max_sites": [...]}}``

    If *pipeline_schema* is ``None`` and *session* is provided, falls back to
    ``session.pipeline_schema``.

    Returns ``(resolved_variants, schema_labels)``.
    """
    from promptpotter.shared.constants import PROMPT_STRING_FIELDS

    if pipeline_schema is None and session is not None:
        pipeline_schema = session.pipeline_schema

    # Flatten nested node groups for schema resolution (only applies to _schema axes)
    flat_for_resolve: dict[str, list] = {}
    for key, spec in scan_variants.items():
        if isinstance(spec, list):
            flat_for_resolve[key] = spec
        elif isinstance(spec, dict):
            for param, vals in spec.items():
                if isinstance(vals, list):
                    flat_for_resolve[param] = vals

    resolved, schema_labels = resolve_schema_axes(flat_for_resolve, pipeline_schema)

    # Display: group by prompt fields vs node params
    for key, spec in scan_variants.items():
        if key in PROMPT_STRING_FIELDS and isinstance(spec, list):
            if key in schema_labels:
                print(f"  {key}: (baseline + {len(spec) - 1} mutations)")
                for i, label in enumerate(schema_labels[key]):
                    print(f"    [{i}] {label}")
            else:
                print(f"  {key}: {spec}")
        elif isinstance(spec, dict):
            for param, vals in spec.items():
                if isinstance(vals, list):
                    r_vals = resolved.get(param, vals)
                    if param in schema_labels:
                        print(f"  {key}.{param}: (baseline + {len(r_vals) - 1} mutations)")
                        for i, label in enumerate(schema_labels[param]):
                            print(f"    [{i}] {label}")
                    else:
                        print(f"  {key}.{param}: {r_vals}")

    # Rebuild nested resolved output
    nested_resolved: dict = {}
    for key, spec in scan_variants.items():
        if isinstance(spec, list):
            nested_resolved[key] = resolved.get(key, spec)
        elif isinstance(spec, dict):
            node_group = {}
            for param, vals in spec.items():
                if isinstance(vals, list):
                    node_group[param] = resolved.get(param, vals)
            nested_resolved[key] = node_group

    return nested_resolved, schema_labels


# ===========================================================================
# LLM-driven scan advisor wrappers
# ===========================================================================


def preview_advisor_prompt(
    campaign_config: CampaignConfig | None = None,
    session: BackendContext | None = None,
    *,
    task_description: str | dict = "",
    raw: bool = False,
) -> None:
    """Display the scan advisor prompt — with real data when session is provided.

    Args:
        raw: When True, print the exact prompt string instead of
            rendering as Markdown. Useful for debugging.
    """
    from IPython.display import Markdown
    from IPython.display import display as ipy_display

    if session is not None:
        pipeline_schema = session.pipeline_schema
        pipeline_params = campaign_config.get("pipeline_params") if campaign_config else None
        exclude_nodes = campaign_config.get("exclude_nodes") if campaign_config else None

        variant_library = _load_filtered_variants(pipeline_params, pipeline_schema)

        prompt = _preview_advisor_prompt(
            pipeline_schema=pipeline_schema,
            variant_library=variant_library,
            pipeline_params=pipeline_params,
            task_description=task_description,
            exclude_nodes=exclude_nodes,
        )
    else:
        prompt = _preview_advisor_prompt()

    if raw:
        print(prompt)
    else:
        ipy_display(Markdown(prompt))


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
    print("PRIORITY AXES (ranked by importance)")
    print("-" * 40)
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
        print("AXES TO SKIP")
        print("-" * 40)
        for ax in skipped:
            print(f"  - {ax.get('axis', '?')}: {ax.get('reason', '?')}")

    budget = advisory.get("budget_breakdown", {})
    if budget:
        print("BUDGET BREAKDOWN")
        print("-" * 40)
        for k, v in budget.items():
            print(f"  {k}: {v}")

    n_diag = advisory.get("suggested_n_diagnostic", 6)
    print(f"  Suggested n_diagnostic: {n_diag}")

    reasoning = advisory.get("reasoning", "")
    if reasoning:
        print("REASONING")
        print("-" * 40)
        print(f"  {reasoning}")

    warnings = advisory.get("validation_warnings", [])
    if warnings:
        print("VALIDATION WARNINGS")
        print("-" * 40)
        for w in warnings:
            print(f"  [!] {w}")


async def scan_advisor(
    campaign_config: CampaignConfig,
    session: BackendContext,
    *,
    task_description: str | dict = "",
    model: str = "",
) -> dict:
    """LLM-powered scan configuration advice.

    Accepts high-level ``campaign_config`` + ``session`` (same shape as
    ``resume_or_build_diagnostic``) and internalizes pipeline_schema
    resolution, variant library loading/filtering, and LLM setup.

    Returns:
        Advisory dict with priority_axes, suggested_n_diagnostic,
        axes_to_skip, budget_breakdown, and reasoning.
    """
    # --- Internalized prep (matches resume_or_build_diagnostic pattern) ---
    pipeline_schema = session.pipeline_schema
    pipeline_params = campaign_config.get("pipeline_params")
    user_excluded = campaign_config.get("exclude_nodes", [])

    print("SCAN ADVISOR -- pipeline-aware sensitivity setup")
    print("-" * 50)

    if pipeline_schema is None:
        print("  Pipeline schema unavailable — start the backend and re-run init_services().")
        return {}

    variant_library = _load_filtered_variants(pipeline_params, pipeline_schema)

    llm_client, resolved_model = setup_llm(campaign_config)
    model = model or resolved_model

    nodes = [s.name for s in pipeline_schema.nodes]
    excluded = f"  excl: {user_excluded}" if user_excluded else ""
    print(f"  {pipeline_schema.name} v{pipeline_schema.version} — {len(nodes)} nodes{excluded}")
    if task_description:
        if isinstance(task_description, dict):
            domain = task_description.get("domain", "?")
            print(f"  Domain: {domain}")
        else:
            print(f"  Task: {task_description[:80].strip()}")
    print(f"  Calling {model or '?'} ...")

    optimizer_llm = campaign_config.get("optimizer_llm", {})
    max_tokens = optimizer_llm.get("max_tokens", 2000)

    advisory = await _advise_scan_config(
        pipeline_schema=pipeline_schema,
        variant_library=variant_library,
        llm_client=llm_client,
        model=model,
        max_tokens=max_tokens,
        pipeline_params=pipeline_params,
        task_description=task_description,
        exclude_nodes=user_excluded or None,
        search_memory=None,
    )

    _display_scan_advisory(advisory)

    return advisory


async def run_scan_advisor(
    campaign_config: CampaignConfig,
    session: BackendContext,
    *,
    task_description: str | dict = "",
    model: str = "",
) -> tuple[dict, dict, dict]:
    """Run scan advisor + extract/display proposed variants.

    Calls scan_advisor(), then advisory_to_scan_variants(), prints summary.
    Returns (advisory, scan_variants, schema_labels).
    """
    advisory = await scan_advisor(
        campaign_config,
        session,
        task_description=task_description,
        model=model,
    )
    if not advisory:
        return {}, {}, {}

    proposed, schema_labels = advisory_to_scan_variants(
        advisory,
        pipeline_schema=session.pipeline_schema,
    )

    # Print copy-pasteable Python dict (nested format)
    print("\n--- PROPOSED SCAN VARIANTS (copy-paste into next cell) ---")
    print("scan_variants = {")
    for key, spec in proposed.items():
        if isinstance(spec, dict):
            # Node param group
            print(f"    {key!r}: {{")
            for param, vals in spec.items():
                if param in schema_labels:
                    # Schema axis: print raw mutation tuples
                    raw_mutations = []
                    for ax in advisory.get("priority_axes", []):
                        if ax.get("axis") == param:
                            raw_mutations = ax.get("suggested_values", [])
                            break
                    print(f"        {param!r}: [")
                    for variant in raw_mutations:
                        if isinstance(variant, str):
                            with contextlib.suppress(json.JSONDecodeError, ValueError):
                                variant = json.loads(variant)
                        print(f"            {variant!r},")
                    print("        ],")
                else:
                    print(f"        {param!r}: {vals!r},")
            print("    },")
        else:
            # Prompt field (list)
            print(f"    {key!r}: {spec!r},")
    print("}")

    n_diag = advisory.get("suggested_n_diagnostic", 10)
    print(f"\nscan_sample_size = {n_diag}  # queries per variant (advisor recommendation)")

    return advisory, proposed if proposed else {}, schema_labels


# ===========================================================================
# Historical diagnostics and coverage display
# ===========================================================================

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

    print(f"{CYAN}Historical data:{RESET} {n_results} results across {n_prompts} unique prompts")

    if scan_diagnosis:
        n_matching = scan_diagnosis["n_matching_runs"]
        n_cached = scan_diagnosis["n_matching_results"]
        print(f"Matching runs (step sequence): {n_matching}, {n_cached} cached results")

    # Scan variant coverage
    if not (scan_diagnosis and scan_diagnosis["axes"]):
        return

    n_matching = scan_diagnosis["n_matching_runs"]
    print(f"{CYAN}Scan variant coverage ({n_matching} matching runs):{RESET}")

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
                chunks.append(f"{display}\u2192{DIM}\u2717{RESET}")

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
        baseline_ps,
        variant_library,
        diagnostic,
        prompt_index,
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
    print(
        f"  Baseline: {bl['n_cached']}/{bl['n_needed']} queries cached"
        f" {'[ok]' if bl['sufficient'] else '[!!]'}"
    )
    print()

    pf_axes = [a for a in axes if a["axis_type"] == "prompt_field"]
    pp_axes = [a for a in axes if a["axis_type"] == "pipeline_param"]

    if pf_axes:
        print("  Prompt field axes:")
        for a in pf_axes:
            label = "value" if a["n_values"] == 1 else "values"
            usable_parts = []
            if a.get("variants"):
                n_partial = sum(1 for v in a["variants"] if 0 < v["n_cached"] < min_queries)
                usable_parts.append(f"{a['n_usable']} usable")
                if n_partial:
                    usable_parts.append(f"{n_partial} partial")
                n_uncovered = sum(1 for v in a["variants"] if v["n_cached"] == 0)
                if n_uncovered:
                    usable_parts.append(f"{n_uncovered} uncovered")
            detail = f"  ({', '.join(usable_parts)})" if usable_parts else ""
            mark = "[ok]" if a["sufficient"] else "[!!]"
            print(
                f"    {a['axis']:<22s} {a['n_values']} {label:<6s} "
                f"| {a['n_usable']}/{a['n_required']} required  "
                f"{mark}{detail}"
            )
        print()

    if pp_axes:
        print("  Pipeline params:")
        n_diag = result["n_diagnostic"]
        for a in pp_axes:
            n_hist = a.get("n_cached_historical", 0)
            n_compat = a.get("n_step_compatible", 0)
            n_calls = a["n_values"] * n_diag
            if n_hist:
                print(
                    f"    {a['axis']:<22s} {a['n_values']} variants "
                    f"| {n_hist} cached ({n_compat} step-compatible)"
                )
            else:
                print(
                    f"    {a['axis']:<22s} {a['n_values']} variants "
                    f"x {n_diag} queries = {n_calls} calls (no historical data)"
                )
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

    if summary["backend_calls_saved"] == 0 and sum(len(v) for v in prompt_index.values()) > 0:
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
            print(
                f"  {axis_name:<22s} {info['n_prompts']:>8d} "
                f"{info['n_queries']:>8d} {info['distinct_values']:>16d}"
            )
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
            print(f"    {pname:<24s} {card} values scanned  sensitivity: {sens:.3f}  [{budget}]")
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
            print(
                f"  {axis_name:<22s} {info['n_prompts']:>8d} "
                f"{info['n_queries']:>8d} {info['distinct_values']:>16d}"
            )
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
            print(f"    {pname:<24s} {card} values scanned  sensitivity: {sens:.3f}  [{budget}]")
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


# ===========================================================================
# Downstream result wrappers: winner selection, campaign seeding, analytics
# ===========================================================================


async def resume_or_build_diagnostic(
    campaign_config: CampaignConfig,
    baseline,
    baseline_results: list,
    session: BackendContext,
    dataset: list,
    scan_variants: dict | None = None,
) -> tuple[str, object, list, list, dict]:
    """Resume or build smart search diagnostic set.

    Prepares LLM client and variant library internally, then delegates to the
    service-layer ``_resume_or_build_diagnostic()``.

    Returns:
        (plan_id, search_baseline, diagnostic, cached_profiles, variant_library)
    """
    # Prepare variant library: base + scan_variants merge
    pipeline_params = campaign_config.get("pipeline_params")
    variant_library = _load_filtered_variants(pipeline_params, session.pipeline_schema)
    if scan_variants:
        variant_library["pipeline_params"] = scan_variants

    # Prepare LLM client
    llm_client, llm_model = setup_llm(campaign_config)

    result = await _resume_or_build_diagnostic(
        campaign_config,
        baseline,
        baseline_results,
        llm_client,
        llm_model,
        session.store,
        session.backend_id,
        dataset,
        variant_library=variant_library,
    )
    plan_id = result.plan_id
    search_baseline = result.search_baseline
    diagnostic = result.diagnostic
    axis_profiles = result.axis_profiles

    if axis_profiles:
        print(f"[RESUME] Plan {plan_id}: {len(axis_profiles)} axis profiles available")
    else:
        print(
            f"  search_baseline: {search_baseline.id[:12]} "
            f"(render: {len(search_baseline.render())} chars)"
        )

    return plan_id, search_baseline, diagnostic, axis_profiles, variant_library


def select_scan_winner_notebook(
    scan_df,
    axis_profiles: list[dict],
    baseline,
    scan_variants: dict[str, list],
):
    """Pick best from scan and print summary. No backend needed.

    Returns:
        SearchPoint with best values composed in.
    """
    if scan_df is None or (hasattr(scan_df, "empty") and scan_df.empty):
        print("No scan data available. Run sensitivity scan first.")
        return baseline

    best_sp = _select_scan_winner(
        scan_df,
        axis_profiles,
        baseline,
        scan_variants,
    )

    # Print summary
    improving = [
        p for p in axis_profiles if p["best_delta"] > 0 and p["exploration_budget"] != "skip"
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

    if best_sp.sp_hash() != baseline.sp_hash():
        print(f"\nComposed winner: sp_hash={best_sp.sp_hash()[:12]}")

    return best_sp


def seed_campaign_from_scan(
    scan_df,
    axis_profiles: list,
    baseline,
    scan_variants: dict[str, list],
    campaign_rounds: list,
    campaign_config: CampaignConfig,
    pipeline_schema=None,
):
    """Select scan winner, update pipeline_params, seed campaign_rounds.

    Delegates to ``promptpotter.services.search.scan_results.seed_campaign_from_scan()``
    and prints summary + progress.
    """
    if scan_df is None or (hasattr(scan_df, "empty") and scan_df.empty):
        print("No scan data available. Run sensitivity scan first.")
        return baseline

    result = _seed_campaign_from_scan(
        scan_df,
        axis_profiles,
        baseline,
        scan_variants,
        campaign_rounds,
        campaign_config,
    )

    # Print improving axes summary
    if result.improving_axes:
        print(f"Selected best from {len(result.improving_axes)} improving axes:")
        for p in result.improving_axes:
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

    # Print pipeline_params update
    if result.merged_pipeline_params:
        display_pp = {}
        for k, v in result.merged_pipeline_params.items():
            if isinstance(v, dict) and len(str(v)) > 100:
                n_keys = len(v.get("properties", v))
                display_pp[k] = f"<{n_keys} fields>"
            else:
                display_pp[k] = v
        print(f"Updated pipeline_params: {display_pp}")
        format_pipeline_overrides(result.merged_pipeline_params, pipeline_schema)

    if result.best_sp.sp_hash() != baseline.sp_hash():
        print(f"\nComposed winner: sp_hash={result.best_sp.sp_hash()[:12]}")

    show_progress(campaign_rounds)

    return result.best_sp


def show_scan_analytics(scan_df, axis_profiles, session: BackendContext):
    """Display scan leaderboard and query difficulty if results are available.

    Returns difficulty_df (or None if scan_df is empty/None).
    """
    if scan_df is None or scan_df.empty:
        return None
    show_scan_leaderboard(scan_df, axis_profiles)
    return show_scan_query_difficulty(session.store, session.backend_id)
