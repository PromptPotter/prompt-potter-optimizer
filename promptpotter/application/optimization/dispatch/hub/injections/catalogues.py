"""Discoverability menus — pipeline-param search-space (L1 reads when proposing override) and
L1 signal menu (L2 reads when authoring l1_layout). Load-bearing per architecture.md §0.5.
"""

from __future__ import annotations

from promptpotter.application.optimization.dispatch.hub.bundle import (
    AXES_ENUM_PREVIEW,
    InjectionBundle,
    InjectionKind,
    signal,
)
from promptpotter.domain.l1_layout import L1_POSSIBLE


@signal(
    "pipeline_param_catalogue",
    kind=InjectionKind.DERIVED,
    description="Pipeline-param menu: name + ≤4-value enum hint per node, plus available models.",
    char_cap=None,
)
def _r_pipeline_param_catalogue(b: InjectionBundle) -> str:
    """Pipeline-param menu (name + ≤4-value enum hint) — what L1 picks from for `pipeline_params_override`.
    Symmetric with `l1_signal_catalogue` (the menu L2 picks from for L1's layout).
    """
    schema = b.pipeline_schema
    if schema is None:
        return ""
    # ONE surface, gated by the lock: when strict the `model` axis is absent, when
    # unlocked it's synthesized with `available_models` as its value space.
    npk = schema.node_param_keys(forbidden_strict=b.forbidden_axes_strict)
    if not npk:
        return ""
    available_models = list(schema.available_models)
    lines = ["PIPELINE PARAM CATALOGUE (use only these — do not invent):"]
    for node_name, params in npk.items():
        node = schema.get_node(node_name)
        if not node or not params:
            continue
        descs = node.param_descriptions
        enums = node.param_allowed_values
        bits: list[str] = []
        for p in sorted(params):
            allowed = enums.get(p) or (available_models if p == "model" else None)
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
    return "\n".join(lines)


@signal(
    "l1_signal_catalogue",
    kind=InjectionKind.DERIVED,
    description="L1 SIGNAL MENU: sorted L1_POSSIBLE placeholder names L2 may use in l1_layout.",
    char_cap=None,
)
def _r_l1_signal_catalogue(b: InjectionBundle) -> str:
    """Names only — sorted ``L1_POSSIBLE``. L2 may pick from this menu."""
    return "L1 SIGNAL MENU (placeholders L2 may use in l1_layout):\n  " + "\n  ".join(
        sorted(L1_POSSIBLE)
    )
