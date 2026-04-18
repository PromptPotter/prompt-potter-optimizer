"""
Pipeline discovery: factory and dynamic pipeline view.

``parse_pipeline_response()`` parses a backend ``GET /pipeline`` JSON
response into a ``PipelineSchema``.  ``compute_pipeline_view()`` fetches
the response.  No backend-specific defaults — the schema is built
entirely from the live response.
"""

from __future__ import annotations

import logging
from datetime import UTC
from typing import TYPE_CHECKING, Any

from promptpotter.domain.pipeline_schema import (
    NodeOutputSchema,
    NodePromptMeta,
    ObservationMapping,
    PipelineNode,
    PipelineSchema,
)

if TYPE_CHECKING:
    from promptpotter.infrastructure.backend.client import BackendClient

logger = logging.getLogger(__name__)

__all__ = ["compute_pipeline_view", "parse_pipeline_response"]


def _parse_resolved_schema(resolved: dict[str, Any]) -> NodeOutputSchema:
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
                entry["output_schema"] = _parse_resolved_schema(schemas_raw[key])

        if pf := nc.get("prompt_family"):
            pv = nc.get("prompt_version")
            key = f"{pf}/{pv}" if pv is not None else pf
            if key in prompts_raw:
                entry["prompt_meta"] = _parse_resolved_prompt(prompts_raw[key])

        if entry:
            metadata[node_name] = entry

    return metadata


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
            "langfuse_type": opt.get("langfuse_type", "span"),
            "current_config": {k: v for k, v in nc.items() if k in pk},
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


async def compute_pipeline_view(
    backend_client: BackendClient,
) -> dict[str, Any]:
    """Build a view of the backend pipeline.

    Returns a dict with keys:
      backend_pipeline  — PipelineSchema.model_dump()
      computed_nodes    — pipeline nodes as dicts (direct copy for now)
      fetched_at        — ISO timestamp
      source            — "live" | "default"
    """
    from datetime import datetime

    try:
        raw: dict[str, Any] | None = await backend_client.fetch_pipeline()
        source = "live"
    except Exception:
        logger.warning("Backend unreachable at %s; returning empty schema", backend_client.base_url)
        raw = None
        source = "default"

    schema = parse_pipeline_response(raw) if raw is not None else PipelineSchema()
    computed_nodes = [s.model_dump() for s in schema.nodes]

    return {
        "backend_pipeline": schema.model_dump(),
        "computed_nodes": computed_nodes,
        "fetched_at": datetime.now(UTC).isoformat(),
        "source": source,
    }
