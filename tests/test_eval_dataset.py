"""Tests for mapping-driven observation extraction in eval_dataset."""

from unittest.mock import MagicMock

from api.services.pipeline_discovery import TERMNORM_DEFAULT_SCHEMA
from api.services.search.eval_dataset import (
    _extract_eval_from_traces,
    load_eval_dataset,
)


# ---------------------------------------------------------------------------
# Helpers — build trace structures matching synced TermNorm format
# ---------------------------------------------------------------------------

def _make_obs(name: str, output: dict, metadata: dict | None = None) -> dict:
    obs = {"name": name, "output": output}
    if metadata is not None:
        obs["metadata"] = metadata
    return obs


def _make_trace(
    query: str,
    observations: list[dict],
    scores: list[dict] | None = None,
) -> dict:
    trace = {
        "input": {"query": query},
        "observations": observations,
    }
    if scores:
        trace["scores"] = scores
    return trace


def _make_exp(traces: list[dict], mappings: list[dict] | None = None) -> dict:
    if mappings is None:
        mappings = [
            {"bom_material": "STEEL-001", "dataset_entry": "Steel Rod 10mm"},
        ]
    return {"mappings": mappings, "runs": [{"traces": traces}]}


# Default observation outputs (modeled on real TermNorm pipeline data)

ENTITY_PROFILE_OUTPUT = {
    "material_type": "steel",
    "dimensions": "10mm",
    "category": "rod",
}

TOKEN_MATCHING_OUTPUT = {
    "candidates": [
        {"id": "c1", "name": "Steel Rod 10mm", "score": 0.95},
        {"id": "c2", "name": "Steel Bar 10mm", "score": 0.80},
    ],
}

LLM_RANKING_OUTPUT = {
    "ranked_candidates": [
        {"id": "c1", "name": "Steel Rod 10mm", "rank": 1},
        {"id": "c2", "name": "Steel Bar 10mm", "rank": 2},
    ],
}

WEB_SEARCH_OUTPUT = {
    "sources": [
        {"url": "https://example.com", "title": "Steel specs"},
    ],
    "status": "completed",
}


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_full_trace_extracts_all_fields():
    """All 4 observations present -> all pipeline_data keys extracted."""
    trace = _make_trace(
        query="STEEL-001 / hot rolled",
        observations=[
            _make_obs("entity_profiling", ENTITY_PROFILE_OUTPUT,
                      metadata={"model": "llama-4-maverick"}),
            _make_obs("token_matching", TOKEN_MATCHING_OUTPUT),
            _make_obs("llm_ranking", LLM_RANKING_OUTPUT,
                      metadata={"model": "llama-4-maverick"}),
            _make_obs("web_search", WEB_SEARCH_OUTPUT),
        ],
    )
    result = _extract_eval_from_traces(_make_exp([trace]), schema=TERMNORM_DEFAULT_SCHEMA)

    assert len(result) == 1
    pd = result[0]["pipeline_data"]
    assert pd["entity_profile"] == ENTITY_PROFILE_OUTPUT
    assert pd["token_matched_candidates"] == TOKEN_MATCHING_OUTPUT["candidates"]
    assert pd["ranked_candidates"] == LLM_RANKING_OUTPUT["ranked_candidates"]
    assert pd["web_sources"] == WEB_SEARCH_OUTPUT["sources"]
    assert pd["web_search_status"] == "completed"


def test_minimal_trace_only_required():
    """Only entity_profiling present -> minimum viable extraction."""
    trace = _make_trace(
        query="STEEL-001 / hot rolled",
        observations=[
            _make_obs("entity_profiling", ENTITY_PROFILE_OUTPUT),
        ],
    )
    result = _extract_eval_from_traces(_make_exp([trace]), schema=TERMNORM_DEFAULT_SCHEMA)

    assert len(result) == 1
    pd = result[0]["pipeline_data"]
    assert pd["entity_profile"] == ENTITY_PROFILE_OUTPUT
    assert "token_matched_candidates" not in pd
    assert "ranked_candidates" not in pd
    assert "web_sources" not in pd


def test_optional_observations_missing():
    """Missing optional observations (web_search, llm_ranking) produce no keys."""
    trace = _make_trace(
        query="STEEL-001 / hot rolled",
        observations=[
            _make_obs("entity_profiling", ENTITY_PROFILE_OUTPUT),
            _make_obs("token_matching", TOKEN_MATCHING_OUTPUT),
        ],
    )
    result = _extract_eval_from_traces(_make_exp([trace]), schema=TERMNORM_DEFAULT_SCHEMA)

    pd = result[0]["pipeline_data"]
    assert "web_sources" not in pd
    assert "web_search_status" not in pd
    assert "ranked_candidates" not in pd
    assert pd["entity_profile"] is not None
    assert pd["token_matched_candidates"] is not None


def test_missing_entity_profile_skips():
    """Traces without the required observation are skipped."""
    trace = _make_trace(
        query="STEEL-001 / hot rolled",
        observations=[
            _make_obs("token_matching", TOKEN_MATCHING_OUTPUT),
            _make_obs("web_search", WEB_SEARCH_OUTPUT),
        ],
    )
    result = _extract_eval_from_traces(_make_exp([trace]), schema=TERMNORM_DEFAULT_SCHEMA)
    assert len(result) == 0


def test_llm_provider_extraction():
    """LLM provider from metadata: first is_llm obs wins; absent when no metadata."""
    # Provider from first LLM obs
    trace = _make_trace(
        query="STEEL-001 / hot rolled",
        observations=[
            _make_obs("entity_profiling", ENTITY_PROFILE_OUTPUT,
                      metadata={"model": "model-a"}),
            _make_obs("llm_ranking", LLM_RANKING_OUTPUT,
                      metadata={"model": "model-b"}),
        ],
    )
    result = _extract_eval_from_traces(_make_exp([trace]), schema=TERMNORM_DEFAULT_SCHEMA)
    assert result[0]["pipeline_data"]["llm_provider"] == "model-a"

    # No metadata -> no llm_provider
    trace2 = _make_trace(
        query="STEEL-001 / hot rolled",
        observations=[
            _make_obs("entity_profiling", ENTITY_PROFILE_OUTPUT),
        ],
    )
    result2 = _extract_eval_from_traces(_make_exp([trace2]), schema=TERMNORM_DEFAULT_SCHEMA)
    assert "llm_provider" not in result2[0]["pipeline_data"]


def test_total_time_from_scores():
    """latency_ms score -> total_time in seconds."""
    trace = _make_trace(
        query="STEEL-001 / hot rolled",
        observations=[
            _make_obs("entity_profiling", ENTITY_PROFILE_OUTPUT),
        ],
        scores=[
            {"name": "latency_ms", "value": 1500},
        ],
    )
    result = _extract_eval_from_traces(_make_exp([trace]), schema=TERMNORM_DEFAULT_SCHEMA)
    assert result[0]["pipeline_data"]["total_time"] == 1.5


def test_custom_schema_obs_mapping():
    """Schema-driven extraction with a custom step works."""
    from api.models.pipeline_schema import ObservationMapping, PipelineSchema, PipelineStep

    custom_schema = PipelineSchema(
        name="custom",
        required_step="entity_profile",
        steps=[
            PipelineStep(
                name="entity_profiling",
                observation_name="entity_profiling",
                observation_mappings=[
                    ObservationMapping(pipeline_key="entity_profile", is_llm=True),
                ],
            ),
            PipelineStep(
                name="custom_step",
                observation_name="custom_step",
                observation_mappings=[
                    ObservationMapping(pipeline_key="custom_output", output_field="data"),
                ],
            ),
        ],
    )

    trace = _make_trace(
        query="STEEL-001 / hot rolled",
        observations=[
            _make_obs("entity_profiling", ENTITY_PROFILE_OUTPUT),
            _make_obs("custom_step", {"data": [1, 2, 3]}),
        ],
    )
    result = _extract_eval_from_traces(_make_exp([trace]), schema=custom_schema)

    pd = result[0]["pipeline_data"]
    assert pd["custom_output"] == [1, 2, 3]
    assert "token_matched_candidates" not in pd


def test_load_eval_dataset():
    """Full flow through load_eval_dataset() with mock store."""
    trace = _make_trace(
        query="STEEL-001 / hot rolled",
        observations=[
            _make_obs("entity_profiling", ENTITY_PROFILE_OUTPUT),
            _make_obs("token_matching", TOKEN_MATCHING_OUTPUT),
        ],
    )
    exp_data = _make_exp([trace])

    mock_store = MagicMock()
    mock_store.backends.load_sync.return_value = exp_data
    mock_store.executions.list_all.return_value = []

    result = load_eval_dataset(mock_store, "backend-1", "exp-1")

    assert len(result) == 1
    assert result[0]["query"] == "STEEL-001 / hot rolled"
    assert result[0]["ground_truth"] == "Steel Rod 10mm"
    assert result[0]["pipeline_data"]["entity_profile"] == ENTITY_PROFILE_OUTPUT
