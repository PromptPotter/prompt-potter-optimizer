"""
Pipeline discovery: static defaults, factory, and dynamic pipeline view.

Provides ``TERMNORM_DEFAULT_SCHEMA`` (the 6-step TermNorm pipeline),
``parse_pipeline_response()`` which parses a backend ``GET /pipeline``
JSON response into a ``PipelineSchema``, and ``compute_pipeline_view()``
which combines backend pipeline data with local node info.
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
            node_role="cache",
            description=(
                "Checks the cache for previously resolved queries. "
                "Short-circuits the pipeline on hit."
            ),
            langfuse_type="span",
        ),
        PipelineStep(
            name="fuzzy_matching",
            type="retriever",
            runtime="frontend",
            short_circuit=True,
            node_role="candidate_source",
            description=(
                "Fuzzy string matching against the database index. "
                "Short-circuits the pipeline on high-confidence matches, "
                "bypassing web search and LLM profiling entirely."
            ),
            param_keys={"fuzzy_threshold", "fuzzy_scorer"},
            param_descriptions={
                "fuzzy_threshold": (
                    "Minimum similarity score (0-100) to accept a fuzzy match"
                ),
                "fuzzy_scorer": "Scoring algorithm for fuzzy string comparison",
            },
            override_map={
                "fuzzy_threshold": "threshold",
                "fuzzy_scorer": "scorer",
            },
            langfuse_type="span",
        ),
        PipelineStep(
            name="web_search",
            type="tool",
            runtime="backend",
            node_role="enricher",
            description=(
                "Searches the web for contextual information about the input "
                "query. Retrieved content provides the primary raw context that "
                "entity_profiling uses to generate structured profiles — the "
                "quality of web results directly determines profiling quality."
            ),
            param_keys={
                "max_sites", "num_results", "content_char_limit",
                "query_prefix", "query_suffix", "url_fetch_multiplier",
            },
            param_descriptions={
                "query_prefix": (
                    "Text prepended to the input query before web search — "
                    "controls search focus, domain targeting, and terminology "
                    "framing (e.g. 'LCA database' narrows to domain)"
                ),
                "query_suffix": (
                    "Text appended to the input query before web search — "
                    "adds qualifiers, constraints, or context keywords "
                    "(e.g. 'material composition' adds specificity)"
                ),
                "num_results": "Number of search engine results to request",
                "max_sites": (
                    "Maximum number of web pages to fetch and extract content from"
                ),
                "content_char_limit": (
                    "Maximum characters of content to extract per web page"
                ),
                "url_fetch_multiplier": (
                    "Multiplier applied to max_sites to determine how many "
                    "URLs to fetch in parallel (e.g. max_sites=7, multiplier=4 "
                    "→ 28 URLs fetched, keeping top 7)"
                ),
            },
            override_map={
                "max_sites": "max_sites",
                "num_results": "num_results",
                "content_char_limit": "content_char_limit",
                "query_prefix": "query_prefix",
                "query_suffix": "query_suffix",
                "url_fetch_multiplier": "url_fetch_multiplier",
            },
            default_config={
                "query_prefix": "",
                "query_suffix": "",
                "num_results": 20,
                "max_sites": 7,
                "content_char_limit": 800,
                "url_fetch_multiplier": 4,
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
            node_role="enricher",
            description=(
                "LLM-driven step that generates a structured entity profile "
                "from the input query and web-sourced raw content. Produces "
                "the required pipeline output (entity_profile). Profile "
                "quality depends on both the LLM configuration and the "
                "quality of upstream web_search results."
            ),
            param_keys={
                "raw_content_limit", "profiling_temperature", "profiling_max_tokens",
                "profiling_prompt", "profiling_schema", "profiling_model",
            },
            param_descriptions={
                "raw_content_limit": (
                    "Maximum characters of web-sourced raw content passed "
                    "as context to the profiling LLM — bounds how much of "
                    "web_search output is visible to the model"
                ),
                "profiling_temperature": (
                    "LLM sampling temperature for profile generation"
                ),
                "profiling_max_tokens": (
                    "Maximum tokens in the LLM profile generation response"
                ),
            },
            override_map={
                "raw_content_limit": "raw_content_limit",
                "profiling_temperature": "temperature",
                "profiling_max_tokens": "max_tokens",
                "profiling_prompt": "prompt",
                "profiling_schema": "output_schema",
                "profiling_model": "model",
            },
            default_config={"raw_content_limit": 5000},
            input_keys={"web_sources"},
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
            node_role="candidate_source",
            description=(
                "Token-level retrieval matching entity profile fields against "
                "database entries. Candidate pool quality depends on the "
                "entity profile generated upstream."
            ),
            param_keys={"max_token_candidates"},
            param_descriptions={
                "max_token_candidates": (
                    "Maximum number of candidates to return from token matching"
                ),
            },
            override_map={
                "max_token_candidates": "max_token_candidates",
            },
            input_keys={"entity_profile"},
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
            node_role="ranker",
            description=(
                "LLM-driven re-ranking of token-matched candidates using "
                "a ranking prompt. Selects the best match from the candidate pool."
            ),
            param_keys={
                "ranking_temperature", "ranking_max_tokens",
                "ranking_sample_size", "ranking_prompt",
                "ranking_schema", "ranking_model",
                "relevance_weight_core",
            },
            param_descriptions={
                "ranking_temperature": "LLM sampling temperature for ranking",
                "ranking_max_tokens": (
                    "Maximum tokens in the LLM ranking response"
                ),
                "ranking_sample_size": (
                    "Number of candidates sampled from the pool for LLM ranking"
                ),
                "relevance_weight_core": (
                    "Weight given to core-concept relevance in ranking score"
                ),
            },
            override_map={
                "ranking_temperature": "temperature",
                "ranking_max_tokens": "max_tokens",
                "ranking_sample_size": "sample_size",
                "ranking_prompt": "prompt",
                "ranking_schema": "output_schema",
                "ranking_model": "model",
                "relevance_weight_core": "relevance_weight_core",
            },
            input_keys={"token_matched_candidates", "entity_profile"},
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
        json_schema=json_schema,
    )


def _parse_resolved_prompt(resolved: dict[str, Any]) -> StepPromptMeta:
    """Convert a resolved prompt dict from the enriched response."""
    return StepPromptMeta(
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
    # Top-level resolved dicts (keyed by "{family}/{version}")
    schemas_raw = config.get("resolved_schemas", {})
    prompts_raw = config.get("resolved_prompts", {})

    nodes = config.get("nodes", {})
    metadata: dict[str, dict[str, Any]] = {}

    for node_name, node_data in nodes.items():
        nc = node_data.get("config", {})
        entry: dict[str, Any] = {}

        # Schema: top-level lookup
        if sf := nc.get("schema_family"):
            sv = nc.get("schema_version")
            key = f"{sf}/{sv}" if sv is not None else sf
            if key in schemas_raw:
                entry["output_schema"] = _parse_resolved_schema(schemas_raw[key])

        # Prompt: top-level lookup
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

    Expects a ``nodes`` dict with per-node config.  Resolved metadata uses
    top-level ``resolved_schemas``/``resolved_prompts`` dicts.

    If the pipeline name matches a known default, enriches with observation
    mappings, langfuse types, and other metadata that the raw response
    doesn't carry. Resolved schema/prompt metadata from the response
    is merged into the known schema's steps.

    For unknown pipelines, builds a minimal schema from the response data.
    """
    if not data:
        logger.warning("Empty pipeline response; returning empty schema")
        return PipelineSchema()

    config = data.get("data", data)
    config = config.get("config", config)
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

        # Merge any resolved metadata and config values from the live response
        if resolved_metadata or nodes:
            updated_steps = []
            for step in known.steps:
                updates = {}
                if step.name in resolved_metadata:
                    rm = resolved_metadata[step.name]
                    if "output_schema" in rm:
                        updates["output_schema"] = rm["output_schema"]
                    if "prompt_meta" in rm:
                        updates["prompt_meta"] = rm["prompt_meta"]
                if step.name in nodes:
                    nc = nodes[step.name].get("config", {})
                    config_values = {
                        k: v for k, v in nc.items()
                        if not k.startswith("_") and k in step.param_keys
                    }
                    if config_values:
                        updates["current_config"] = config_values
                if updates:
                    step = step.model_copy(update=updates)
                updated_steps.append(step)
            return known.model_copy(update={
                "version": version or known.version,
                "description": description or known.description,
                "steps": updated_steps,
                "available_models": config.get("available_models", []),
            })

        return known.model_copy(update={
            "version": version or known.version,
            "description": description or known.description,
            "available_models": config.get("available_models", []),
        })

    # Unknown pipeline — build from nodes dict
    if not nodes:
        logger.warning(
            "Unknown pipeline '%s' with no nodes dict; returning empty schema",
            pipeline_name,
        )
        return PipelineSchema(
            name=pipeline_name, version=version, description=description,
        )

    steps: list[PipelineStep] = []
    for node_name, node_data in nodes.items():
        nc = node_data.get("config", {})
        # Filter out internal keys when building param_keys
        param_keys = {
            k for k in nc
            if not k.startswith("_")
        } if nc else set()
        config_values = {
            k: v for k, v in nc.items()
            if not k.startswith("_")
        }
        step_kwargs: dict[str, Any] = {
            "name": node_name,
            "type": node_data.get("type", "tool"),
            "short_circuit": node_data.get("short_circuit", False),
            "param_keys": param_keys,
            "current_config": config_values,
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
        available_models=config.get("available_models", []),
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
) -> dict[str, Any]:
    """Build a view of the backend pipeline.

    Returns a dict with keys:
      backend_pipeline  — PipelineSchema.model_dump()
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

    # Computed steps (direct copy from backend pipeline for now)
    computed_steps = [s.model_dump() for s in schema.steps]

    return {
        "backend_pipeline": schema.model_dump(),
        "computed_steps": computed_steps,
        "fetched_at": datetime.now(timezone.utc).isoformat(),
        "source": source,
    }
