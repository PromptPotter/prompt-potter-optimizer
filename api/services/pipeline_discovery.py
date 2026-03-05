"""
Pipeline discovery: static defaults, factory, and dynamic pipeline view.

Provides ``TERMNORM_DEFAULT_SCHEMA`` (the 6-step TermNorm pipeline),
``parse_pipeline_response()`` which parses a backend ``GET /pipeline``
JSON response into a ``PipelineSchema``, and ``compute_pipeline_view()``
which combines backend pipeline data with local workflow node info.
"""

from __future__ import annotations

import logging
import time as _time
from typing import TYPE_CHECKING, Any

from api.models.pipeline_schema import (
    ObservationMapping,
    PipelineSchema,
    PipelineStep,
    StepOutputSchema,
    StepPromptMeta,
)
from api.nodes import get_node_info_all
from api.services.constants import DATASET_NAME

if TYPE_CHECKING:
    from api.services.backend_client import BackendClient

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# TermNorm 6-step default
# ---------------------------------------------------------------------------

TERMNORM_DEFAULT_SCHEMA = PipelineSchema(
    name="termnorm",
    version="1.0",
    description="TermNorm AI terminology normalization pipeline",
    required_step="entity_profile",
    template_variables={"{{core_concept}}", "{{entity_profile_json}}", "{{matches}}"},
    dataset_name=DATASET_NAME,
    steps=[
        PipelineStep(
            name="cache_lookup",
            type="cache",
            runtime="frontend",
            short_circuit=True,
            langfuse_type="span",
        ),
        PipelineStep(
            name="fuzzy_matching",
            type="retriever",
            runtime="frontend",
            short_circuit=True,
            param_keys={"fuzzy_threshold", "fuzzy_scorer"},
            langfuse_type="span",
        ),
        PipelineStep(
            name="web_search",
            type="tool",
            runtime="backend",
            param_keys={
                "max_sites", "num_results", "content_char_limit",
                "query_prefix", "query_suffix",
            },
            observation_name="web_search",
            observation_mappings=[
                ObservationMapping(pipeline_key="web_sources", output_field="sources"),
                ObservationMapping(pipeline_key="web_search_status", output_field="status"),
            ],
            langfuse_type="tool",
        ),
        PipelineStep(
            name="entity_profiling",
            type="generation",
            runtime="backend",
            param_keys={
                "raw_content_limit", "profiling_temperature", "profiling_max_tokens",
                "profiling_prompt", "profiling_schema", "profiling_model",
            },
            observation_name="entity_profiling",
            observation_mappings=[
                ObservationMapping(
                    pipeline_key="entity_profile", output_field=None, is_llm=True,
                ),
            ],
            langfuse_type="generation",
        ),
        PipelineStep(
            name="token_matching",
            type="retriever",
            runtime="backend",
            param_keys={"max_token_candidates", "relevance_weight_core"},
            observation_name="token_matching",
            observation_mappings=[
                ObservationMapping(
                    pipeline_key="token_matched_candidates", output_field="candidates",
                ),
            ],
            langfuse_type="retriever",
        ),
        PipelineStep(
            name="llm_ranking",
            type="generation",
            runtime="backend",
            param_keys={
                "ranking_temperature", "ranking_max_tokens",
                "ranking_sample_size", "ranking_prompt",
                "ranking_schema", "ranking_model",
            },
            observation_name="llm_ranking",
            observation_mappings=[
                ObservationMapping(
                    pipeline_key="ranked_candidates",
                    output_field="ranked_candidates",
                    is_llm=True,
                ),
            ],
            langfuse_type="generation",
        ),
    ],
)


# ---------------------------------------------------------------------------
# Known pipeline defaults (keyed by pipeline name)
# ---------------------------------------------------------------------------

_KNOWN_PIPELINES: dict[str, PipelineSchema] = {
    "termnorm": TERMNORM_DEFAULT_SCHEMA,
}


# ---------------------------------------------------------------------------
# Factory
# ---------------------------------------------------------------------------

def _parse_resolved_schema(resolved: dict[str, Any]) -> StepOutputSchema:
    """Convert a ``_resolved_schema`` dict from the enriched response."""
    json_schema = resolved.get("json_schema", {})
    props = json_schema.get("properties", {})
    fields = resolved.get("fields") or list(props.keys())
    field_descriptions = {
        k: v.get("description", "")
        for k, v in props.items()
        if v.get("description")
    }
    return StepOutputSchema(
        family=resolved.get("family", ""),
        version=resolved.get("version"),
        fields=fields,
        field_descriptions=field_descriptions,
    )


def _parse_resolved_prompt(resolved: dict[str, Any]) -> StepPromptMeta:
    """Convert a resolved prompt dict from the enriched response."""
    return StepPromptMeta(
        family=resolved.get("family", ""),
        version=resolved.get("version"),
        template_variables=resolved.get("template_variables", []),
        description=resolved.get("description", ""),
        template=resolved.get("template", ""),
    )


def _extract_resolved_metadata(
    config: dict[str, Any],
) -> dict[str, dict[str, Any]]:
    """Extract per-node resolved metadata from the pipeline response.

    Prefers top-level ``resolved_schemas``/``resolved_prompts`` dicts (new
    format). Falls back to inline ``_resolved_schema``/``_resolved_prompt``
    keys inside each node's config (legacy format).

    Returns mapping of ``node_name -> {"output_schema": ..., "prompt_meta": ...}``.
    """
    # Top-level resolved dicts (keyed by "{family}/{version}")
    schemas_raw = config.get("resolved_schemas", {})
    prompts_raw = config.get("resolved_prompts", {})

    nodes = config.get("nodes", {})
    metadata: dict[str, dict[str, Any]] = {}

    for node_name, node_data in nodes.items():
        nc = node_data.get("config", {})
        entry: dict[str, Any] = {}

        # Schema: try top-level lookup first, then inline fallback
        if sf := nc.get("schema_family"):
            sv = nc.get("schema_version")
            key = f"{sf}/{sv}" if sv is not None else sf
            if key in schemas_raw:
                entry["output_schema"] = _parse_resolved_schema(schemas_raw[key])
            elif "_resolved_schema" in nc:
                entry["output_schema"] = _parse_resolved_schema(nc["_resolved_schema"])

        # Prompt: try top-level lookup first, then inline fallback
        if pf := nc.get("prompt_family"):
            pv = nc.get("prompt_version")
            key = f"{pf}/{pv}" if pv is not None else pf
            if key in prompts_raw:
                entry["prompt_meta"] = _parse_resolved_prompt(prompts_raw[key])
            elif "_resolved_prompt" in nc:
                entry["prompt_meta"] = _parse_resolved_prompt(nc["_resolved_prompt"])

        if entry:
            metadata[node_name] = entry

    return metadata


def parse_pipeline_response(data: dict[str, Any]) -> PipelineSchema:
    """Parse a ``GET /pipeline`` JSON response into a PipelineSchema.

    Handles three response formats:

    1. **New** (top-level ``resolved_schemas``/``resolved_prompts`` dicts):
       Nodes reference by ``schema_family``/``prompt_family``; resolved
       objects live in separate top-level sections.
    2. **Legacy enriched** (inline ``_resolved_schema``/``_resolved_prompt``
       in each node's config): Falls back to this if top-level dicts missing.
    3. **Legacy steps list**: Extracts step names and config keys.

    If the pipeline name matches a known default, enriches with observation
    mappings, langfuse types, and other metadata that the raw response
    doesn't carry. Resolved schema/prompt metadata from the response
    is merged into the known schema's steps.

    For unknown pipelines, builds a minimal schema from the response data.
    """
    if not data:
        logger.warning("Empty pipeline response; returning empty schema")
        return PipelineSchema()

    config = data.get("config", data)
    pipeline_name = config.get("name", "").lower()
    version = config.get("version", "")
    description = config.get("description", "")

    # Extract resolved metadata from response (top-level or inline)
    nodes = config.get("nodes", {})
    resolved_metadata = _extract_resolved_metadata(config)

    # Try to match a known pipeline and enrich
    if pipeline_name in _KNOWN_PIPELINES:
        known = _KNOWN_PIPELINES[pipeline_name]
        logger.info("Matched known pipeline '%s'; using enriched schema", pipeline_name)

        # Merge any resolved metadata from the live response into known steps
        if resolved_metadata:
            updated_steps = []
            for step in known.steps:
                if step.name in resolved_metadata:
                    updates = {}
                    rm = resolved_metadata[step.name]
                    if "output_schema" in rm:
                        updates["output_schema"] = rm["output_schema"]
                    if "prompt_meta" in rm:
                        updates["prompt_meta"] = rm["prompt_meta"]
                    if updates:
                        step = step.model_copy(update=updates)
                updated_steps.append(step)
            return known.model_copy(update={
                "version": version or known.version,
                "description": description or known.description,
                "steps": updated_steps,
            })

        return known.model_copy(update={
            "version": version or known.version,
            "description": description or known.description,
        })

    # Unknown pipeline — build from nodes dict or steps list
    if nodes:
        steps: list[PipelineStep] = []
        for node_name, node_data in nodes.items():
            nc = node_data.get("config", {})
            # Filter out internal keys when building param_keys
            param_keys = {
                k for k in nc
                if not k.startswith("_")
            } if nc else set()
            step_kwargs: dict[str, Any] = {
                "name": node_name,
                "type": node_data.get("type", "tool"),
                "short_circuit": node_data.get("short_circuit", False),
                "param_keys": param_keys,
            }
            rm = resolved_metadata.get(node_name, {})
            if "output_schema" in rm:
                step_kwargs["output_schema"] = rm["output_schema"]
            if "prompt_meta" in rm:
                step_kwargs["prompt_meta"] = rm["prompt_meta"]
            steps.append(PipelineStep(**step_kwargs))

        logger.info(
            "Unknown pipeline '%s'; built schema from nodes dict with %d steps",
            pipeline_name, len(steps),
        )
        return PipelineSchema(
            name=pipeline_name,
            version=version,
            description=description,
            steps=steps,
        )

    # Fallback: legacy steps list format
    steps_data = config.get("steps", [])
    steps = []
    for step_data in steps_data:
        step_name = step_data.get("name", "")
        step_config = step_data.get("config", {})
        param_keys = set(step_config.keys()) if step_config else set()
        steps.append(PipelineStep(
            name=step_name,
            param_keys=param_keys,
        ))

    logger.info(
        "Unknown pipeline '%s'; built minimal schema with %d steps",
        pipeline_name, len(steps),
    )
    return PipelineSchema(
        name=pipeline_name,
        version=version,
        description=description,
        steps=steps,
    )


# ---------------------------------------------------------------------------
# TTL cache for pipeline responses
# ---------------------------------------------------------------------------

_PIPELINE_CACHE: dict[str, tuple[float, dict[str, Any]]] = {}
_CACHE_TTL_SECONDS = 30.0


def _get_cached(base_url: str) -> dict[str, Any] | None:
    """Return cached raw response if still within TTL, else None."""
    entry = _PIPELINE_CACHE.get(base_url)
    if entry is None:
        return None
    ts, data = entry
    if (_time.monotonic() - ts) > _CACHE_TTL_SECONDS:
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
    *,
    include_nodes: bool = True,
) -> dict[str, Any]:
    """Build a combined view of backend pipeline + local workflow nodes.

    Returns a dict with keys:
      backend_pipeline  — PipelineSchema.model_dump()
      local_nodes       — list of node info dicts from api.nodes
      computed_steps    — pipeline steps as dicts (direct copy for now)
      fetched_at        — ISO timestamp
      source            — "live" | "cached" | "default"
    """
    from datetime import datetime, timezone

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
            logger.warning("Backend unreachable at %s; using default schema", base_url)
            raw = None
            source = "default"

    # Parse to PipelineSchema
    if raw is not None:
        schema = parse_pipeline_response(raw)
    else:
        schema = TERMNORM_DEFAULT_SCHEMA

    # Local nodes
    local_nodes: list[dict[str, Any]] = []
    if include_nodes:
        local_nodes = get_node_info_all()

    # Computed steps (direct copy from backend pipeline for now)
    computed_steps = [s.model_dump() for s in schema.steps]

    return {
        "backend_pipeline": schema.model_dump(),
        "local_nodes": local_nodes,
        "computed_steps": computed_steps,
        "fetched_at": datetime.now(timezone.utc).isoformat(),
        "source": source,
    }
