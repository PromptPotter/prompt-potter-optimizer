"""Discoverability menus — pipeline-param search-space (L1 reads when proposing override) and the
cross-slot layout rule (L2 reads when authoring l1_layout; that field's own schema carries its
vocabulary). Load-bearing per architecture.md §0.5.
"""

from __future__ import annotations

from typing import Any

from promptpotter.application.optimization.dispatch.bundle import (
    AXES_ENUM_PREVIEW,
    InjectionBundle,
    InjectionKind,
    Item,
    signal,
)
from promptpotter.application.scoring.formula.matchers import extraction_note_for_scoring
from promptpotter.config.prompt_blocks import general_reasoning_blocks, prompt_blocks
from promptpotter.domain.l1_layout import NODE_LAYOUTS
from promptpotter.domain.pipeline_schema import (
    ANSWER_AS_JSON,
    ANSWER_AS_TEXT,
    OUTPUT_SCHEMA_KEY,
    SCHEMA_DESCRIPTION_PREFIX,
    SCHEMA_TOGGLE_PARAM,
    PipelineNode,
    described_field,
)


def _schema_description_block(
    node: PipelineNode, keys: list[str], current: dict[str, Any]
) -> list[str]:
    """The CURRENT prose under each open description key — without it the lever is offered blind.
    Read off the point being improved, whose folded schema carries what earlier winners wrote: the
    declaration shows the prose they replaced. An UNDESCRIBED field is marked, being the
    highest-value target."""
    schema = current.get(OUTPUT_SCHEMA_KEY) or (
        node.output_schema.json_schema if node.output_schema else None
    )
    lines = [
        f"    {SCHEMA_DESCRIPTION_PREFIX}<path> — current prose (rewrite what underspecifies):"
    ]
    for key in keys:
        path = key.removeprefix(SCHEMA_DESCRIPTION_PREFIX)
        prose = (described_field(schema, path) or {}).get("description")
        lines.append(f"      {path}: {prose or '(undescribed)'}")
    return lines


def _schema_toggle_block(formula: str | None) -> list[str]:
    """What the OTHER arm of the schema toggle costs. The enum preview says the two values exist
    and nothing else, and `param_descriptions` renders only for an axis with NO value space — so a
    precondition on an axis that has a menu reaches L1 through no channel but this.

    The matcher's contract is quoted verbatim so both moves land in ONE variant: split across two
    rounds, the first scores a mechanical zero and the loop charges structured output for an
    answer nothing could read."""
    note = extraction_note_for_scoring(formula or "")
    lines = [
        f"    {SCHEMA_TOGGLE_PARAM}={ANSWER_AS_TEXT} REMOVES the output schema from the call: "
        f"{SCHEMA_DESCRIPTION_PREFIX}* then reach nothing, and the answer has to be findable "
        f"in prose. Rewrite answer_format in the SAME variant."
    ]
    if note:
        lines.append(f"      the scorer reads: {note}")
    return lines


@signal(
    "pipeline_param_catalogue",
    kind=InjectionKind.DERIVED,
    char_cap=None,
    # A value-space menu is what a mutation may SAY, never why it should be made.
    citable=False,
)
def _r_pipeline_param_catalogue(b: InjectionBundle) -> list[Item]:
    """Pipeline-param menu (name + ≤4-value enum hint) — what L1 picks from for `pipeline_overlay`.
    Symmetric with `l1_signal_catalogue` (the menu L2 picks from for L1's layout).
    """
    schema = b.pipeline_schema
    if schema is None:
        return []
    # ONE surface: `provider`/`route_order` are always absent (cost levers, never an
    # optimizer axis), so the catalogue never advertises them. `model` appears exactly
    # where its node opened it, and always WITH its value space — a bare `model` with no
    # enum would be the one line in this menu inviting an invented id.
    npk = schema.node_param_keys()
    if not npk:
        return []
    lines = ["PIPELINE PARAM CATALOGUE (use only these — do not invent):"]
    for node_name, params in npk.items():
        node = schema.get_node(node_name)
        if not node or not params:
            continue
        descs = node.param_descriptions
        # Listed under the node with their current prose, never as bare names on its line.
        described = [k for k in node.description_keys if k in params]
        bits: list[str] = []
        for p in sorted(params - set(described)):
            allowed = schema.param_options(node, p)
            # `[]` is a declared axis with nothing legal left, and the wire schema emits no
            # property for it — so listing it here would advertise a mutation L1 cannot make.
            if allowed is not None and not allowed:
                continue
            if allowed:
                shown = list(allowed)[:AXES_ENUM_PREVIEW]
                preview = ", ".join(str(x) for x in shown)
                if len(allowed) > AXES_ENUM_PREVIEW:
                    preview += f", … (+{len(allowed) - AXES_ENUM_PREVIEW})"
                # Indistinct rungs stay on the menu; L1 is told that moving between them is not a
                # mutation, or a variant re-measures the configuration it started from.
                if same := schema.param_indistinct(node, p):
                    preview += f"; {'='.join(same)} identical here"
                bits.append(f"{p} [{preview}]")
            elif desc := descs.get(p):
                bits.append(f"{p} ({desc[:40]})")
            else:
                bits.append(p)
        # Every axis skipped leaves the node with nothing to offer, and a bare `name:` in the
        # menu reads as an axis whose values went missing rather than as a node with none.
        if not bits and not described:
            continue
        lines.append(f"  {node_name}: {', '.join(bits)}".rstrip())
        if described:
            current = b.cycle_slice.pipeline_params.get(node_name) or {}
            lines.extend(_schema_description_block(node, described, current))
        # Only where the other arm is actually reachable: a node pinned to one value has no
        # trade to explain, and printing the cost of a move nobody can make is prompt mass.
        if ANSWER_AS_JSON in (schema.param_options(node, SCHEMA_TOGGLE_PARAM) or ()):
            lines.extend(_schema_toggle_block(b.cycle_slice.composite_formula))
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
