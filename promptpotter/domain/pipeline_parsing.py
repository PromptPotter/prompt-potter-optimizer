"""Parse a backend ``GET /pipeline`` response into a ``PipelineSchema``.

Pure dict → domain transforms. No I/O, no async, no infrastructure
dependency — the network fetch lives at the call site
(``presentation/api.py::get_pipeline``). Backends are self-describing
(`pipelines.default` for step order, per-node ``optimizer`` sub-objects,
top-level ``resolved_schemas`` / ``resolved_prompts`` registries keyed by
``"{family}/{version}"``); zero hardcoded defaults.
"""

from __future__ import annotations

import logging
from typing import Any

from promptpotter.config.settings import WELL_KNOWN_PARAM_TYPES
from promptpotter.domain.pipeline_schema import (
    NodeOutputSchema,
    NodePromptMeta,
    ObservationMapping,
    PipelineNode,
    PipelineSchema,
)

logger = logging.getLogger(__name__)

__all__ = ["parse_pipeline_response", "parse_resolved_schema"]


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


def _parse_resolved_prompt(resolved: dict[str, Any]) -> NodePromptMeta:
    """Convert a resolved prompt dict from the enriched response."""
    return NodePromptMeta(
        family=resolved.get("family", ""),
        template_variables=resolved.get("template_variables", []),
        description=resolved.get("description", ""),
    )


def _extract_resolved_metadata(
    config: dict[str, Any],
) -> dict[str, dict[str, Any]]:
    """Extract per-node resolved metadata from the pipeline response.

    Uses top-level ``resolved_schemas``/``resolved_prompts`` dicts keyed by
    ``"{family}/{version}"``.  Each node references its schema/prompt via
    ``schema_family``/``prompt_family`` config keys.

    Returns mapping of ``node_name -> {"output_schema": ..., "prompt_meta": ...}``.
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
                entry["prompt_meta"] = _parse_resolved_prompt(prompts_raw[key])

        if entry:
            metadata[node_name] = entry

    return metadata


_PY_TO_JSON_TYPE: dict[type, str] = {
    bool: "boolean",
    int: "integer",
    float: "number",
    str: "string",
}


def _infer_param_types(opt: dict[str, Any], node_config: dict[str, Any]) -> dict[str, str]:
    """Resolve JSON-schema ``param_types`` for an optimizer node.

    Three-tier resolution, highest-precedence first:

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
    declared: dict[str, str] = dict(opt.get("param_types", {}) or {})
    for param in opt.get("param_keys", []) or []:
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
            "display_tag": opt.get("display_tag", ""),
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
        if "prompt_meta" in rm:
            step_kwargs["prompt_meta"] = rm["prompt_meta"]

        # Inline prompt_meta (for static pipeline.json without resolved_prompts)
        if "prompt_meta" not in step_kwargs and "prompt_meta" in node:
            step_kwargs["prompt_meta"] = NodePromptMeta(**node["prompt_meta"])

        steps.append(PipelineNode(**step_kwargs))

    logger.info(
        "Parsed pipeline '%s' with %d steps",
        config.get("name", "unknown"),
        len(steps),
    )

    return PipelineSchema(
        name=config.get("name", "").lower(),
        version=config.get("version", ""),
        description=config.get("description", ""),
        nodes=steps,
        available_models=config.get("available_models", []),
    )
