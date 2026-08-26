from __future__ import annotations

import copy
import logging
from collections.abc import Mapping, Sequence
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
    """Cache role wins — a hit short-circuits the pipeline — then the LLM-bearing signals,
    broadest first, so a node carrying the model axis can never read as anything else."""
    if node is None:
        return "tool"
    if str(node.node_type) == "cache":
        return "cache"
    if node.runs_llm:
        return "llm"
    # `llm/optimizer` and `agent` bear an LLM without carrying the MODEL AXIS, which is all
    # `runs_llm` asks about — an in-process optimizer node resolves no carrier.
    if node.wire_type.startswith("llm") or node.wire_type == "agent":
        return "llm"
    # A kind this cannot emit is a kind the client styles and captions for nothing, so every
    # member of the served vocabulary has a branch here (`pipeline_schema.PipelineViewNode`).
    if node.wire_type == "measurement":
        return "measurement"
    if node.wire_type == "retriever":
        return "retriever"
    if node.wire_type == "tool":
        return "tool"
    return "tool"


def derive_pipeline_view(
    nodes: Mapping[str, PipelineNode],
    pipelines: Mapping[str, Sequence[str]],
) -> PipelineView:
    """The graph the engine actually runs, read off the two blocks that declare it.

    ``default`` is the chain a sample runs. A node declared but named by no pipeline runs
    once ahead of it, so it joins the chain without being a member of anything that
    repeats. Every other pipeline is an ESCALATION: the nodes it introduces are placed at
    its depth, and the depths order by containment, since a deeper escalation re-runs the
    shallower one's steps. A pipeline with no escalations is one straight tier.
    """
    declared = list(nodes)
    chain = [n for n in (pipelines.get("default") or declared) if n in nodes]
    in_chain = set(chain)
    others = sorted(
        (name, [s for s in seq if s in nodes])
        for name, seq in pipelines.items()
        if name != "default"
    )
    # Sharing no step with the chain makes a pipeline a separate PHASE — its own occasion,
    # ahead of the chain and outside anything that repeats. Sharing steps makes it an
    # ESCALATION, which re-runs the chain rather than standing beside it. A node named by
    # NO pipeline is not in the flow at all and is drawn nowhere.
    spine = [*(s for _n, seq in others if not (set(seq) & in_chain) for s in seq), *chain]
    rank_of = {name: i for i, name in enumerate(spine)}
    placed: dict[str, tuple[int, int]] = {n: (0, i) for i, n in enumerate(spine)}

    # Shortest first: an escalation that re-runs another's steps is the deeper of the two,
    # so length IS the containment order for a chain of them.
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
            # Ranked on the tier-0 step it acts on: the first spine node that follows it.
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
                kind=_derive_node_kind(nodes.get(name)),
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
    # An escalation re-runs the chain, which is what makes the chain repeat — so a view
    # carrying any tier above 0 always carries this edge too, and a renderer may lay a
    # loopless view out as a straight rail knowing every node on it is tier 0.
    if introduced and chain:
        _edge(chain[-1], chain[0], "loop")
    for fresh, seq in introduced:
        for step in fresh:
            if chain:
                _edge(chain[-1], step, "escalate")
            after = seq[seq.index(step) + 1 :]
            if after:
                _edge(step, after[0], "directive")

    return PipelineView(nodes=view_nodes, edges=edges)


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

    # EVERY declared node, because the escalation pipelines name nodes beside the chain and
    # both the view and the config surface reach them. `steps` below stays the chain alone,
    # which is what keeps `active_steps` — and so `sp_hash` — a fact about the round.
    parsed: dict[str, PipelineNode] = {}
    for name in nodes:
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

        parsed[name] = PipelineNode(**step_kwargs)

    steps: list[PipelineNode] = [parsed[name] for name in step_order if name in parsed]

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
        declared_nodes=list(parsed.values()),
        available_models=config.get("available_models", []),
    )

    # Always derived, never read off the manifest: a declared ``view`` is a second roster
    # beside `nodes`, with nothing able to catch the two drifting apart.
    pipelines = config.get("pipelines") or {"default": step_order}
    view = derive_pipeline_view(parsed, pipelines) if parsed else None

    return schema.model_copy(update={"view": view})
