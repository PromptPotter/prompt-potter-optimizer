"""
Pipeline discovery: factory and dynamic pipeline view.

``parse_pipeline_response()`` parses a backend ``GET /pipeline`` JSON
response into a ``PipelineSchema``.  ``compute_pipeline_view()`` fetches
and caches the response.  No backend-specific defaults — the schema is
built entirely from the live response.
"""

from __future__ import annotations

import logging
import time as _time
from datetime import UTC
from typing import TYPE_CHECKING, Any

from api.config.settings import PIPELINE_CACHE_TTL
from api.models.pipeline_schema import (
    NodeOutputSchema,
    NodePromptMeta,
    ObservationMapping,
    PipelineNode,
    PipelineSchema,
)

if TYPE_CHECKING:
    from api.services.backend_client import BackendClient

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Factory helpers
# ---------------------------------------------------------------------------

def _parse_resolved_schema(resolved: dict[str, Any]) -> NodeOutputSchema:
    """Convert a ``_resolved_schema`` dict from the enriched response."""
    json_schema = resolved.get("json_schema", {})
    props = json_schema.get("properties", {})
    fields = resolved.get("fields") or list(props.keys())
    field_descriptions = {
        k: v.get("description", "")
        for k, v in props.items()
        if v.get("description")
    }
    return NodeOutputSchema(
        family=resolved.get("family", ""),
        version=resolved.get("version"),
        fields=fields,
        field_descriptions=field_descriptions,
        json_schema=json_schema,
    )


def _parse_resolved_prompt(resolved: dict[str, Any]) -> NodePromptMeta:
    """Convert a resolved prompt dict from the enriched response."""
    return NodePromptMeta(
        family=resolved.get("family", ""),
        version=resolved.get("version"),
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


# ---------------------------------------------------------------------------
# Main parser
# ---------------------------------------------------------------------------

def parse_pipeline_response(data: dict[str, Any]) -> PipelineSchema:
    """Parse a ``GET /pipeline`` JSON response into a PipelineSchema.

    Builds the schema entirely from the response — no hardcoded defaults.
    Each node may carry an ``optimizer`` sub-object with param_keys,
    override_map, observation_mappings, and other metadata consumed by
    PromptPotter services.
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
            "type": node.get("type", "tool"),
            "runtime": node.get("runtime", "backend"),
            "short_circuit": node.get("short_circuit", False),
            "node_role": node.get("node_role", ""),
            "description": node.get("description", ""),
            "param_keys": pk,
            "param_descriptions": opt.get("param_descriptions", {}),
            "override_map": opt.get("override_map", {}),
            "default_config": {
                k: v for k, v in nc.items()
                if k in pk or k in set(opt.get("override_map", {}).values())
            },
            "input_keys": set(opt.get("input_keys", [])),
            "langfuse_type": opt.get("langfuse_type", "span"),
            "current_config": {
                k: v for k, v in nc.items()
                if k in pk or k in set(opt.get("override_map", {}).values())
            },
        }

        # Observation mappings
        obs_name = opt.get("observation_name")
        if obs_name:
            step_kwargs["observation_name"] = obs_name
        obs_raw = opt.get("observation_mappings", [])
        if obs_raw:
            step_kwargs["observation_mappings"] = [
                ObservationMapping(**m) for m in obs_raw
            ]

        # Merge resolved registry metadata
        rm = resolved_metadata.get(name, {})
        if "output_schema" in rm:
            step_kwargs["output_schema"] = rm["output_schema"]
        if "prompt_meta" in rm:
            step_kwargs["prompt_meta"] = rm["prompt_meta"]

        steps.append(PipelineNode(**step_kwargs))

    logger.info(
        "Parsed pipeline '%s' with %d steps",
        config.get("name", "unknown"), len(steps),
    )

    return PipelineSchema(
        name=config.get("name", "").lower(),
        version=config.get("version", ""),
        description=config.get("description", ""),
        required_step=config.get("required_step"),
        template_variables=set(config.get("template_variables", [])),
        dataset_name=config.get("dataset_name", ""),
        available_models=config.get("available_models", []),
        nodes=steps,
    )


# ---------------------------------------------------------------------------
# TTL cache for pipeline responses
# ---------------------------------------------------------------------------

_PIPELINE_CACHE: dict[str, tuple[float, dict[str, Any]]] = {}


def _get_cached(base_url: str) -> dict[str, Any] | None:
    """Return cached raw response if still within TTL, else None."""
    entry = _PIPELINE_CACHE.get(base_url)
    if entry is None:
        return None
    ts, data = entry
    if (_time.monotonic() - ts) > PIPELINE_CACHE_TTL:
        del _PIPELINE_CACHE[base_url]
        return None
    return data


def _set_cached(base_url: str, data: dict[str, Any]) -> None:
    _PIPELINE_CACHE[base_url] = (_time.monotonic(), data)


# ---------------------------------------------------------------------------
# Dynamic pipeline view
# ---------------------------------------------------------------------------


async def compute_pipeline_view(
    backend_client: BackendClient,
) -> dict[str, Any]:
    """Build a view of the backend pipeline.

    Returns a dict with keys:
      backend_pipeline  — PipelineSchema.model_dump()
      computed_nodes    — pipeline nodes as dicts (direct copy for now)
      fetched_at        — ISO timestamp
      source            — "live" | "cached" | "default"
    """
    from datetime import datetime

    base_url = backend_client.base_url
    source = "live"
    raw: dict[str, Any] | None = None

    # Check cache
    cached = _get_cached(base_url)
    if cached is not None:
        raw = cached
        source = "cached"

    # Fetch from backend
    if raw is None:
        try:
            raw = await backend_client.fetch_pipeline()
            _set_cached(base_url, raw)
            source = "live"
        except Exception:
            logger.warning("Backend unreachable at %s; returning empty schema", base_url)
            raw = None
            source = "default"

    # Parse to PipelineSchema
    schema = parse_pipeline_response(raw) if raw is not None else PipelineSchema()

    # Computed steps (direct copy from backend pipeline for now)
    computed_nodes = [s.model_dump() for s in schema.nodes]

    return {
        "backend_pipeline": schema.model_dump(),
        "computed_nodes": computed_nodes,
        "fetched_at": datetime.now(UTC).isoformat(),
        "source": source,
    }
