"""Variant library display and schema resolution helpers."""

from __future__ import annotations

import json
import logging

from api.models.pipeline_schema import PipelineSchema
from api.config.settings import load_variant_library_rich

logger = logging.getLogger(__name__)

__all__ = [
    "show_variant_library",
    "advisory_to_scan_variants",
    "resolve_scan_variants",
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
    from .display import BOLD, RESET, CYAN, GREEN, YELLOW

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
            if isinstance(v, dict):
                v_source = v.get("source", "")
            else:
                v_source = "PromptPotter"

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
        baseline_schema_from_node,
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
            node_name = pipeline_schema.node_for_flat_param(axis_name)
            node = pipeline_schema.get_node(node_name) if node_name else None
            if node and node.output_schema:
                try:
                    variants = [parse_mutation_tuples(sv) for sv in vals]
                    baseline = baseline_schema_from_node(node.output_schema)
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
        vals = ax["suggested_values"]
        # LLMs sometimes return mutation arrays as JSON strings — parse them
        if ax["axis"].endswith("_schema"):
            parsed = []
            for v in vals:
                if isinstance(v, str):
                    try:
                        v = json.loads(v)
                    except (json.JSONDecodeError, ValueError):
                        pass
                parsed.append(v)
            vals = parsed
        raw[ax["axis"]] = vals

    return _resolve_schema_axes(raw, pipeline_schema)


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
    ``svc["pipeline_schema"]``.

    Returns ``(resolved_variants, schema_labels)``.
    """
    if pipeline_schema is None and svc is not None:
        pipeline_schema = svc.get("pipeline_schema")

    resolved, schema_labels = _resolve_schema_axes(scan_variants, pipeline_schema)

    for axis, vals in resolved.items():
        if axis in schema_labels:
            print(f"  {axis}: (baseline + {len(vals) - 1} mutations)")
            for i, label in enumerate(schema_labels[axis]):
                print(f"    [{i}] {label}")
        else:
            print(f"  {axis}: {vals}")

    return resolved, schema_labels
