from __future__ import annotations

import copy
import logging
from collections.abc import Mapping, Sequence
from typing import Any

from promptpotter.config.settings import WELL_KNOWN_PARAM_TYPES
from promptpotter.domain.pipeline_schema import (
    ANSWER_AS_JSON,
    ANSWER_AS_TEXT,
    OUTPUT_CONTRACT_KEYS,
    SCHEMA_TOGGLE_PARAM,
    THINKING_KINDS,
    NodeKind,
    NodeOutputSchema,
    NodePromptInfo,
    NodeType,
    ObservationMapping,
    PipelineNode,
    PipelineSchema,
    PipelineView,
    PipelineViewEdge,
    PipelineViewNode,
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

# The node-definition sub-blocks a partial overlay AUGMENTS rather than replaces.
# Every other key in a node block replaces wholesale — `output_schema` above all,
# because a shallow-merged schema can keep a `required` entry naming a field the
# incoming `properties` just dropped, and the backend rejects that.
_MERGED_NODE_SUB_BLOCKS = ("config", "optimizer")

# Maps INSIDE `optimizer` that are keyed BY PARAM, so a layer naming one param says nothing about
# the others. `PipelineSchema.narrow` composes `param_allowed_values` the same way, and the two
# must not disagree about what narrowing a value space means: merged one level, a rung list for
# one axis DELETES the declared space of every other axis on that node, which leaves those axes
# open with nothing to bound them and `build_l1_response_schema` emitting a bare string.
_MERGED_OPTIMIZER_MAPS = ("param_allowed_values", "param_descriptions")


def merge_node_blocks(base: dict[str, Any], overlay: dict[str, Any]) -> dict[str, Any]:
    """Layers node DEFINITIONS — one level above ``application.pipeline_resolve.apply_node_overlay``,
    which merges ``pipeline_params``. Only :data:`_MERGED_NODE_SUB_BLOCKS` merge by name, and
    inside `optimizer` the per-param maps merge by param (:data:`_MERGED_OPTIMIZER_MAPS`).
    ``param_keys`` is a SET declaration and still replaces: a layer restating which axes exist is
    answering for all of them."""
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
    """A lone surrogate is a valid Python codepoint that raises ``UnicodeEncodeError`` at the
    wire, so overlays are scrubbed at parse time and the schema is born wire-safe."""
    if isinstance(obj, str):
        return obj.encode("utf-8", errors="replace").decode("utf-8")
    if isinstance(obj, dict):
        return {k: strip_lone_surrogates(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [strip_lone_surrogates(v) for v in obj]
    return obj


def _node_kind(name: str, raw: object) -> NodeKind | None:
    """The declared ``type:``, admitted only if :class:`NodeKind` names it. RAISES on anything
    else — a typed setup error, not a warning, because the alternative is what stood before: an
    unrecognised type fell through to ``tool`` and the run proceeded describing the node wrongly on
    every surface. Absent stays ``None``; that is a producer saying nothing, not a bad answer."""
    if raw is None or raw == "":
        return None
    if isinstance(raw, str):
        try:
            return NodeKind(raw)
        except ValueError:
            pass
    # One raise for both misses — an unknown spelling and a non-string (a YAML `type: 3`) are the
    # same answer to the operator, and splitting them would state the permitted set twice.
    raise PayloadInvalidError(
        f"node {name!r} declares type {raw!r}, which is not a node kind. One of: "
        f"{', '.join(sorted(k.value for k in NodeKind))}.",
        code="pipeline_config_invalid",
        details={"node": name, "declared_type": str(raw)},
    )


def _derive_node_kind(node: PipelineNode | None) -> str:
    """The DECLARED kind (:class:`NodeKind`) mapped to the coarser vocabulary the CLIENT styles
    (``PipelineViewNode.kind``). Cache role wins — a hit short-circuits the pipeline.

    TOTAL over ``NodeKind`` rather than matched by prefix. ``startswith("llm")`` could not tell a
    declared type from a view kind spelled into a manifest, and every unlisted string fell through
    to ``tool`` — so a kind the client styles for nothing and a kind nobody declared were one
    answer. Now the first is this match's job and the second is refused at parse."""
    if node is None:
        return "tool"
    if node.node_type is NodeType.CACHE:
        return "cache"
    kind = node.wire_type
    # An undeclared node is plumbing until its producer says otherwise — the one place the old
    # catch-all survives, now naming the single input it actually covers.
    if kind is None:
        return "tool"
    if kind in THINKING_KINDS:
        return "llm"
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
        kind = _node_kind(name, node.get("type"))
        # A GATEWAY runs another PIPELINE, so the tunables it appears to have are that pipeline's
        # and it owns none. Refused here rather than filtered downstream: `node_config_schema`
        # derives a node's params from `param_keys | param_keys_held | current_config`, so a key
        # left standing in EITHER block becomes a row on every surface — which is how a dead
        # `reasoning_effort` came to be drawn, padlocked, on the one node in the optimizer graph
        # that does not reason. Both blocks, or the rule holds on the half that bit once.
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
            "wire_type": kind,
            "node_type": node.get("node_role", ""),
            "param_keys": pk,
            "param_descriptions": opt.get("param_descriptions", {}),
            "param_allowed_values": opt.get("param_allowed_values", {}),
            "param_types": _infer_param_types(opt, nc),
            "current_config": dict(nc),
            "tunes_llm": kind in THINKING_KINDS and bool(pk),
        }

        # Observation mappings
        obs_name = opt.get("observation_name")
        if obs_name:
            step_kwargs["observation_name"] = obs_name
        mappings = [ObservationMapping(**m) for m in opt.get("observation_mappings") or ()]
        if mappings:
            step_kwargs["observation_mappings"] = mappings

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

        # Synthesize the `description` lever onto any node that ships an `output_schema` —
        # schema-driven, never a per-dataset `param_keys` opt-in. The field NAME stays locked
        # (`SCHEMA_OWNED_FIELDS`); only the free prose becomes tunable, one `string` param per
        # field, so a campaign's `param_keys` holds or opens each like any scalar.
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

        # Synthesize the schema TOGGLE onto every node that tunes an LLM — the sibling of the
        # lever above, and neither is a per-dataset opt-in: whether the request carries a schema
        # is PromptPotter's own decision, so a connector re-declaring it would be a second
        # declaration of one axis (`docs/developer/node-standard.md`). One bound rides the value
        # space below; the model's own refusal is the other and belongs to `_refused`.
        if step_kwargs["tunes_llm"]:
            step_kwargs["param_keys"] = step_kwargs["param_keys"] | {SCHEMA_TOGGLE_PARAM}
            # Typed even where the node declares no schema — that is the row an operator creates
            # one from, and inference has nothing to read. Types only: they stay out of
            # `param_keys`, so no layer's `narrow` can intersect the row away.
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
            # Operator-facing, and only that: the menu renderer prints a description for an axis
            # with no value space, so L1 reads this axis through `catalogues::_schema_toggle_block`
            # instead, which states the precondition rather than the two values.
            step_kwargs["param_descriptions"] = {
                SCHEMA_TOGGLE_PARAM: (
                    f"How this node answers: {ANSWER_AS_JSON!r} fills the declared output "
                    f"schema, {ANSWER_AS_TEXT!r} sends no schema and answers in prose."
                ),
                **step_kwargs["param_descriptions"],
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
