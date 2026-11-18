"""EMISSION, not validation; the parse twin is ``schemas.py::build_l1_response_model``, and a rename map the two disagree on fails every parse."""

from __future__ import annotations

from collections.abc import Collection, Mapping, Sequence
from typing import Any, cast

from promptpotter.application.bench.llm_call import wire_closed
from promptpotter.application.optimizers.potter.dispatch.bundle import (
    BLOCK_ID_INSTRUCTION,
    LAYOUT_SCHEMA_INSTRUCTION,
    LENGTH_BUDGET_TEXT,
    SCHEMA_DESCRIPTION_MAX_CHARS,
    SCHEMA_DESCRIPTIONS_INSTRUCTION,
    SCHEMA_RENAME_INSTRUCTION,
    RecombinationOffer,
    offers_param,
)
from promptpotter.application.optimizers.potter.dispatch.layout import (
    NODE_LAYOUTS,
    layout_json_schema,
)
from promptpotter.application.optimizers.potter.dispatch.schemas import (
    L1GenerateOutput,
    L1Variant,
    recombinations_schema,
)
from promptpotter.domain.opt_search_point import Locus
from promptpotter.domain.pipeline_schema import (
    NESTED_PARAM_TYPES,
    SCHEMA_RENAME_PARAM,
    PipelineNode,
    PipelineSchema,
)


def _inline_refs(node: Any, defs: dict[str, dict[str, Any]]) -> Any:
    """Provider ``response_format`` wants a self-contained schema; the answer shares no dict or list with its inputs."""
    if isinstance(node, dict):
        if "$ref" in node:
            key = node["$ref"].split("/")[-1]
            return _inline_refs(defs[key], defs)
        return {k: _inline_refs(v, defs) for k, v in node.items() if k != "title"}
    if isinstance(node, list):
        return [_inline_refs(v, defs) for v in node]
    return node


def _arm(slot: dict[str, Any]) -> dict[str, Any]:
    return cast("dict[str, Any]", next(a for a in slot["anyOf"] if a.get("type") != "null"))


def _said(slot: dict[str, Any]) -> dict[str, Any]:
    """The slot's non-null arm alone, its description kept."""
    return {**_arm(slot), **{k: slot[k] for k in ("description",) if k in slot}}


def effective_l1_field_names(levers: Mapping[str, Any]) -> dict[str, str]:
    """Unconditional: gating on the INNER cycle's own config would emit a rename nothing applied, then score it."""
    return dict(levers.get(SCHEMA_RENAME_PARAM, {}))


def _rename_variant_schema(variant: dict[str, Any], field_names: Mapping[str, str]) -> None:
    props = variant["properties"]
    variant["properties"] = {field_names.get(k, k): v for k, v in props.items()}
    required = variant.get("required")
    if isinstance(required, list):
        variant["required"] = [field_names.get(k, k) for k in required]


def _nested_param_property(node: PipelineNode, param: str) -> dict[str, Any] | None:
    """Each lever is keyed by a CLOSED set, so the optimizer can edit but never invent."""
    if param == "layout":
        spec = NODE_LAYOUTS.get(node.name)
        return (
            None
            if spec is None
            else layout_json_schema(spec, description=LAYOUT_SCHEMA_INSTRUCTION)
        )
    if param == SCHEMA_RENAME_PARAM and NODE_LAYOUTS.get(node.name) is not None:
        return {
            "type": "object",
            "description": SCHEMA_RENAME_INSTRUCTION,
            "properties": {f: {"type": "string"} for f in L1Variant.model_fields},
            "additionalProperties": False,
        }
    return None


def _pooled_edit(props: dict[str, Any], locus: str, block_ids: Sequence[str]) -> dict[str, Any]:
    """A whole-field replace by an offered id: `op` and `anchor` are pinned, the id alone decides."""
    return {
        **props,
        "at": {**props["at"], "enum": [locus]},
        "op": {**props["op"], "enum": ["replace"]},
        "anchor": {**props["anchor"], "enum": [""]},
        "text": {**props["text"], "enum": list(block_ids)},
    }


# A slot whose panel produced nothing is withdrawn: L1 cannot fill a value space it was not shown.
_SLOT_PANEL: dict[str, str] = {
    "edits": "rendered_prompt",
    "pipeline_overlay": "pipeline_param_catalogue",
    "shots": "demo_pool",
}


def build_l1_response_schema(
    pipeline_schema: PipelineSchema,
    *,
    silent_panels: Collection[str] = (),
    schema_field_rename: bool = False,
    n_variants: int | None = None,
    pooled_fields: Mapping[str, Sequence[str]] | None = None,
    recombination: RecombinationOffer | None = None,
    field_names: Mapping[str, str],
    length_budget: int,
) -> dict[str, Any]:
    """Returns the BARE schema: an envelope nests it where the provider reads no ``type`` and every constraint goes inert.
    ``None`` *recombination* withdraws the slot."""
    raw_schema = L1GenerateOutput.model_json_schema()
    defs = raw_schema.pop("$defs", {})
    inlined = _inline_refs(raw_schema, defs)

    # Without `maxItems` an over-generating model is BILLED for every variant the slice then discards.
    if n_variants is not None:
        inlined["properties"]["variants"].update(minItems=n_variants, maxItems=n_variants)
    if recombination is None:
        del inlined["properties"]["recombinations"]
    else:
        slot = inlined["properties"]["recombinations"]
        slot["anyOf"] = [recombinations_schema(recombination), {"type": "null"}]

    variant_items = inlined["properties"]["variants"]["items"]
    variant_props = variant_items["properties"]

    pp_properties = _arm(variant_props["pipeline_overlay"])["properties"]

    # The nodes the GRAPH reaches, never the chain alone: an off-chain escalation node is what L4 tunes.
    # No view means no projection to narrow by, never "narrow to nothing".
    reachable = {n.id for n in pipeline_schema.view.nodes} if pipeline_schema.view else None
    for node_name, keys in pipeline_schema.node_param_keys().items():
        node = pipeline_schema.get_node(node_name)
        if node is None or (reachable is not None and node_name not in reachable):
            continue
        keys = {
            p
            for p in keys
            if offers_param(pipeline_schema, node, p, schema_field_rename=schema_field_rename)
        }
        # Field ORDER is what this schema teaches, so the groups emit in a fixed sequence, never one sort.
        nested = {p for p in keys if node.param_types.get(p) in NESTED_PARAM_TYPES}
        described = [k for k in node.description_keys if k in keys]
        param_props: dict[str, dict[str, Any]] = {}
        for param in sorted(keys - nested - set(described)):
            if allowed := pipeline_schema.param_options(node, param):
                param_props[param] = {"type": "string", "enum": list(allowed)}
            else:
                param_props[param] = {"type": node.param_types[param]}
        for param in sorted(nested):
            prop = _nested_param_property(node, param)
            if prop is not None:
                param_props[param] = prop
        # Stated, never a `maxLength`: `validators.py::l1_prose_within_ceiling` rejects past it.
        for key in described:
            param_props[key] = {
                "type": "string",
                "description": f"At most {SCHEMA_DESCRIPTION_MAX_CHARS} characters.",
            }
        assert param_props.keys() <= keys, (
            f"{node_name}: the schema offers a key the node declares no param for"
        )
        if not param_props:
            continue
        pp_properties[node_name] = {
            "type": "object",
            **({"description": SCHEMA_DESCRIPTIONS_INSTRUCTION} if described else {}),
            "properties": param_props,
            "additionalProperties": False,
        }

    # The roster's prompt nodes, each by ITS open fields; under `closed` only a field the pool holds.
    prompt_nodes = pipeline_schema.prompt_nodes()
    loci = {
        Locus(n.name, f).name: f
        for n in prompt_nodes
        for f in n.open_prompt_fields
        if pooled_fields is None or pooled_fields.get(f)
    }
    # STRICTER than the parse twin on purpose: tolerated missing at the parse boundary, never OFFERED as `null`.
    if loci:
        # Stated, never a `maxLength`: `children.py::length_failures` refuses past it at the mint.
        stated = LENGTH_BUDGET_TEXT.format(length_budget)
        edits = variant_props["edits"] = {
            **_said(variant_props["edits"]),
            "minItems": 1,
            "description": stated,
        }
        edit = edits["items"]
        if pooled_fields is None:
            edit["properties"]["at"]["enum"] = list(loci)
        else:
            # One branch per locus: a block id is legal only for the field whose pool lists it.
            edits["items"] = {
                "anyOf": [
                    {
                        **edit,
                        "properties": _pooled_edit(edit["properties"], locus, pooled_fields[f]),
                    }
                    for locus, f in loci.items()
                ]
            }
            edits["description"] = f"{BLOCK_ID_INSTRUCTION} {stated}"
    else:
        del variant_props["edits"]
    if prompt_nodes:
        shot_list = {"type": "array", "items": {"type": "integer"}}
        _arm(variant_props["shots"])["properties"] = {node.name: shot_list for node in prompt_nodes}
    else:
        del variant_props["shots"]

    for slot, panel in _SLOT_PANEL.items():
        if panel in silent_panels:
            variant_props.pop(slot, None)

    variant_props["evidence_grounding"] = _said(variant_props["evidence_grounding"])
    variant_props["targets_rows"] = _said(variant_props["targets_rows"])

    # Rename LAST: every key above is addressed by its real field name.
    if field_names:
        _rename_variant_schema(variant_items, field_names)

    return wire_closed(inlined)
