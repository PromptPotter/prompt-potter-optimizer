"""The L1 wire schema the optimizer's own ``l1_generate`` call is answered against — EMISSION, not
validation. Its twin is ``schemas.py::build_l1_response_model``, called on the adjacent line in
``l1/generate.py``: one produces the JSON Schema sent to the provider, the other the Pydantic model
the reply is parsed with, and both derive their rename map from ``effective_l1_field_names`` here,
because a disagreement between them fails every parse of every round.

It sits in ``dispatch/`` rather than ``validators/`` because it neither REJECTS nor SCORES
(`../CLAUDE.md` § A validator either REJECTS or SCORES) — it composes a prompt surface, which is
this package's job. Its deterministic counterpart, the backstop for a provider that does not
honour the schema it was handed, is ``validators/l1_strict.py::validate_overrides``."""

from __future__ import annotations

import copy
from collections.abc import Sequence
from typing import Any, cast

from promptpotter.application.optimization.dispatch.bundle import (
    LAYOUT_SCHEMA_INSTRUCTION,
    OPTIMIZER_PROMPT_FIELD_MAX_CHARS,
    SCHEMA_DESCRIPTION_MAX_CHARS,
    SCHEMA_DESCRIPTIONS_INSTRUCTION,
    SCHEMA_RENAME_INSTRUCTION,
)
from promptpotter.application.optimization.dispatch.llm_call.prompts import resolve_node_override
from promptpotter.application.optimization.dispatch.schemas import L1GenerateOutput, L1Variant
from promptpotter.config.settings import PROMPT_STRING_FIELDS, TASK_CONTEXT_OVERRIDES
from promptpotter.domain.l1_layout import NODE_LAYOUTS, layout_json_schema
from promptpotter.domain.pipeline_schema import (
    NESTED_PARAM_TYPES,
    SCHEMA_DESCRIPTIONS_PARAM,
    SCHEMA_RENAME_PARAM,
    PipelineNode,
    PipelineSchema,
)

__all__ = [
    "build_l1_response_schema",
    "effective_l1_field_names",
]

# A ceiling is prompt text declared on a REWRITABLE PROMPT FIELD; an entry naming anything else
# declares a bound the `.get(param)` below can never fire on, so it reads as a live cap while
# binding nothing. The two tables are authored in two modules, and this is the one place both
# are in scope — so the subset is asserted here rather than pinned by a test.
assert set(OPTIMIZER_PROMPT_FIELD_MAX_CHARS) <= set(PROMPT_STRING_FIELDS), (
    "OPTIMIZER_PROMPT_FIELD_MAX_CHARS declares a ceiling on a non-prompt field: "
    f"{sorted(set(OPTIMIZER_PROMPT_FIELD_MAX_CHARS) - set(PROMPT_STRING_FIELDS))}"
)


def _inline_refs(node: Any, defs: dict[str, dict[str, Any]]) -> Any:
    """Provider ``response_format`` wants a self-contained schema, so Pydantic's ``$defs`` table is
    inlined in place; ``title`` metadata is stripped with it (auto-emitted, no LLM-side benefit)."""
    if isinstance(node, dict):
        if "$ref" in node:
            key = node["$ref"].split("/")[-1]
            return _inline_refs(copy.deepcopy(defs[key]), defs)
        return {k: _inline_refs(v, defs) for k, v in node.items() if k != "title"}
    if isinstance(node, list):
        return [_inline_refs(v, defs) for v in node]
    return node


def effective_l1_field_names() -> dict[str, str]:
    """The ONE source the emitted schema and the response model both derive from. Unconditional —
    gating on the INNER cycle's own config would emit a rename nothing applied, then score it."""
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
    """Each lever is keyed by a CLOSED set, so the optimizer can edit but never invent; ``None``
    where the node declares none."""
    if param == SCHEMA_DESCRIPTIONS_PARAM:
        out_schema = node.output_schema
        if out_schema is None or not out_schema.fields:
            return None
        return {
            "type": "object",
            "description": SCHEMA_DESCRIPTIONS_INSTRUCTION,
            # Bounded HERE, the only production site this prose has. It rides forward — the
            # winner's descriptions become the next round's parent — so an unbounded one
            # compounds exactly like `l3_plan.plan` did.
            "properties": {
                f: {"type": "string", "maxLength": SCHEMA_DESCRIPTION_MAX_CHARS}
                for f in out_schema.fields
            },
            "additionalProperties": False,
        }
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


def build_l1_response_schema(
    pipeline_schema: PipelineSchema,
    *,
    citable_fields: Sequence[str],
    schema_field_rename: bool = False,
    n_variants: int | None = None,
) -> dict[str, Any]:
    """Returns the BARE JSON Schema — ``chat()``'s ``response_schema`` IS the wire schema, and an
    envelope here nests it where the provider reads no ``type`` and every constraint goes inert."""
    raw_schema = L1GenerateOutput.model_json_schema()
    defs = raw_schema.pop("$defs", {})
    inlined = _inline_refs(raw_schema, defs)

    # The ceiling the prompt states, stated again where the decoder can enforce it. Without it
    # the only bound was `l1/generate.py`'s `variants_list[:n_variants]` slice — so an
    # over-generating model was BILLED for every extra variant and the overflow was then thrown
    # away. Per-round, like the citable enum below, because `n_variants` is per-round.
    if n_variants is not None:
        inlined["properties"]["variants"]["maxItems"] = n_variants

    variant_items = inlined["properties"]["variants"]["items"]
    variant_props = variant_items["properties"]

    # 1. pipeline_params_override — per-node tunables.
    pp_override = variant_props["pipeline_params_override"]
    pp_override.setdefault("properties", {})
    pp_override["additionalProperties"] = False
    pp_properties = pp_override["properties"]

    # The emittable per-node param surface is `node_param_keys()` — the ONE source
    # the catalogue + validator share. It omits model/provider entirely (the LLM
    # cannot emit a key the schema doesn't declare, so the lock needs no per-round
    # rejection).
    # No `model` branch here, deliberately. `node_param_keys()` has already stripped
    # model/provider via PARAM_FORBIDDEN_KEYS, so `keys` cannot contain them and a
    # `if param == "model": <emit available_models enum>` arm is unreachable. One stood
    # here anyway, which is worse than useless: it read as though the campaign's model
    # catalogue were an emittable axis, contradicting the lock the line above states.
    # The lock is structural — the LLM cannot emit a key the schema never declares.
    for node_name, keys in pipeline_schema.node_param_keys().items():
        node = pipeline_schema.get_node(node_name)
        if node is None:
            continue
        # Scalars first, then the nested params, each alphabetical. Field ORDER is what
        # this schema teaches (`docs/concepts/structured-output.md`), so the two groups
        # are emitted in a fixed sequence rather than one interleaved sort — the optimizer
        # levers read after the surface they act on.
        nested = {p for p in keys if node.param_types.get(p) in NESTED_PARAM_TYPES}
        param_props: dict[str, dict[str, Any]] = {}
        for param in sorted(keys - nested):
            allowed = node.param_allowed_values.get(param)
            declared_type = node.param_types.get(param)
            if allowed:
                param_props[param] = {"type": "string", "enum": list(allowed)}
            elif declared_type:
                param_props[param] = {"type": declared_type}
            else:
                param_props[param] = {}
            # Only on a node carrying a layout — an optimizer node, whose `instruction` is the
            # long-form artifact. Elsewhere the declaration is prompt text that never binds.
            ceiling = OPTIMIZER_PROMPT_FIELD_MAX_CHARS.get(param)
            if ceiling is not None and NODE_LAYOUTS.get(node.name) is not None:
                param_props[param]["maxLength"] = ceiling
        # The field-NAME lever is the strongest and the only one that can break a parser,
        # so the campaign must unlock it: dropped from the emitted schema when locked, and
        # the LLM cannot emit a key the schema omits. Structural, never policed per round.
        # Unlocked, the rename is a presentation transform — `build_l1_response_model`
        # aliases the wire key back onto the real field, so no downstream reader observes it.
        if not schema_field_rename:
            nested.discard(SCHEMA_RENAME_PARAM)
        for param in sorted(nested):
            prop = _nested_param_property(node, param)
            if prop is not None:
                param_props[param] = prop
        if not param_props:
            continue
        pp_properties[node_name] = {
            "type": "object",
            "properties": param_props,
            "additionalProperties": False,
        }

    # 2 + 3. The two OptSearchPoint slots — emitted only where the evolved prompt has a
    # node to land on. `to_job_search_point` gates its whole render on the same
    # `prompt_node_names()`, so with none the slots would be write-only.
    if pipeline_schema.prompt_node_names():
        pf_override = variant_props["prompt_fields_override"]
        pf_override["properties"] = {field: {"type": "string"} for field in PROMPT_STRING_FIELDS}
        pf_override["additionalProperties"] = False

        tc_override = variant_props["task_context_override"]
        tc_override["properties"] = {
            field: {"type": "string"} for field in sorted(TASK_CONTEXT_OVERRIDES)
        }
        tc_override["additionalProperties"] = False
    else:
        del variant_props["prompt_fields_override"]
        del variant_props["task_context_override"]

    # 4. evidence_grounding — the panels THIS round's prompt renders, and the one field this
    # schema makes STRICTER than its parse twin. The model stays optional on purpose (a provider
    # omitting the citation must not crash a whole round's variants), but the WIRE offered `null`
    # as a legal answer to a question the loop treats as mandatory, and 2 of 19 live rounds took
    # it — for every variant in the call, since one response is one decision. `citable_fields` is
    # never empty, so the object arm alone is always satisfiable.
    eg = variant_props["evidence_grounding"]
    object_arm = next(a for a in eg["anyOf"] if a.get("type") == "object")
    object_arm["properties"]["field"]["enum"] = list(citable_fields)
    # The null arm and the `default: null` beside it go together — either one alone still reads
    # as "you may skip this".
    variant_props["evidence_grounding"] = object_arm
    required = variant_items.setdefault("required", [])
    if "evidence_grounding" not in required:
        required.insert(0, "evidence_grounding")
    # Same split as above: tolerated missing at the parse boundary, never OFFERED as skippable.
    if "targets_cluster" not in required:
        required.append("targets_cluster")

    # 5. Rename LAST. `build_l1_response_model` aliases the same map back so no downstream
    # reader observes the wire name. (The `description` lever no longer touches THIS schema:
    # it rewrites each TARGET node's own `output_schema` at the wire seam
    # `OptSearchPoint.to_job_search_point`, keyed by that node's fields — the core case.)
    field_names = effective_l1_field_names()
    if field_names:
        _rename_variant_schema(variant_items, field_names)

    return cast("dict[str, Any]", inlined)
