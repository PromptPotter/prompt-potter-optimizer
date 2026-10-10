"""EMISSION, not validation; the parse twin is ``schemas.py::build_l1_response_model``, and a rename map the two disagree on fails every parse."""

from __future__ import annotations

from collections.abc import Collection, Sequence
from typing import TYPE_CHECKING, Any, cast

from promptpotter.application.optimizer_manifest import resolve_node_override
from promptpotter.application.optimizers.potter.dispatch.bundle import (
    LAYOUT_SCHEMA_INSTRUCTION,
    OPTIMIZER_PROMPT_FIELD_MAX_CHARS,
    SCHEMA_DESCRIPTION_MAX_CHARS,
    SCHEMA_DESCRIPTIONS_INSTRUCTION,
    SCHEMA_RENAME_INSTRUCTION,
)
from promptpotter.application.optimizers.potter.dispatch.layout import (
    NODE_LAYOUTS,
    layout_json_schema,
)
from promptpotter.application.optimizers.potter.dispatch.schemas import L1GenerateOutput, L1Variant
from promptpotter.domain.pipeline_schema import (
    NESTED_PARAM_TYPES,
    SCHEMA_RENAME_PARAM,
    PipelineNode,
    PipelineSchema,
)
from promptpotter.domain.search_point import PROMPT_STRING_FIELDS
from promptpotter.shared.hashing import shapes_optimizer_prompt

if TYPE_CHECKING:
    from promptpotter.application.optimizer_manifest import SelectedOptimizer

shapes_optimizer_prompt(__name__)

__all__ = [
    "build_l1_response_schema",
    "effective_l1_field_names",
]

# A ceiling on a non-prompt field reads as a live cap while binding nothing.
assert set(OPTIMIZER_PROMPT_FIELD_MAX_CHARS) <= set(PROMPT_STRING_FIELDS), (
    "OPTIMIZER_PROMPT_FIELD_MAX_CHARS declares a ceiling on a non-prompt field: "
    f"{sorted(set(OPTIMIZER_PROMPT_FIELD_MAX_CHARS) - set(PROMPT_STRING_FIELDS))}"
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


def effective_l1_field_names() -> dict[str, str]:
    """Unconditional: gating on the INNER cycle's own config would emit a rename nothing applied, then score it."""
    proposed = resolve_node_override("l1_generate").schema_field_names
    if not proposed:
        return {}
    survivors = set(L1Variant.model_fields) - set(proposed)
    return {f: w for f, w in proposed.items() if f in L1Variant.model_fields and w not in survivors}


def _rename_variant_schema(variant: dict[str, Any], field_names: dict[str, str]) -> None:
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


# A slot whose panel produced nothing is withdrawn (`optimizers/potter/CLAUDE.md` § L1).
_SLOT_PANEL: dict[str, str] = {
    "prompt_fields_updates": "rendered_prompt",
    "pipeline_overlay": "pipeline_param_catalogue",
    "shot_ids": "demo_pool",
}


def build_l1_response_schema(
    pipeline_schema: PipelineSchema,
    *,
    citable_fields: Sequence[str],
    inner_optimizer: SelectedOptimizer | None,
    silent_panels: Collection[str] = (),
    schema_field_rename: bool = False,
    n_variants: int | None = None,
) -> dict[str, Any]:
    """Returns the BARE schema: an envelope nests it where the provider reads no ``type`` and every constraint goes inert."""
    raw_schema = L1GenerateOutput.model_json_schema()
    defs = raw_schema.pop("$defs", {})
    inlined = _inline_refs(raw_schema, defs)

    # Without `maxItems` an over-generating model is BILLED for every variant the slice then discards.
    if n_variants is not None:
        inlined["properties"]["variants"]["maxItems"] = n_variants

    variant_items = inlined["properties"]["variants"]["items"]
    variant_props = variant_items["properties"]

    pipeline_overlay = variant_props["pipeline_overlay"]
    pipeline_overlay.setdefault("properties", {})
    pipeline_overlay["additionalProperties"] = False
    pp_properties = pipeline_overlay["properties"]

    # The nodes the GRAPH reaches, never `active_steps`: an off-chain escalation node is what L4 tunes.
    # No view means no projection to narrow by, never "narrow to nothing".
    reachable = {n.id for n in pipeline_schema.view.nodes} if pipeline_schema.view else None
    for node_name, keys in pipeline_schema.node_param_keys().items():
        node = pipeline_schema.get_node(node_name)
        if node is None or (reachable is not None and node_name not in reachable):
            continue
        # Field ORDER is what this schema teaches, so the groups emit in a fixed sequence, never one sort.
        nested = {p for p in keys if node.param_types.get(p) in NESTED_PARAM_TYPES}
        described = [k for k in node.description_keys if k in keys]
        param_props: dict[str, dict[str, Any]] = {}
        for param in sorted(keys - nested - set(described)):
            allowed = pipeline_schema.param_options(node, param)
            declared_type = node.param_types.get(param)
            # `[]` (declared, nothing legal) is not `None` (no space declared): the first emits no property.
            if allowed is not None and not allowed:
                continue
            if allowed:
                param_props[param] = {"type": "string", "enum": list(allowed)}
            elif declared_type:
                param_props[param] = {"type": declared_type}
            else:
                param_props[param] = {}
            # Only on the recursion; elsewhere the declaration is prompt text that never binds.
            ceiling = OPTIMIZER_PROMPT_FIELD_MAX_CHARS.get(param)
            if ceiling is not None and inner_optimizer is not None:
                param_props[param]["maxLength"] = ceiling
        # The one lever that can break a parser: locked, it is absent from the schema, never policed per round.
        if not schema_field_rename:
            nested.discard(SCHEMA_RENAME_PARAM)
        for param in sorted(nested):
            prop = _nested_param_property(node, param)
            if prop is not None:
                param_props[param] = prop
        # Bounded HERE: the winner's descriptions become the next round's parent, so an unbounded one compounds.
        for key in described:
            param_props[key] = {"type": "string", "maxLength": SCHEMA_DESCRIPTION_MAX_CHARS}
        if not param_props:
            continue
        pp_properties[node_name] = {
            "type": "object",
            **({"description": SCHEMA_DESCRIPTIONS_INSTRUCTION} if described else {}),
            "properties": param_props,
            "additionalProperties": False,
        }

    # With no open field the slot would be write-only; `minLength` because "" is not an edit.
    if open_fields := pipeline_schema.open_prompt_fields():
        variant_props["prompt_fields_updates"].update(
            properties={field: {"type": "string", "minLength": 1} for field in open_fields},
            additionalProperties=False,
        )
    else:
        del variant_props["prompt_fields_updates"]
    # No `null` arm: omitting the key already keeps the parent's shots.
    shots = variant_props["shot_ids"]
    if pipeline_schema.prompt_node_names():
        array_arm = next(a for a in shots["anyOf"] if a.get("type") == "array")
        variant_props["shot_ids"] = {**array_arm, "description": shots["description"]}
    else:
        del variant_props["shot_ids"]

    for slot, panel in _SLOT_PANEL.items():
        if panel in silent_panels:
            variant_props.pop(slot, None)

    # STRICTER than the parse twin on purpose: tolerated missing at the parse boundary, never OFFERED as `null`.
    eg = variant_props["evidence_grounding"]
    object_arm = next(a for a in eg["anyOf"] if a.get("type") == "object")
    object_arm["properties"]["field"]["enum"] = list(citable_fields)
    # The null arm and its `default: null` go together: either alone still reads as skippable.
    variant_props["evidence_grounding"] = object_arm
    required = variant_items.setdefault("required", [])
    if "evidence_grounding" not in required:
        required.insert(0, "evidence_grounding")
    if "targets_cluster" not in required:
        required.append("targets_cluster")

    # Rename LAST: every key above is addressed by its real field name.
    field_names = effective_l1_field_names()
    if field_names:
        _rename_variant_schema(variant_items, field_names)

    return cast("dict[str, Any]", inlined)
