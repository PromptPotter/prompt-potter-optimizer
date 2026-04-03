"""Variant library display and schema resolution helpers."""

from __future__ import annotations

import logging

from api.config.settings import load_variant_library_rich
from api.models.pipeline_schema import PipelineSchema
from api.services.search.scan_advisor import (
    _resolve_schema_axes,
    advisory_to_scan_variants,
)

logger = logging.getLogger(__name__)

__all__ = [
    "advisory_to_scan_variants",
    "resolve_scan_variants",
    "show_variant_library",
]


# ---------------------------------------------------------------------------
# Variant library browsing
# ---------------------------------------------------------------------------


def show_variant_library(
    svc: dict | None = None,
    *,
    axes: list[str] | None = None,
    source: str | None = None,
) -> dict:
    """Display the variant library with provenance metadata.

    Args:
        svc: Service dict (optional). When provided with ``store`` and
            ``backend_id``, shows historical coverage per axis.
        axes: Filter to specific axes (e.g. ``["thinking_style", "persona"]``).
        source: Filter to a specific source (e.g. ``"PromptWizard"``).

    Returns:
        The filtered rich variant dict (objects with ``text``/``source``/``year``).
    """
    from .display import BOLD, CYAN, GREEN, RESET, YELLOW

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
    svc: dict | None = None,
) -> tuple[dict, dict[str, list[str]]]:
    """Resolve schema mutation tuples and display the resolved variants.

    Convenience wrapper for notebook use — resolves schema axes via
    ``_resolve_schema_axes`` and prints a summary.

    Accepts nested format: ``{"thinking_style": [...], "web_search": {"max_sites": [...]}}``

    If *pipeline_schema* is ``None`` and *svc* is provided, falls back to
    ``svc.pipeline_schema``.

    Returns ``(resolved_variants, schema_labels)``.
    """
    from api.shared.constants import PROMPT_STRING_FIELDS

    if pipeline_schema is None and svc is not None:
        pipeline_schema = svc.pipeline_schema

    # Flatten nested node groups for schema resolution (only applies to _schema axes)
    flat_for_resolve: dict[str, list] = {}
    for key, spec in scan_variants.items():
        if isinstance(spec, list):
            flat_for_resolve[key] = spec
        elif isinstance(spec, dict):
            for param, vals in spec.items():
                if isinstance(vals, list):
                    flat_for_resolve[param] = vals

    resolved, schema_labels = _resolve_schema_axes(flat_for_resolve, pipeline_schema)

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
