"""
Pipeline discovery: static defaults and factory for PipelineSchema.

Provides ``TERMNORM_DEFAULT_SCHEMA`` (the 6-step TermNorm pipeline) and
``parse_pipeline_response()`` which parses a backend ``GET /pipeline``
JSON response into a ``PipelineSchema``.
"""

import logging
from typing import Any

from api.models.pipeline_schema import ObservationMapping, PipelineSchema, PipelineStep
from api.services.constants import DATASET_NAME

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
            langfuse_type="span",
        ),
        PipelineStep(
            name="web_search",
            type="tool",
            runtime="backend",
            param_keys={"max_sites", "num_results", "content_char_limit"},
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

def parse_pipeline_response(data: dict[str, Any]) -> PipelineSchema:
    """Parse a ``GET /pipeline`` JSON response into a PipelineSchema.

    Extracts step names and config keys from the response. If the pipeline
    name matches a known default, enriches with observation mappings,
    langfuse types, and other metadata that the raw response doesn't carry.

    For unknown pipelines, builds a minimal schema from the response data.
    """
    if not data:
        logger.warning("Empty pipeline response; returning empty schema")
        return PipelineSchema()

    config = data.get("config", data)
    pipeline_name = config.get("name", "").lower()
    version = config.get("version", "")
    description = config.get("description", "")

    # Try to match a known pipeline and enrich
    if pipeline_name in _KNOWN_PIPELINES:
        known = _KNOWN_PIPELINES[pipeline_name]
        logger.info("Matched known pipeline '%s'; using enriched schema", pipeline_name)
        return known.model_copy(update={
            "version": version or known.version,
            "description": description or known.description,
        })

    # Unknown pipeline — build minimal schema from response
    steps_data = config.get("steps", [])
    steps: list[PipelineStep] = []
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
