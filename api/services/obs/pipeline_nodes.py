"""Typed pipeline node extraction for Langfuse graph observations.

Parses backend ``pipeline_data`` dicts into ordered ``PipelineNode`` objects
with correct Langfuse ``as_type`` mapping so each step appears as a distinct
top-level node in the trace graph.

as_type mapping:
    web_search       → "tool"       (external tool call)
    entity_profiling → "generation" (LLM call 1)
    token_matching   → "retriever"  (deterministic data retrieval)
    llm_ranking      → "generation" (LLM call 2, conditional)
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from api.models.pipeline_schema import PipelineSchema


@dataclass(frozen=True)
class PipelineNode:
    """A single pipeline step ready to be pushed as a Langfuse observation."""

    name: str
    as_type: str
    input: dict[str, Any]
    output: dict[str, Any]
    metadata: dict[str, Any] = field(default_factory=dict)
    model: str | None = None
    usage_details: dict[str, int] | None = None


def _profile_summary(profile: dict) -> str:
    """One-line summary of an entity profile for token_matching input."""
    name = profile.get("entity_name", "")
    concept = profile.get("core_concept", "")
    if name and concept:
        return f"{name} ({concept})"
    return name or concept or ""


def _step_meta(
    timings: dict,
    step_params: dict,
    step_name: str,
    schema: "PipelineSchema",
) -> dict[str, Any]:
    """Build metadata dict for a pipeline step: timing + per-step params."""
    meta: dict[str, Any] = {}
    if step_name in timings:
        meta["duration_s"] = timings[step_name]
    param_keys = schema.step_param_keys().get(step_name, set())
    node_params = {k: step_params[k] for k in param_keys if k in step_params}
    if node_params:
        meta["pipeline_params"] = node_params
    return meta


def extract_pipeline_nodes(
    pipeline_data: dict,
    query: str,
    schema: "PipelineSchema",
) -> list[PipelineNode]:
    """Parse pipeline_data into an ordered list of typed nodes.

    Missing steps are simply absent from the returned list.
    """
    nodes: list[PipelineNode] = []
    timings = pipeline_data.get("step_timings") or {}
    llm_provider = pipeline_data.get("llm_provider", "")
    step_params = pipeline_data.get("pipeline_params") or {}
    lf_types = schema.langfuse_type_map() if schema else {}

    # 1. web_search — tool
    if pipeline_data.get("web_search_status"):
        meta = _step_meta(timings, step_params, "web_search", schema)
        nodes.append(PipelineNode(
            name="web_search",
            as_type=lf_types.get("web_search", "tool"),
            input={"query": query},
            output={
                "status": pipeline_data["web_search_status"],
                "error": pipeline_data.get("web_search_error"),
                "sources": pipeline_data.get("web_sources") or [],
                "n_sources": len(pipeline_data.get("web_sources") or []),
            },
            metadata=meta,
        ))

    # 2. entity_profiling — generation (LLM call 1)
    if pipeline_data.get("entity_profile"):
        profile = pipeline_data["entity_profile"]
        meta = _step_meta(timings, step_params, "entity_profiling", schema)
        nodes.append(PipelineNode(
            name="entity_profiling",
            as_type=lf_types.get("entity_profiling", "generation"),
            input={"query": query},
            output=profile,
            metadata=meta,
            model=llm_provider or None,
        ))

    # 3. token_matching — retriever
    if pipeline_data.get("token_matched_candidates"):
        candidates = pipeline_data["token_matched_candidates"]
        meta = _step_meta(timings, step_params, "token_matching", schema)
        nodes.append(PipelineNode(
            name="token_matching",
            as_type=lf_types.get("token_matching", "retriever"),
            input={
                "query": query,
                "profile": _profile_summary(
                    pipeline_data.get("entity_profile") or {},
                ),
            },
            output={
                "n_candidates": len(candidates),
                "candidates": candidates,
            },
            metadata=meta,
        ))

    # 4. llm_ranking — generation (LLM call 2, conditional)
    if pipeline_data.get("ranked_candidates"):
        ranked = pipeline_data["ranked_candidates"]
        meta = _step_meta(timings, step_params, "llm_ranking", schema)
        nodes.append(PipelineNode(
            name="llm_ranking",
            as_type=lf_types.get("llm_ranking", "generation"),
            input={"n_candidates": len(ranked)},
            output={
                "n_candidates": len(ranked),
                "candidates": ranked,
            },
            metadata=meta,
            model=llm_provider or None,
        ))

    return nodes
