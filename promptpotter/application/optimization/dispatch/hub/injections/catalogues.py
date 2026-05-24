"""Discoverability menus — pipeline-param search-space (L1 reads when proposing override) and
L1 signal menu (L2 reads when authoring l1_layout). Load-bearing per architecture.md §0.5.
"""

from __future__ import annotations

from promptpotter.application.optimization.dispatch.hub.bundle import (
    AXES_ENUM_PREVIEW,
    PIPELINE_PARAM_CATALOGUE_MODEL_CAP,
    InjectionBundle,
)
from promptpotter.domain.l1_layout import L1_POSSIBLE


def _r_pipeline_param_catalogue(b: InjectionBundle) -> str:
    """Pipeline-param menu (name + ≤4-value enum hint) — what L1 picks from for `pipeline_params_override`.
    Symmetric with `l1_signal_catalogue` (the menu L2 picks from for L1's layout).
    """
    schema = b.pipeline_schema
    if schema is None:
        return ""
    npk = schema.node_param_keys()
    if not npk:
        return ""
    lines = ["PIPELINE PARAM CATALOGUE (use only these — do not invent):"]
    for node_name, params in npk.items():
        node = schema.get_node(node_name)
        if not node or not params:
            continue
        descs = node.param_descriptions or {}
        enums = node.param_allowed_values or {}
        bits: list[str] = []
        for p in sorted(params):
            allowed = enums.get(p)
            if allowed:
                shown = list(allowed)[:AXES_ENUM_PREVIEW]
                preview = ", ".join(str(x) for x in shown)
                if len(allowed) > AXES_ENUM_PREVIEW:
                    preview += f", … (+{len(allowed) - AXES_ENUM_PREVIEW})"
                bits.append(f"{p} [{preview}]")
            elif desc := descs.get(p):
                bits.append(f"{p} ({desc[:40]})")
            else:
                bits.append(p)
        lines.append(f"  {node_name}: {', '.join(bits)}")
    # Suppress MODELS when operator-locked — advertising a list the validator will reject just
    # costs L1 a candidate slot per round to Wound 1.
    if schema.available_models and not b.forbidden_axes_strict:
        lines.append("MODELS:")
        lines.append(
            "  " + ", ".join(list(schema.available_models)[:PIPELINE_PARAM_CATALOGUE_MODEL_CAP])
        )
    return "\n".join(lines)


def _r_l1_signal_catalogue(b: InjectionBundle) -> str:
    """Names only — sorted ``L1_POSSIBLE``. L2 may pick from this menu."""
    return "L1 SIGNAL MENU (placeholders L2 may use in l1_layout):\n  " + "\n  ".join(
        sorted(L1_POSSIBLE)
    )
