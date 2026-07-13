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
from promptpotter.config.prompt_blocks import prompt_blocks
from promptpotter.domain.l1_layout import L1_POSSIBLE
from promptpotter.domain.pipeline_schema import SCHEMA_DESCRIPTIONS_PARAM, PipelineNode


def _schema_description_block(node: PipelineNode) -> list[str]:
    """The node's CURRENT output-schema `description` per field — the value space of the
    ``output_schema_descriptions`` param, rendered the way an enum's values are.

    Without it the lever is offered blind: its instruction says "describe only where the
    current prose underspecifies", and a model that cannot read the current prose either
    rewrites good text or skips the lever. An undescribed field is marked, because an empty
    slot is the highest-value target, not an absent one.
    """
    out_schema = node.output_schema
    if out_schema is None or not out_schema.fields:
        return []
    described = out_schema.field_descriptions
    lines = [
        f"    {SCHEMA_DESCRIPTIONS_PARAM} — current prose per field (rewrite what underspecifies):"
    ]
    for field in out_schema.fields:
        prose = described.get(field)
        lines.append(f"      {field}: {prose}" if prose else f"      {field}: (undescribed)")
    return lines


@signal(
    "pipeline_param_catalogue",
    kind=InjectionKind.DERIVED,
    description="Pipeline-param menu: name + ≤4-value enum hint per node, plus available models.",
    char_cap=None,
    # A value-space menu is what a mutation may SAY, never why it should be made.
    citable=False,
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
        if SCHEMA_DESCRIPTIONS_PARAM in params:
            lines.extend(_schema_description_block(node))
    return "\n".join(lines)


# The two modes that render. `off` is absent by construction: no header ⇒ no text ⇒ the
# slot is bit-for-bit identical to a no-library ablation run (facade.py drops empty renders).
_BLOCK_LIBRARY_HEADERS: dict[str, str] = {
    "guidance": (
        "PROMPT BLOCK LIBRARY — field values that have earned their keep "
        "(reuse one verbatim, adapt one, or write your own):"
    ),
    "restrict": ("PROMPT BLOCK LIBRARY (use only these — do not invent):"),
}


@signal(
    "prompt_block_catalogue",
    kind=InjectionKind.DERIVED,
    description="Reusable prompt-field blocks (persona / task_intent / thinking_style / answer_format) L1 recombines.",
    char_cap=None,
    citable=False,
)
def _r_prompt_block_catalogue(b: InjectionBundle) -> str:
    """The building-block library — what L1 picks from for `prompt_fields_override`.

    Symmetric with `pipeline_param_catalogue` (the param menu): one names the value space
    of a pipeline param, this one the value space of a prompt field.
    """
    header = _BLOCK_LIBRARY_HEADERS.get(b.prompt_block_catalogue)
    if header is None:
        return ""
    lines = [header]
    for field, blocks in prompt_blocks().items():
        lines.append(f"  {field}:")
        lines.extend(f"    - {text}" for text in blocks)
    return "\n".join(lines)


@signal(
    "l1_signal_catalogue",
    kind=InjectionKind.DERIVED,
    description="L1 SIGNAL MENU: sorted L1_POSSIBLE placeholder names L2 may use in l1_layout.",
    char_cap=None,
    citable=False,
)
def _r_l1_signal_catalogue(b: InjectionBundle) -> str:
    """Names only — sorted ``L1_POSSIBLE``. L2 may pick from this menu."""
    return "L1 SIGNAL MENU (placeholders L2 may use in l1_layout):\n  " + "\n  ".join(
        sorted(L1_POSSIBLE)
    )
