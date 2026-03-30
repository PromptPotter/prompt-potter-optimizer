"""Campaign factory functions — pipeline configuration and LLM client creation."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from api.models.pipeline_schema import PipelineSchema
    from api.services.llm_client import LLMClientBase

logger = logging.getLogger(__name__)


@dataclass
class PipelineConfigResult:
    """Result from ``configure_pipeline()``."""

    pipeline_params: dict
    active_nodes: list[str]
    excluded_nodes: list[str]


def configure_pipeline(
    pipeline_schema: PipelineSchema | None,
    campaign_config: dict,
    exp_data: dict | None = None,
) -> PipelineConfigResult:
    """Build pipeline_params from live pipeline schema and campaign_config.

    Uses *pipeline_schema* (from ``GET /pipeline``) as the source of
    truth for node names, falling back to *exp_data* only when the schema
    is unavailable.  Reads ``exclude_nodes`` and ``pipeline_overrides`` from
    *campaign_config*, stores the result back into
    ``campaign_config["pipeline_params"]``, and returns a typed result.
    """
    from api.services.backend_client import extract_pipeline_config

    exclude = campaign_config.get("exclude_nodes", [])
    overrides = campaign_config.get("pipeline_overrides")

    if pipeline_schema:
        all_names = [n.name for n in pipeline_schema.nodes]
    elif exp_data:
        pipeline_config = extract_pipeline_config(exp_data)
        all_names = [s["name"] for s in pipeline_config["steps"]]
    else:
        all_names = []

    active = [n for n in all_names if n not in (exclude or [])]
    pipeline_params: dict = {"steps": active}

    # Seed with live config from GET /pipeline (no hidden defaults)
    if pipeline_schema:
        for node in pipeline_schema.nodes:
            if node.name in active and node.current_config:
                pipeline_params[node.name] = dict(node.current_config)

    # Apply overrides for active nodes only
    if overrides and pipeline_schema:
        for flat_name, value in overrides.items():
            resolved = pipeline_schema.resolve_flat_param(flat_name)
            if resolved:
                node, wire_key = resolved
                if node in active:
                    pipeline_params.setdefault(node, {})[wire_key] = value
            else:
                pipeline_params[flat_name] = value
    elif overrides:
        pipeline_params.update(overrides)

    campaign_config["pipeline_params"] = pipeline_params

    return PipelineConfigResult(
        pipeline_params=pipeline_params,
        active_nodes=active,
        excluded_nodes=list(exclude) if exclude else [],
    )


def create_llm_client(
    campaign_config: dict,
) -> tuple[LLMClientBase, str]:
    """Create LLM client + model from campaign_config['eval_llm'].

    Returns:
        Tuple of (llm_client, model_name).
    """
    from api.services.llm_client import get_llm_client

    eval_llm = campaign_config["eval_llm"]
    return get_llm_client(eval_llm["provider"]), eval_llm["model"]
