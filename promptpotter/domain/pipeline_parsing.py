from __future__ import annotations

import copy
import logging
from typing import Any

from promptpotter.config.settings import WELL_KNOWN_PARAM_TYPES
from promptpotter.domain.pipeline_schema import (
    SCHEMA_DESCRIPTIONS_PARAM,
    NodeOutputSchema,
    NodePromptInfo,
    ObservationMapping,
    PipelineNode,
    PipelineSchema,
    PipelineView,
    PipelineViewEdge,
    PipelineViewNode,
)

logger = logging.getLogger(__name__)

__all__ = [
    "derive_pipeline_view",
    "merge_node_blocks",
    "parse_pipeline_response",
    "parse_resolved_schema",
    "strip_lone_surrogates",
]

# The node-definition sub-blocks a partial overlay AUGMENTS rather than replaces.
# Every other key in a node block replaces wholesale — `output_schema` above all,
# because a shallow-merged schema can keep a `required` entry naming a field the
# incoming `properties` just dropped, and the backend rejects that.
_MERGED_NODE_SUB_BLOCKS = ("config", "optimizer")


def merge_node_blocks(base: dict[str, Any], overlay: dict[str, Any]) -> dict[str, Any]:
    """Layers node DEFINITIONS — one level above ``application.pipeline_resolve.apply_node_overlay``,
    which merges ``pipeline_params``. Only :data:`_MERGED_NODE_SUB_BLOCKS` merge by name."""
    out = copy.deepcopy(base)
    for node_name, node_def in overlay.items():
        if not isinstance(node_def, dict):
            continue
        dst = out.setdefault(node_name, {})
        for key, val in node_def.items():
            if key in _MERGED_NODE_SUB_BLOCKS and isinstance(val, dict):
                dst.setdefault(key, {}).update(val)
            else:
                dst[key] = val
    return out


def strip_lone_surrogates(obj: Any) -> Any:
    """A lone surrogate is a valid Python codepoint that raises ``UnicodeEncodeError`` at the
    wire, so overlays are scrubbed at parse time and the schema is born wire-safe."""
    if isinstance(obj, str):
        return obj.encode("utf-8", errors="replace").decode("utf-8")
    if isinstance(obj, dict):
        return {k: strip_lone_surrogates(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [strip_lone_surrogates(v) for v in obj]
    return obj


def _derive_node_kind(node: PipelineNode | None) -> str:
    """Cache role wins — a hit short-circuits the pipeline — then :attr:`PipelineNode.runs_llm`,
    which the model-axis carrier also reads, so kind and carrier cannot disagree."""
    if node is None:
        return "tool"
    if str(node.node_type) == "cache":
        return "cache"
    if node.runs_llm:
        return "llm"
    if node.wire_type == "retriever":
        return "retriever"
    if node.wire_type == "tool":
        return "tool"
    return "tool"


def derive_pipeline_view(schema: PipelineSchema) -> PipelineView:
    interior_ids = list(schema.active_steps)
    nodes: list[PipelineViewNode] = [
        PipelineViewNode(id="input", label="Input", kind="io"),
    ]
    for name in interior_ids:
        node = schema.get_node(name)
        nodes.append(PipelineViewNode(id=name, label=name, kind=_derive_node_kind(node)))
    nodes.append(PipelineViewNode(id="output", label="Output", kind="io"))

    sequence = ["input", *interior_ids, "output"]
    edges = [
        PipelineViewEdge.model_validate({"from": sequence[i], "to": sequence[i + 1]})
        for i in range(len(sequence) - 1)
    ]
    return PipelineView(nodes=nodes, edges=edges)


def parse_resolved_schema(resolved: dict[str, Any]) -> NodeOutputSchema:
    """``fields`` CONSTRAINS ``json_schema`` — it must name exactly its properties, and its
    order is the generation order. An empty ``description`` is a searchpoint, not an absence."""
    json_schema = resolved.get("json_schema", {})
    props = json_schema.get("properties", {})
    fields = resolved.get("fields") or list(props)
    if props and set(fields) != set(props):
        raise ValueError(
            f"output schema `fields` {fields} does not name exactly the schema's "
            f"properties {list(props)} — the order declaration must cover the schema"
        )
    if props and list(props) != fields:
        props = {f: props[f] for f in fields}
        json_schema = {**json_schema, "properties": props}
    field_descriptions = {
        k: v["description"] for k, v in props.items() if isinstance(v, dict) and "description" in v
    }
    return NodeOutputSchema(
        fields=fields,
        field_descriptions=field_descriptions,
        json_schema=json_schema,
    )


def _parse_resolved_prompt(resolved: dict[str, Any]) -> NodePromptInfo:
    return NodePromptInfo(
        template_variables=resolved.get("template_variables", []),
    )


def _extract_resolved_metadata(
    config: dict[str, Any],
) -> dict[str, dict[str, Any]]:
    schemas_raw = config.get("resolved_schemas", {})
    prompts_raw = config.get("resolved_prompts", {})

    nodes = config.get("nodes", {})
    metadata: dict[str, dict[str, Any]] = {}

    for node_name, node_data in nodes.items():
        nc = node_data.get("config", {})
        entry: dict[str, Any] = {}

        if sf := nc.get("schema_family"):
            sv = nc.get("schema_version")
            key = f"{sf}/{sv}" if sv is not None else sf
            if key in schemas_raw:
                entry["output_schema"] = parse_resolved_schema(schemas_raw[key])

        if pf := nc.get("prompt_family"):
            pv = nc.get("prompt_version")
            key = f"{pf}/{pv}" if pv is not None else pf
            if key in prompts_raw:
                entry["prompt_info"] = _parse_resolved_prompt(prompts_raw[key])

        if entry:
            metadata[node_name] = entry

    return metadata


# Order matters: `isinstance(True, int)` is True, so bool must be matched first.
_PY_TO_JSON_TYPE: dict[type, str] = {
    bool: "boolean",
    int: "integer",
    float: "number",
    str: "string",
    dict: "object",
    list: "array",
}


def _infer_param_types(opt: dict[str, Any], node_config: dict[str, Any]) -> dict[str, str]:
    """Resolves every key the node carries, not only the tunable ``param_keys`` — the steer panel
    bundles the config-only ones too, so their widget kind must resolve."""
    declared: dict[str, str] = dict(opt.get("param_types") or {})
    param_keys = list(opt.get("param_keys") or [])
    config_only = [k for k in node_config if k not in param_keys]
    for param in (*param_keys, *config_only):
        if param in declared:
            continue
        if param in WELL_KNOWN_PARAM_TYPES:
            declared[param] = WELL_KNOWN_PARAM_TYPES[param]
            continue
        if param in node_config:
            val = node_config[param]
            for py_type, json_type in _PY_TO_JSON_TYPE.items():
                if isinstance(val, py_type):
                    declared[param] = json_type
                    break
    return declared


def parse_pipeline_response(data: dict[str, Any]) -> PipelineSchema:
    if not data:
        logger.warning("Empty pipeline response; returning empty schema")
        return PipelineSchema()

    data = strip_lone_surrogates(data)
    config = data.get("data", data)
    config = config.get("config", config)

    nodes = config.get("nodes", {})
    resolved_metadata = _extract_resolved_metadata(config)

    # Step order from pipelines.default, fallback to nodes dict order
    step_order = config.get("pipelines", {}).get("default", list(nodes.keys()))

    steps: list[PipelineNode] = []
    for name in step_order:
        node = nodes.get(name, {})
        if not node:
            continue
        opt = node.get("optimizer", {})
        nc = node.get("config", {})
        pk = set(opt.get("param_keys", []))

        step_kwargs: dict[str, Any] = {
            "name": name,
            "wire_type": node.get("type", ""),
            "node_type": node.get("node_role", ""),
            "param_keys": pk,
            "param_descriptions": opt.get("param_descriptions", {}),
            "param_allowed_values": opt.get("param_allowed_values", {}),
            "param_types": _infer_param_types(opt, nc),
            "langfuse_type": opt.get("langfuse_type", "span"),
            "current_config": dict(nc),
        }

        # Observation mappings
        obs_name = opt.get("observation_name")
        if obs_name:
            step_kwargs["observation_name"] = obs_name
        obs_raw = opt.get("observation_mappings", [])
        if obs_raw:
            step_kwargs["observation_mappings"] = [ObservationMapping(**m) for m in obs_raw]

        # Merge resolved registry metadata
        rm = resolved_metadata.get(name, {})
        if "output_schema" in rm:
            step_kwargs["output_schema"] = rm["output_schema"]
        if "prompt_info" in rm:
            step_kwargs["prompt_info"] = rm["prompt_info"]

        # Inline prompt_info (for static pipeline.yaml without resolved_prompts)
        if "prompt_info" not in step_kwargs and "prompt_info" in node:
            step_kwargs["prompt_info"] = NodePromptInfo(**node["prompt_info"])

        # Inline output_schema — the schema the WIRE already carries. A node that declares
        # its structured output on `config.output_schema` (rather than via a
        # `schema_family` registry entry) gets the same read-model, from the same parser,
        # off the same declaration the connector forwards to the backend. One schema, not
        # a display copy beside a wire copy — which is why it is read from `config` and
        # why `SCHEMA_OWNED_FIELDS` locks the optimizer out of it.
        if "output_schema" not in step_kwargs and isinstance(nc.get("output_schema"), dict):
            step_kwargs["output_schema"] = parse_resolved_schema(
                {"json_schema": nc["output_schema"]}
            )
            # `answer_field` names which slot IS the answer. Checked at LOAD, before one
            # call is paid for: an executor destructuring a field the schema never declares
            # reads "" for every sample, and the whole run grades NO_RESULT with nothing
            # but a floor score to say why.
            answer_field = nc.get("answer_field")
            props = nc["output_schema"].get("properties") or {}
            if answer_field is not None and answer_field not in props:
                raise ValueError(
                    f"node {name!r}: answer_field {answer_field!r} is not a property of its "
                    f"output_schema (have: {sorted(props)})"
                )

        # Synthesize the always-on `description` lever onto any node that ships an
        # `output_schema` with fields — schema-driven, never a per-dataset `param_keys`
        # opt-in. The field NAME stays locked (`SCHEMA_OWNED_FIELDS`); only the free
        # prose becomes tunable. Declared as an `object` param so the one nesting
        # contract (`apply_node_overlay` merges one level, `build_l1_response_schema`
        # emits the sub-schema, `validate_overrides` type-checks it) applies with no
        # special case downstream — the same shape a hand-declared nested param has.
        out_schema = step_kwargs.get("output_schema")
        if out_schema is not None and out_schema.fields:
            step_kwargs["param_keys"] = pk | {SCHEMA_DESCRIPTIONS_PARAM}
            step_kwargs["param_types"] = {
                **step_kwargs["param_types"],
                SCHEMA_DESCRIPTIONS_PARAM: "object",
            }

        steps.append(PipelineNode(**step_kwargs))

    logger.info(
        "Parsed pipeline '%s' with %d steps",
        config.get("name", "unknown"),
        len(steps),
    )

    schema = PipelineSchema(
        name=config.get("name", "").lower(),
        version=config.get("version", ""),
        description=config.get("description", ""),
        nodes=steps,
        available_models=config.get("available_models", []),
    )

    # View pass-through (explicit ``view`` block in the source JSON) or
    # synthesized from ``pipelines.default`` so every dataset overlay
    # renders without authoring graph bookkeeping by hand. The optimizer
    # pipeline ships an explicit view; dataset overlays don't.
    raw_view = config.get("view")
    if isinstance(raw_view, dict):
        view: PipelineView | None = PipelineView.model_validate(raw_view)
    elif schema.active_steps:
        view = derive_pipeline_view(schema)
    else:
        view = None

    return schema.model_copy(update={"view": view})
