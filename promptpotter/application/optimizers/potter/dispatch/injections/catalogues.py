from __future__ import annotations

from typing import Any

from promptpotter.application.optimizers.potter.dispatch.bundle import (
    ANSWER_LABEL_STEM,
    AXES_ENUM_PREVIEW,
    DEMO_POOL_RENDER_CAP,
    DEMO_QUERY_STEM,
    InjectionBundle,
    InjectionKind,
    Item,
    signal,
)
from promptpotter.application.optimizers.potter.dispatch.layout import NODE_LAYOUTS
from promptpotter.application.scoring.formula.matchers import extraction_note_for_scoring
from promptpotter.config.prompt_blocks import general_reasoning_blocks, prompt_blocks
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
    """Read off the point being improved, never the declaration, which shows the prose earlier winners replaced."""
    schema = current.get(OUTPUT_SCHEMA_KEY) or (
        node.output_schema.json_schema if node.output_schema else None
    )
    lines = [
        f"    {SCHEMA_DESCRIPTION_PREFIX}<path> — sent with the node's prompt; rewrite any your "
        "edit contradicts:"
    ]
    for key in keys:
        path = key.removeprefix(SCHEMA_DESCRIPTION_PREFIX)
        prose = (described_field(schema, path) or {}).get("description")
        lines.append(f"      {path}: {prose or '(undescribed)'}")
    return lines


def _schema_toggle_block(formula: str | None) -> list[str]:
    """The only channel a precondition on an axis WITH a menu reaches L1; split across two rounds, the first scores a mechanical zero."""
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
    schema = b.pipeline_schema
    if schema is None:
        return []
    npk = schema.node_param_keys()
    if not npk:
        return []
    lines = ["PIPELINE PARAM CATALOGUE:"]
    for node_name, params in npk.items():
        node = schema.get_node(node_name)
        if not node or not params:
            continue
        descs = node.param_descriptions
        described = [k for k in node.description_keys if k in params]
        bits: list[str] = []
        for p in sorted(params - set(described)):
            allowed = schema.param_options(node, p)
            # `[]` emits no wire property, so listing it would advertise a mutation L1 cannot make.
            if allowed is not None and not allowed:
                continue
            if allowed:
                shown = list(allowed)[:AXES_ENUM_PREVIEW]
                preview = ", ".join(str(x) for x in shown)
                if len(allowed) > AXES_ENUM_PREVIEW:
                    preview += f", … (+{len(allowed) - AXES_ENUM_PREVIEW})"
                # Untold, a variant moving between indistinct rungs re-measures the configuration it started from.
                if same := schema.param_indistinct(node, p):
                    preview += f"; {'='.join(same)} identical here"
                bits.append(f"{p} [{preview}]")
            elif desc := descs.get(p):
                bits.append(f"{p} ({desc[:40]})")
            else:
                bits.append(p)
        # A bare `name:` reads as an axis whose values went missing.
        if not bits and not described:
            continue
        lines.append(f"  {node_name}: {', '.join(bits)}".rstrip())
        if described:
            current = b.cycle_slice.pipeline_params.get(node_name) or {}
            lines.extend(_schema_description_block(node, described, current))
        # Only where the other arm is reachable: the cost of a move nobody can make is prompt mass.
        if ANSWER_AS_JSON in (schema.param_options(node, SCHEMA_TOGGLE_PARAM) or ()):
            lines.extend(_schema_toggle_block(b.cycle_slice.composite_formula))
    return [Item("\n".join(lines))]


# `off` is absent by construction, so the slot is bit-for-bit a no-library ablation run.
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
    """Falls back to general reasoning modules, NEVER silence, which shifts temp-0 generation to weaker mutations."""
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
    "demo_pool",
    kind=InjectionKind.DERIVED,
    char_cap=None,
    citable=False,
)
def _r_demo_pool(b: InjectionBundle) -> list[Item]:
    if not b.offers_shots:
        return []
    rows = {s.id: s for s in b.demo_pool}
    current = [rows[i] for i in b.opt_sp.shot_ids]
    others = [s for s in b.demo_pool if s.id not in set(b.opt_sp.shot_ids)]
    k = min(DEMO_POOL_RENDER_CAP, len(others))
    start = (b.cycle_slice.round_num - 1) * k % len(others) if others else 0
    window = (others[start:] + others[:start])[:k]
    shown = ", ".join(f"#{s.id}" for s in current) or "none"
    header = (
        "DEMO POOL — rows held out as shots, never scored. `shot_ids` REPLACES the parent's shots "
        f"with the ids you list, in order, at most {b.shot_k_max}; omit it to keep them. Parent's "
        f"shots: {shown}. Below: those, then {len(window)} of the {len(others)} others."
    )
    return [
        Item(header),
        *(
            Item(
                f"#{s.id} {' '.join(s.query.split())[:DEMO_QUERY_STEM]} -> "
                f"{str(s.ground_truth)[:ANSWER_LABEL_STEM]}",
                trusted=False,
            )
            for s in (*current, *window)
        ),
    ]


@signal(
    "l1_signal_catalogue",
    kind=InjectionKind.DERIVED,
    char_cap=None,
    citable=False,
)
def _r_l1_signal_catalogue(b: InjectionBundle) -> list[Item]:
    """ONLY what the wire schema cannot say: a constraint ACROSS the slots, binding the MERGED layout."""
    mandatory = sorted(NODE_LAYOUTS["l1_generate"].mandatory - b.silent_l1_panels)
    return [
        Item(
            "L1 LAYOUT — the response schema's `l1_layout` carries the legal slots and the signal "
            "enum; pick from there and invent nothing.\n"
            "  The one rule it cannot state: after your edit is applied, each of these must still sit "
            f"under SOME slot, or the whole edit rolls back —\n    {', '.join(mandatory)}"
        )
    ]
