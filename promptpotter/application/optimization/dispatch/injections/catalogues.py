"""Discoverability menus — pipeline-param search-space (L1 reads when proposing override) and the
cross-slot layout rule (L2 reads when authoring l1_layout; that field's own schema carries its
vocabulary). Load-bearing per architecture.md §0.5.
"""

from __future__ import annotations

from promptpotter.application.optimization.dispatch.bundle import (
    AXES_ENUM_PREVIEW,
    InjectionBundle,
    InjectionKind,
    Item,
    signal,
)
from promptpotter.config.prompt_blocks import general_reasoning_blocks, prompt_blocks
from promptpotter.domain.l1_layout import NODE_LAYOUTS
from promptpotter.domain.pipeline_schema import SCHEMA_DESCRIPTIONS_PARAM, PipelineNode


def _schema_description_block(node: PipelineNode) -> list[str]:
    """The node's CURRENT output-schema descriptions — the value space of the ``output_schema_descriptions`` param.
    Without it the lever is offered blind; an UNDESCRIBED field is marked, being the highest-value target."""
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
    # No "plus available models" — the renderer never emitted them and must not: the model
    # axis is operator-owned, so advertising the catalogue here would teach a lever L1 cannot
    # pull (`node_param_keys` strips model/provider before this ever sees them).
    char_cap=None,
    # A value-space menu is what a mutation may SAY, never why it should be made.
    citable=False,
)
def _r_pipeline_param_catalogue(b: InjectionBundle) -> list[Item]:
    """Pipeline-param menu (name + ≤4-value enum hint) — what L1 picks from for `pipeline_params_override`.
    Symmetric with `l1_signal_catalogue` (the menu L2 picks from for L1's layout).
    """
    schema = b.pipeline_schema
    if schema is None:
        return []
    # ONE surface: model/provider are always absent (operator-owned, never an
    # optimizer axis), so the catalogue never advertises them.
    npk = schema.node_param_keys()
    if not npk:
        return []
    lines = ["PIPELINE PARAM CATALOGUE (use only these — do not invent):"]
    for node_name, params in npk.items():
        node = schema.get_node(node_name)
        if not node or not params:
            continue
        descs = node.param_descriptions
        enums = node.param_allowed_values
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
        if SCHEMA_DESCRIPTIONS_PARAM in params:
            lines.extend(_schema_description_block(node))
    return [Item("\n".join(lines))]


# The two modes that render. `off` is absent by construction: no header ⇒ no text ⇒ the
# slot is bit-for-bit identical to a no-library ablation run (facade.py drops empty renders).
_BLOCK_LIBRARY_HEADERS: dict[str, str] = {
    "guidance": "PROMPT BLOCK LIBRARY (reuse one verbatim, adapt one, or write your own):",
    "restrict": ("PROMPT BLOCK LIBRARY (use only these — do not invent):"),
}


@signal(
    "prompt_block_catalogue",
    kind=InjectionKind.DERIVED,
    char_cap=None,
    citable=False,
)
def _r_prompt_block_catalogue(b: InjectionBundle) -> list[Item]:
    """The block library L1 picks from. ``restrict`` is a hard value space, ``guidance`` leaves it open. It
    falls back to general reasoning modules — NEVER silence, which shifts temp-0 generation to weaker mutations."""
    mode = b.prompt_block_catalogue
    header = _BLOCK_LIBRARY_HEADERS.get(mode)
    if header is None:
        return []
    library = (
        prompt_blocks() if mode == "restrict" else (b.earned_blocks or general_reasoning_blocks())
    )
    if not library:
        return []
    lines = [header]
    for field, blocks in library.items():
        lines.append(f"  {field}:")
        lines.extend(f"    - {text}" for text in blocks)
    return [Item("\n".join(lines))]


@signal(
    "l1_signal_catalogue",
    kind=InjectionKind.DERIVED,
    char_cap=None,
    citable=False,
)
def _r_l1_signal_catalogue(b: InjectionBundle) -> list[Item]:
    """ONLY what the wire schema cannot say. It listed all 18 signal names and nothing else — the
    values without the keys — so L2 supplied a shape: one fire keyed the map by slot with a
    ``<slot>_block`` value apiece, the next keyed it by SIGNAL with invented sub-selectors. Both
    reached ``validate_l1_layout`` as breaches and force-triggered L3 off L2's first fire. The slots
    and the signal enum now ride ``l1_layout``'s own schema (``layout_json_schema``), which states
    them AND is enforced where the provider honours it, so re-listing them here is a second copy of
    the half that is already covered. What no JSON Schema can express is a constraint ACROSS the four
    arrays — that is this panel's whole job now, and naming a COUNT of mandatory placeholders could
    never resolve without it: a count is not a vocabulary. The constraint binds the MERGED layout,
    which is what ``validate_l1_layout`` is handed — so it is a rule about what an edit may take
    AWAY, and stating it as one an edit must satisfy by itself is what asked L2 to restate a layout
    it was not changing."""
    mandatory = sorted(NODE_LAYOUTS["l1_generate"].mandatory)
    return [
        Item(
            "L1 LAYOUT — the response schema's `l1_layout` carries the legal slots and the signal "
            "enum; pick from there and invent nothing.\n"
            "  The one rule it cannot state: after your edit is applied, each of these must still sit "
            f"under SOME slot, or the whole edit rolls back —\n    {', '.join(mandatory)}"
        )
    ]
