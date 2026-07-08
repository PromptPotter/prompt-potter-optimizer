"""Parse a backend ``GET /pipeline`` response into a ``PipelineSchema``.

Pure dict → domain transforms. No I/O, no async, no infrastructure
dependency — the network fetch lives at the call site
(``presentation/api.py::get_pipeline``). Backends are self-describing
(`pipelines.default` for step order, per-node ``optimizer`` sub-objects,
top-level ``resolved_schemas`` / ``resolved_prompts`` registries keyed by
``"{family}/{version}"``); zero hardcoded defaults.

The task model is read ONLY from each node's ``config`` (``current_config``);
the top-level ``llm_defaults`` block is **not authoritative** — it is a display
snapshot of the backend's ``GET /pipeline`` and is intentionally never parsed
here. The dataset owns its model in ``nodes.{node}.config.model``.
"""

from __future__ import annotations

import logging
from typing import Any

from promptpotter.config.settings import WELL_KNOWN_PARAM_TYPES
from promptpotter.domain.pipeline_schema import (
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
    "parse_pipeline_response",
    "parse_resolved_schema",
    "strip_lone_surrogates",
]


def strip_lone_surrogates(obj: Any) -> Any:
    """Recursively replace unpaired surrogate codepoints with ``?``.

    Some dataset overlays carry description strings whose JSON escape
    sequences point at lone low surrogates (e.g. ``\\udc9d``). Those
    codepoints are valid Python strings
    but cannot encode to UTF-8 when a downstream serializer (FastAPI,
    ``json.dumps`` without ``ensure_ascii``) tries to send them — they
    raise ``UnicodeEncodeError`` at the wire. Scrubbing at parse time so
    the resulting :class:`PipelineSchema` is already wire-safe.
    """
    if isinstance(obj, str):
        return obj.encode("utf-8", errors="replace").decode("utf-8")
    if isinstance(obj, dict):
        return {k: strip_lone_surrogates(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [strip_lone_surrogates(v) for v in obj]
    return obj


def _derive_node_kind(node: PipelineNode | None) -> str:
    """Pick a webapp render kind for one pipeline node.

    Priority order: cache role wins because a hit short-circuits the
    pipeline; then the LLM signal — a node is an LLM call if it is the
    ``llm_only`` single-LLM sentinel, declares a ``generation`` wire /
    langfuse type (the backend's explicit LLM marker), OR carries an
    ``is_llm`` observation mapping. A bare ``llm_only`` overlay carries
    none of the latter two, so the sentinel name is load-bearing here.
    Everything else falls back to the connector's ``wire_type``
    (``tool`` / ``retriever``) and ultimately a plain ``tool`` dot.
    ``node_role`` of ``"ranker"`` / ``"candidate_source"`` is intentionally
    NOT checked — those roles can be LLM (``llm_ranking``) or pure algos
    (``fuzzy_matching``); the three signals above are what discriminate.
    """
    if node is None:
        return "tool"
    if str(node.node_type) == "cache":
        return "cache"
    if (
        node.name == "llm_only"
        or node.is_llm
        or node.wire_type == "generation"
        or node.langfuse_type == "generation"
    ):
        return "llm"
    if node.wire_type == "retriever":
        return "retriever"
    if node.wire_type == "tool":
        return "tool"
    return "tool"


def derive_pipeline_view(schema: PipelineSchema) -> PipelineView:
    """Synthesize a webapp ``PipelineView`` from a ``PipelineSchema``.

    Wraps ``schema.active_steps`` with synthetic ``input``/``output`` IO
    bookends and chains a forward edge through them. ``kind`` per interior
    node comes from ``_derive_node_kind`` so the dot styling matches what
    the node actually does (LLM nodes glow accent; retrievers/tools/caches
    each get a distinct fill).
    """
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
    """Convert a ``_resolved_schema`` dict from the enriched response."""
    json_schema = resolved.get("json_schema", {})
    props = json_schema.get("properties", {})
    fields = resolved.get("fields") or list(props.keys())
    field_descriptions = {
        k: v.get("description", "") for k, v in props.items() if v.get("description")
    }
    return NodeOutputSchema(
        fields=fields,
        field_descriptions=field_descriptions,
        json_schema=json_schema,
    )


def _parse_resolved_prompt(resolved: dict[str, Any]) -> NodePromptInfo:
    """Convert a resolved prompt dict from the enriched response."""
    return NodePromptInfo(
        template_variables=resolved.get("template_variables", []),
    )


def _extract_resolved_metadata(
    config: dict[str, Any],
) -> dict[str, dict[str, Any]]:
    """Extract per-node resolved metadata from the pipeline response.

    Uses top-level ``resolved_schemas``/``resolved_prompts`` dicts keyed by
    ``"{family}/{version}"``.  Each node references its schema/prompt via
    ``schema_family``/``prompt_family`` config keys.

    Returns mapping of ``node_name -> {"output_schema": ..., "prompt_info": ...}``.
    """
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


_PY_TO_JSON_TYPE: dict[type, str] = {
    bool: "boolean",
    int: "integer",
    float: "number",
    str: "string",
    dict: "object",
    list: "array",
}


def _infer_param_types(opt: dict[str, Any], node_config: dict[str, Any]) -> dict[str, str]:
    """Resolve JSON-schema ``param_types`` for a node — covering EVERY key the
    node carries, not just the optimizer-tunable ``param_keys``.

    A node's ``config`` can hold keys not declared in ``param_keys`` (e.g.
    ``provider: groq`` — set but not advertised as tunable). The operator-steer
    panel bundles those too (``node_config_schema`` walks the union), so their
    widget kind must resolve. Iteration is ``param_keys`` followed by the
    config-only keys, both run through the same three-tier resolution,
    highest-precedence first:

    1. Explicit ``optimizer.param_types`` on the dataset overlay (rare; only
       needed for backend-specific params with no project-wide convention).
    2. ``WELL_KNOWN_PARAM_TYPES`` registry — universal LLM-call params
       (``temperature``, ``max_tokens``, ``model``, ``provider``,
       ``reasoning_effort``, ...) plus the six-field PromptTemplate scheme.
       This is where every common param resolves; dataset overlays do not
       re-declare these.
    3. Inference from the Python type of the param's default in
       ``node.config`` (e.g. ``threshold: 70`` → ``"integer"``). Last-resort
       for backend-specific params where the default carries the type.
       Booleans are matched before ints because ``isinstance(True, int)`` is
       True in Python — dict-iteration order in ``_PY_TO_JSON_TYPE`` matters.
    """
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
    """Parse a ``GET /pipeline`` JSON response into a PipelineSchema.

    Builds the schema entirely from the response — no hardcoded defaults.
    Each node may carry an ``optimizer`` sub-object with param_keys,
    observation_mappings, and other metadata consumed by PromptPotter
    services.
    """
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
            "short_circuit": node.get("short_circuit", False),
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

        # Inline prompt_info (for static pipeline.json without resolved_prompts)
        if "prompt_info" not in step_kwargs and "prompt_info" in node:
            step_kwargs["prompt_info"] = NodePromptInfo(**node["prompt_info"])

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
