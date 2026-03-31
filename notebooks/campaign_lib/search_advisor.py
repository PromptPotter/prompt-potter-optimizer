"""LLM-driven scan advisor wrappers."""

from __future__ import annotations

import contextlib
import json
import logging
from pathlib import Path

from api.services.search import (
    advise_scan_config as _advise_scan_config,
)
from api.services.search import (
    load_filtered_variant_library as _load_filtered_variants,
)
from api.services.search import (
    preview_advisor_prompt as _preview_advisor_prompt,
)

from .search_variants import advisory_to_scan_variants
from .setup import setup_llm

logger = logging.getLogger(__name__)

__all__ = [
    "load_task_description",
    "preview_advisor_prompt",
    "run_scan_advisor",
    "scan_advisor",
]


def preview_advisor_prompt(
    campaign_config: dict | None = None,
    svc: dict | None = None,
    *,
    task_description: str | dict = "",
    raw: bool = False,
) -> None:
    """Display the scan advisor prompt — with real data when svc is provided.

    Args:
        raw: When True, print the exact prompt string instead of
            rendering as Markdown. Useful for debugging.
    """
    from IPython.display import Markdown
    from IPython.display import display as ipy_display

    if svc is not None:
        pipeline_schema = svc.get("pipeline_schema")
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
    campaign_config: dict,
    svc: dict,
    *,
    task_description: str | dict = "",
    model: str = "",
) -> dict:
    """LLM-powered scan configuration advice.

    Accepts high-level ``campaign_config`` + ``svc`` (same shape as
    ``resume_or_build_diagnostic``) and internalizes pipeline_schema
    resolution, variant library loading/filtering, and LLM setup.

    Returns:
        Advisory dict with priority_axes, suggested_n_diagnostic,
        axes_to_skip, budget_breakdown, and reasoning.
    """
    # --- Internalized prep (matches resume_or_build_diagnostic pattern) ---
    pipeline_schema = svc.get("pipeline_schema")
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
            domain = task_description.get('domain', '?')
            print(f"  Domain: {domain}")
        else:
            print(f"  Task: {task_description[:80].strip()}")
    print(f"  Calling {model or '?'} ...")

    eval_llm = campaign_config.get("eval_llm", {})
    max_tokens = eval_llm.get("max_tokens", 2000)

    advisory = await _advise_scan_config(
        pipeline_schema=pipeline_schema,
        variant_library=variant_library,
        llm_client=llm_client,
        model=model,
        max_tokens=max_tokens,
        pipeline_params=pipeline_params,
        task_description=task_description,
        exclude_nodes=user_excluded or None,
        search_memory=svc.get("search_memory"),
    )

    _display_scan_advisory(advisory)

    return advisory


async def run_scan_advisor(
    campaign_config: dict,
    svc: dict,
    *,
    task_description: str | dict = "",
    model: str = "",
) -> tuple[dict, dict, dict]:
    """Run scan advisor + extract/display proposed variants.

    Calls scan_advisor(), then advisory_to_scan_variants(), prints summary.
    Returns (advisory, scan_variants, schema_labels).
    """
    advisory = await scan_advisor(
        campaign_config, svc,
        task_description=task_description,
        model=model,
    )
    if not advisory:
        return {}, {}, {}

    proposed, schema_labels = advisory_to_scan_variants(
        advisory, pipeline_schema=svc.get("pipeline_schema"),
    )

    # Print copy-pasteable Python dict
    print("\n--- PROPOSED SCAN VARIANTS (copy-paste into next cell) ---")
    print("scan_variants = {")
    for axis, values in proposed.items():
        if axis in schema_labels:
            # Schema axis: print raw mutation tuples from advisory (one variant per line)
            raw_mutations = []
            for ax in advisory.get("priority_axes", []):
                if ax.get("axis") == axis:
                    raw_mutations = ax.get("suggested_values", [])
                    break
            print(f"    {axis!r}: [")
            for variant in raw_mutations:
                # LLMs sometimes return mutation arrays as JSON strings — parse them
                if isinstance(variant, str):
                    with contextlib.suppress(json.JSONDecodeError, ValueError):
                        variant = json.loads(variant)
                print(f"        {variant!r},")
            print("    ],")
        else:
            print(f"    {axis!r}: {values!r},")
    print("}")

    n_diag = advisory.get("suggested_n_diagnostic", 10)
    print(f"\nscan_sample_size = {n_diag}  # queries per variant (advisor recommendation)")

    return advisory, proposed if proposed else {}, schema_labels
