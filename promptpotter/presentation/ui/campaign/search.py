"""Search display wrappers — baseline, variants, advisor, coverage, results."""

from __future__ import annotations

import contextlib
import json
import logging
from pathlib import Path
from typing import TYPE_CHECKING

from promptpotter.application.intelligence import load_variant_library_rich
from promptpotter.application.recon import (
    advise_recon as _advise_recon,
)
from promptpotter.application.recon import (
    load_filtered_variant_library as _load_filtered_variants,
)
from promptpotter.application.recon import (
    preview_advisor_prompt as _preview_advisor_prompt,
)
from promptpotter.application.recon import (
    resume_or_build_diagnostic as _resume_or_build_diagnostic,
)
from promptpotter.application.recon.recon_advisor import (
    convert_advisory_to_recon_variants,
    resolve_schema_axes,
)
from promptpotter.application.recon.recon_report import (
    decompose_recon_baseline as _decompose_scan_baseline,
)
from promptpotter.application.recon.recon_report import (
    finalize_scan as _finalize_scan,
)
from promptpotter.domain.pipeline_schema import PipelineSchema
from promptpotter.shared.constants import LAYER1_STRING_FIELDS

from .notebook_display import (
    CYAN,
    DIM,
    RESET,
    format_pipeline_overrides,
    show_progress,
    show_recon_leaderboard,
    show_recon_query_difficulty,
)
from .setup import setup_llm

if TYPE_CHECKING:
    from promptpotter.application.campaign.campaign_setup import SessionEnv
    from promptpotter.application.campaign.config import CampaignConfig

logger = logging.getLogger(__name__)

__all__ = [
    # search_variants
    "convert_advisory_to_recon_variants",
    # search_baseline
    "decompose_recon_baseline",
    # search_advisor
    "load_task_description",
    "preview_advisor_prompt",
    "recon_advisor",
    "resolve_recon_variants",
    # search_results
    "resume_or_build_diagnostic",
    "run_recon_advisor",
    "seed_campaign_from_recon",
    "select_recon_winner_notebook",
    "show_recon_analytics",
    "show_variant_library",
]


# ===========================================================================
# Scan baseline preparation
# ===========================================================================


async def decompose_recon_baseline(
    baseline,
    campaign_config: CampaignConfig,
    pipeline_params: dict | None = None,
    *,
    recon_variants: dict | None = None,
    force_restructure: bool = False,
    session: SessionEnv | None = None,
):
    """Restructure baseline instruction into PromptPotter's internal fields.

    Delegates to ``application.scan.recon_report.decompose_recon_baseline()``
    and prints the restructured fields.

    Returns:
        (baseline_jsp, search_baseline)
    """
    from .setup import configure_pipeline

    # Fresh pipeline defaults when not explicitly provided
    if pipeline_params is None and session is not None:
        pipeline_params = configure_pipeline(session, campaign_config)

    llm_client, llm_model = setup_llm(campaign_config)

    ps = session.pipeline_schema if session is not None else None

    result = await _decompose_scan_baseline(
        baseline,
        campaign_config,
        llm_client,
        llm_model,
        pipeline_params=pipeline_params,
        session=session,
        recon_variants=recon_variants,
        force_restructure=force_restructure,
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

    return result.baseline_jsp, result.search_baseline


# ===========================================================================
# Variant library display and schema resolution
# ===========================================================================


def show_variant_library(
    session: SessionEnv | None = None,
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
    from promptpotter.application.recon.adaptive_recon import filter_variant_library_display

    from .notebook_display import BOLD, GREEN, YELLOW

    rich = load_variant_library_rich()
    lib = filter_variant_library_display(rich, axes=axes, source=source)

    print(f"{BOLD}Variant Library{RESET}")
    print(f"  Sources: {', '.join(f'{s} ({n})' for s, n in lib.source_counts.items())}")
    if source:
        print(f"  Filter: source={source}")
    print()

    for field_name, field_variants in lib.fields.items():
        by_source = lib.per_field_sources.get(field_name, {})
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

    return {"prompt_fields": lib.fields}


def resolve_recon_variants(
    recon_variants: dict,
    pipeline_schema: PipelineSchema | None = None,
    *,
    session: SessionEnv | None = None,
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

    from promptpotter.application.recon.recon_advisor import (
        flatten_recon_variants,
        rebuild_nested_resolved,
    )

    flat_for_resolve = flatten_recon_variants(recon_variants)
    resolved, schema_labels = resolve_schema_axes(flat_for_resolve, pipeline_schema)

    # Display: group by prompt fields vs node params
    for key, spec in recon_variants.items():
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

    return rebuild_nested_resolved(recon_variants, resolved), schema_labels


# ===========================================================================
# LLM-driven scan advisor wrappers
# ===========================================================================


def preview_advisor_prompt(
    campaign_config: CampaignConfig | None = None,
    session: SessionEnv | None = None,
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


def _display_recon_advisory(advisory: dict) -> None:
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


async def recon_advisor(
    campaign_config: CampaignConfig,
    session: SessionEnv,
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

    advisory = await _advise_recon(
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

    _display_recon_advisory(advisory)

    return advisory


async def run_recon_advisor(
    campaign_config: CampaignConfig,
    session: SessionEnv,
    *,
    task_description: str | dict = "",
    model: str = "",
) -> tuple[dict, dict, dict]:
    """Run scan advisor + extract/display proposed variants.

    Calls recon_advisor(), then convert_advisory_to_recon_variants(), prints summary.
    Returns (advisory, recon_variants, schema_labels).
    """
    advisory = await recon_advisor(
        campaign_config,
        session,
        task_description=task_description,
        model=model,
    )
    if not advisory:
        return {}, {}, {}

    proposed, schema_labels = convert_advisory_to_recon_variants(
        advisory,
        pipeline_schema=session.pipeline_schema,
    )

    # Print copy-pasteable Python dict (nested format)
    print("\n--- PROPOSED SCAN VARIANTS (copy-paste into next cell) ---")
    print("recon_variants = {")
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
    print(f"\nrecon_sample_size = {n_diag}  # queries per variant (advisor recommendation)")

    return advisory, proposed if proposed else {}, schema_labels


# ===========================================================================
# Downstream result wrappers: winner selection, campaign seeding, analytics
# ===========================================================================


async def resume_or_build_diagnostic(
    campaign_config: CampaignConfig,
    baseline,
    baseline_results: list,
    session: SessionEnv,
    dataset: list,
    recon_variants: dict | None = None,
) -> tuple[str, object, list, list, dict]:
    """Resume or build smart search diagnostic set.

    Prepares LLM client and variant library internally, then delegates to the
    service-layer ``_resume_or_build_diagnostic()``.

    Returns:
        (plan_id, search_baseline, diagnostic, cached_profiles, variant_library)
    """
    # Prepare variant library: base + recon_variants merge
    pipeline_params = campaign_config.get("pipeline_params")
    variant_library = _load_filtered_variants(pipeline_params, session.pipeline_schema)
    if recon_variants:
        variant_library["pipeline_params"] = recon_variants

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


def _display_improving_axes(recon_df, improving_axes: list) -> None:
    """Print formatted summary of improving scan axes."""
    if improving_axes:
        print(f"Selected best from {len(improving_axes)} improving axes:")
        for p in improving_axes:
            axis_rows = recon_df[recon_df["axis"] == p["axis"]]
            if not axis_rows.empty:
                best_row = axis_rows.loc[axis_rows["accuracy"].idxmax()]
                print(
                    f"  {p['axis']:<25s} best_delta=+{p['best_delta']:.1%}  "
                    f"value_idx={int(best_row['value_idx'])}  "
                    f"acc={best_row['accuracy']:.1%}"
                )
    else:
        print("No axes improved over baseline -- using baseline as-is.")


def select_recon_winner_notebook(
    recon_df,
    axis_profiles: list[dict],
    baseline,
    recon_variants: dict[str, list],
):
    """Pick best from scan and print summary. No backend needed.

    Returns:
        SearchPoint with best values composed in.
    """
    if recon_df is None or (hasattr(recon_df, "empty") and recon_df.empty):
        print("No scan data available. Run sensitivity scan first.")
        return baseline

    result = _finalize_scan(recon_df, axis_profiles, baseline, recon_variants)
    _display_improving_axes(recon_df, result.improving_axes)

    if result.best_sp.sp_hash() != baseline.sp_hash():
        print(f"\nComposed winner: sp_hash={result.best_sp.sp_hash()[:12]}")

    return result.best_sp


def seed_campaign_from_recon(
    recon_df,
    axis_profiles: list,
    baseline,
    recon_variants: dict[str, list],
    campaign_rounds: list,
    campaign_config: CampaignConfig,
    pipeline_schema=None,
):
    """Select scan winner, update pipeline_params, seed campaign_rounds.

    Delegates to ``promptpotter.application.recon.recon_report.seed_campaign_from_recon()``
    and prints summary + progress.
    """
    if recon_df is None or (hasattr(recon_df, "empty") and recon_df.empty):
        print("No scan data available. Run sensitivity scan first.")
        return baseline

    result = _finalize_scan(
        recon_df,
        axis_profiles,
        baseline,
        recon_variants,
        campaign_rounds=campaign_rounds,
        campaign_config=campaign_config,
    )

    # Print improving axes summary
    _display_improving_axes(recon_df, result.improving_axes)

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


def show_recon_analytics(recon_df, axis_profiles, session: SessionEnv):
    """Display scan leaderboard and query difficulty if results are available.

    Returns difficulty_df (or None if recon_df is empty/None).
    """
    if recon_df is None or recon_df.empty:
        return None
    show_recon_leaderboard(recon_df, axis_profiles)
    return show_recon_query_difficulty(session.store, session.backend_id)
