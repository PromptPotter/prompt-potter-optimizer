from __future__ import annotations

import copy
import logging
from collections.abc import Mapping, Sequence
from typing import Any, NoReturn

from promptpotter.domain.pipeline_schema import (
    ANSWER_AS_JSON,
    ANSWER_AS_TEXT,
    MEMBER_KINDS,
    OUTPUT_CONTRACT_KEYS,
    SCHEMA_TOGGLE_PARAM,
    THINKING_KINDS,
    NodeKind,
    NodeOutputSchema,
    NodePromptInfo,
    NodeRole,
    ObservationMapping,
    PipelineNode,
    PipelineSchema,
    PipelineView,
    PipelineViewEdge,
    PipelineViewNode,
    ViewKind,
    description_key,
    description_paths,
)
from promptpotter.shared.errors import PayloadInvalidError
from promptpotter.shared.hashing import shapes_optimizer_prompt

shapes_optimizer_prompt(__name__)

logger = logging.getLogger(__name__)

__all__ = [
    "derive_pipeline_view",
    "merge_node_blocks",
    "parse_pipeline_response",
    "parse_resolved_schema",
]

WELL_KNOWN_PARAM_TYPES: dict[str, str] = {
    "temperature": "number",
    "top_p": "number",
    "max_tokens": "integer",
    "max_completion_tokens": "integer",
    "thinking_budget": "integer",
    "seed": "integer",
    "model": "string",
    "provider": "string",
    "reasoning_effort": "string",
    "persona": "string",
    "task_intent": "string",
    "problem_description": "string",
    "instruction": "string",
    "thinking_style": "string",
    "answer_format": "string",
    # Typed, not inferred: both must resolve on a node declaring NO schema.
    "output_schema": "object",
    "answer_field": "string",
}

# Every other key replaces wholesale: a shallow-merged `output_schema` keeps a `required` naming a dropped property.
_MERGED_NODE_SUB_BLOCKS = ("config", "optimizer")

# Keyed BY PARAM: merged one level up, a layer naming one axis deletes every other axis's value space.
_MERGED_OPTIMIZER_MAPS = ("param_allowed_values", "param_descriptions")


def merge_node_blocks(base: dict[str, Any], overlay: dict[str, Any]) -> dict[str, Any]:
    """``param_keys`` is a SET declaration, so it replaces: a layer restating the axes answers for all."""
    out = copy.deepcopy(base)
    for node_name, node_def in overlay.items():
        if not isinstance(node_def, dict):
            continue
        dst = out.setdefault(node_name, {})
        for key, val in node_def.items():
            if key in _MERGED_NODE_SUB_BLOCKS and isinstance(val, dict):
                block = dst.setdefault(key, {})
                for sub, sub_val in val.items():
                    if (
                        key == "optimizer"
                        and sub in _MERGED_OPTIMIZER_MAPS
                        and isinstance(sub_val, dict)
                        and isinstance(block.get(sub), dict)
                    ):
                        block[sub] = {**block[sub], **sub_val}
                    else:
                        block[sub] = sub_val
            else:
                dst[key] = val
    return out


def strip_lone_surrogates(obj: Any) -> Any:
    """A lone surrogate is a valid Python codepoint that raises ``UnicodeEncodeError`` at the wire."""
    if isinstance(obj, str):
        return obj.encode("utf-8", errors="replace").decode("utf-8")
    if isinstance(obj, dict):
        return {k: strip_lone_surrogates(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [strip_lone_surrogates(v) for v in obj]
    return obj


def _node_kind(name: str, raw: object) -> NodeKind | None:
    if raw is None or raw == "":
        return None
    if isinstance(raw, str):
        try:
            return NodeKind(raw)
        except ValueError:
            pass
    raise PayloadInvalidError(
        f"node {name!r} declares type {raw!r}, which is not a node kind. One of: "
        f"{', '.join(sorted(k.value for k in NodeKind))}.",
        code="pipeline_config_invalid",
        details={"node": name, "declared_type": str(raw)},
    )


def _derive_node_kind(node: PipelineNode) -> ViewKind:
    if node.role is NodeRole.CACHE:
        return "cache"
    kind = node.kind
    if kind is None:
        return "tool"
    if kind in THINKING_KINDS:
        return "llm"
    if kind in MEMBER_KINDS:
        return "tool"
    match kind:
        case NodeKind.GATEWAY:
            return "measurement"
        case NodeKind.RETRIEVER:
            return "retriever"
        case NodeKind.TOOL | NodeKind.CACHE:
            return "tool"
        case _:  # pragma: no cover — THINKING_KINDS covers the rest, and the assert pins that
            raise AssertionError(f"NodeKind {kind!r} has no view kind")


def derive_pipeline_view(
    nodes: Mapping[str, PipelineNode],
    pipelines: Mapping[str, Sequence[str]],
    descriptions: Mapping[str, str],
) -> PipelineView:
    declared = list(nodes)
    chain = [n for n in (pipelines.get("default") or declared) if n in nodes]
    in_chain = set(chain)
    others = sorted(
        (name, [s for s in seq if s in nodes])
        for name, seq in pipelines.items()
        if name != "default"
    )
    # Sharing no step with the chain makes a pipeline a PHASE, drawn ahead of it; sharing steps, an ALTERNATIVE.
    spine = [*(s for _n, seq in others if not (set(seq) & in_chain) for s in seq), *chain]
    rank_of = {name: i for i, name in enumerate(spine)}
    placed: dict[str, tuple[int, int]] = {n: (0, i) for i, n in enumerate(spine)}

    # Shortest first: an alternative re-running another's steps is the deeper, so length is containment.
    ordered = sorted(
        ((name, seq) for name, seq in others if set(seq) & in_chain),
        key=lambda kv: (len(kv[1]), kv[0]),
    )
    depth = 0
    introduced: list[tuple[list[str], list[str]]] = []
    for _name, seq in ordered:
        fresh = [s for s in seq if s not in placed]
        if not fresh:
            continue
        depth += 1
        for step in fresh:
            following = seq[seq.index(step) + 1 :]
            placed[step] = (
                depth,
                next((rank_of[t] for t in following if t in rank_of), max(len(spine) - 1, 0)),
            )
        introduced.append((fresh, seq))

    view_nodes = [PipelineViewNode(id="input", label="Input", kind="io", rank=-1)]
    for name, (tier, rank) in placed.items():
        view_nodes.append(
            PipelineViewNode(
                id=name,
                label=name,
                description=descriptions[name],
                kind=_derive_node_kind(nodes[name]),
                tier=tier,
                rank=rank,
            )
        )
    view_nodes.append(PipelineViewNode(id="output", label="Output", kind="io", rank=len(spine)))

    edges: list[PipelineViewEdge] = []

    def _edge(source: str, target: str, kind: str) -> None:
        edges.append(PipelineViewEdge.model_validate({"from": source, "to": target, "kind": kind}))

    sequence = ["input", *spine, "output"]
    for i in range(len(sequence) - 1):
        _edge(sequence[i], sequence[i + 1], "forward")
    repeats = bool(introduced) or any(n.kind in MEMBER_KINDS for n in nodes.values())
    if repeats and chain:
        _edge(chain[-1], chain[0], "loop")
    for fresh, seq in introduced:
        for step in fresh:
            if chain:
                _edge(chain[-1], step, "alternative")
            after = seq[seq.index(step) + 1 :]
            if after:
                _edge(step, after[0], "directive")

    return PipelineView(nodes=view_nodes, edges=edges)


def parse_resolved_schema(resolved: dict[str, Any]) -> NodeOutputSchema:
    """``fields`` order is the generation order; an empty ``description`` is a searchpoint, not an absence."""
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
    """Covers config-only keys too: the steer panel renders them, so their widget kind must resolve."""
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


def _check_member_structure(
    parsed: Mapping[str, PipelineNode], pipelines: Mapping[str, Sequence[str]]
) -> None:
    def kind(name: str) -> NodeKind | None:
        node = parsed.get(name)
        return node.kind if node is not None else None

    def refuse(message: str, **details: Any) -> NoReturn:
        raise PayloadInvalidError(message, code="pipeline_config_invalid", details=details)

    if not any(node.kind in MEMBER_KINDS for node in parsed.values()):
        return
    default = list(pipelines.get("default") or [])
    measurements = [n for n in default if kind(n) is NodeKind.GATEWAY]
    if len(measurements) != 1:
        refuse(
            "an optimizer manifest's `default` pipeline must name exactly one measurement node "
            f"(found {measurements}): with none the round has no rows, with two nothing says "
            "which set a selector reads.",
            measurement_nodes=measurements,
        )
    controllers = [n for n in default if kind(n) is NodeKind.CONTROLLER]
    if len(controllers) > 1:
        refuse(
            f"`default` names {len(controllers)} controllers ({controllers}); a round has one.",
            controller_nodes=controllers,
        )
    for pipeline, steps in pipelines.items():
        for i, step in enumerate(steps):
            if kind(step) is NodeKind.ELIMINATOR and not any(
                kind(s) is NodeKind.SAMPLER for s in steps[:i]
            ):
                refuse(
                    f"pipeline {pipeline!r}: eliminator {step!r} has no sampler before it — a cut "
                    "decides between blocks, and only a sampler cuts the panel into blocks.",
                    pipeline=pipeline,
                    eliminator=step,
                )


def parse_pipeline_response(data: dict[str, Any]) -> PipelineSchema:
    if not data:
        logger.warning("Empty pipeline response; returning empty schema")
        return PipelineSchema()

    data = strip_lone_surrogates(data)
    config = data.get("data", data)
    config = config.get("config", config)

    nodes = config.get("nodes", {})
    resolved_metadata = _extract_resolved_metadata(config)

    pipelines = {"default": list(nodes), **(config.get("pipelines") or {})}

    # The chain stays `pipelines["default"]` alone, which keeps `active_steps` and `sp_hash` facts about the round.
    parsed: dict[str, PipelineNode] = {}
    for name in nodes:
        node = nodes.get(name, {})
        if not node:
            continue
        opt = node.get("optimizer", {})
        nc = node.get("config", {})
        pk = set(opt.get("param_keys", []))
        kind = _node_kind(name, node.get("type"))
        # Refused, not filtered downstream: a key left in EITHER block becomes a row on every surface.
        if kind is NodeKind.GATEWAY and (declared := sorted(set(nc) | pk)):
            raise PayloadInvalidError(
                f"node {name!r} is a gateway ({kind.value}): it runs another pipeline, so its "
                f"tunables belong to that pipeline and it declares none of its own. Remove "
                f"{declared} from its `config` / `optimizer.param_keys`.",
                code="pipeline_config_invalid",
                details={"node": name, "kind": kind.value, "declared_keys": declared},
            )

        step_kwargs: dict[str, Any] = {
            "name": name,
            "kind": kind,
            "role": node.get("node_role") or None,
            "param_keys": pk,
            "param_descriptions": opt.get("param_descriptions", {}),
            "param_allowed_values": opt.get("param_allowed_values", {}),
            "param_types": _infer_param_types(opt, nc),
            "current_config": dict(nc),
            "tunes_llm": kind in THINKING_KINDS and bool(pk),
            "spend_bound": node.get("spend_bound"),
        }

        obs_name = opt.get("observation_name")
        if obs_name:
            step_kwargs["observation_name"] = obs_name
        mappings = [ObservationMapping(**m) for m in opt.get("observation_mappings") or ()]
        if mappings:
            step_kwargs["observation_mappings"] = mappings

        rm = resolved_metadata.get(name, {})
        if "output_schema" in rm:
            step_kwargs["output_schema"] = rm["output_schema"]
        if "prompt_info" in rm:
            step_kwargs["prompt_info"] = rm["prompt_info"]

        if "prompt_info" not in step_kwargs and "prompt_info" in node:
            step_kwargs["prompt_info"] = NodePromptInfo(**node["prompt_info"])

        # Read from `config`, the declaration the connector forwards: never a display copy beside the wire copy.
        if "output_schema" not in step_kwargs and isinstance(nc.get("output_schema"), dict):
            step_kwargs["output_schema"] = parse_resolved_schema(
                {"json_schema": nc["output_schema"]}
            )
            # Checked at load: an undeclared field reads "" on every sample, and the run grades NO_RESULT.
            answer_field = nc.get("answer_field")
            props = nc["output_schema"].get("properties") or {}
            if answer_field is not None and answer_field not in props:
                raise ValueError(
                    f"node {name!r}: answer_field {answer_field!r} is not a property of its "
                    f"output_schema (have: {sorted(props)})"
                )

        # Schema-driven, never a `param_keys` opt-in; the field NAME stays locked (`SCHEMA_OWNED_FIELDS`).
        out_schema = step_kwargs.get("output_schema")
        described = (
            [description_key(p) for p in description_paths(out_schema.json_schema)]
            if out_schema is not None
            else []
        )
        if described:
            step_kwargs["param_keys"] = pk | set(described)
            step_kwargs["param_types"] = {
                **step_kwargs["param_types"],
                **dict.fromkeys(described, "string"),
            }

        # Synthesized, never connector-declared: whether a request carries a schema is ours to decide.
        if step_kwargs["tunes_llm"]:
            step_kwargs["param_keys"] = step_kwargs["param_keys"] | {SCHEMA_TOGGLE_PARAM}
            # Types only: outside `param_keys`, so no layer's `narrow` can intersect the row away.
            step_kwargs["param_types"] = {
                **step_kwargs["param_types"],
                SCHEMA_TOGGLE_PARAM: "string",
                **{k: WELL_KNOWN_PARAM_TYPES[k] for k in OUTPUT_CONTRACT_KEYS},
            }
            step_kwargs["param_allowed_values"] = {
                **step_kwargs["param_allowed_values"],
                SCHEMA_TOGGLE_PARAM: (
                    [ANSWER_AS_TEXT, ANSWER_AS_JSON] if out_schema else [ANSWER_AS_TEXT]
                ),
            }
            # Operator-facing only: L1 reads this axis through `catalogues::_schema_toggle_block`.
            step_kwargs["param_descriptions"] = {
                SCHEMA_TOGGLE_PARAM: (
                    f"How this node answers: {ANSWER_AS_JSON!r} fills the declared output "
                    f"schema, {ANSWER_AS_TEXT!r} sends no schema and answers in prose."
                ),
                **step_kwargs["param_descriptions"],
            }

        parsed[name] = PipelineNode(**step_kwargs)

    # DEBUG: a parse on a read path fires per poll, and INFO would flood a supervised run's console.
    logger.debug(
        "Parsed pipeline '%s' with %d nodes",
        config.get("name", "unknown"),
        len(parsed),
    )

    _check_member_structure(parsed, pipelines)
    schema = PipelineSchema(
        name=config.get("name", "").lower(),
        version=config.get("version", ""),
        description=config.get("description", ""),
        declared_nodes=list(parsed.values()),
        pipelines={name: list(seq) for name, seq in pipelines.items()},
        available_models=config.get("available_models", []),
    )

    # Derived, never read off the manifest: a declared ``view`` is a second roster beside `nodes`.
    descriptions = {name: str(nodes[name].get("description") or "") for name in parsed}
    view = derive_pipeline_view(parsed, pipelines, descriptions) if parsed else None

    return schema.model_copy(update={"view": view})
