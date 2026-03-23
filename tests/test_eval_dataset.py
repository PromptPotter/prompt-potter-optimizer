"""Tests for mapping-driven observation extraction in eval_dataset."""

from unittest.mock import MagicMock

from api.models.pipeline_schema import ObservationMapping, PipelineSchema, PipelineStep
from api.services.search.eval_dataset import (
    _extract_eval_from_traces,
    load_eval_dataset,
)


# Minimal test schema matching TermNorm's observation structure
_TEST_SCHEMA = PipelineSchema(
    name="test",
    required_step="entity_profile",
    steps=[
        PipelineStep(
            name="web_search",
            observation_name="web_search",
            observation_mappings=[
                ObservationMapping(pipeline_key="web_sources", output_field="sources"),
                ObservationMapping(pipeline_key="web_search_status", output_field="status"),
            ],
        ),
        PipelineStep(
            name="entity_profiling",
            observation_name="entity_profiling",
            observation_mappings=[
                ObservationMapping(pipeline_key="entity_profile", is_llm=True),
            ],
        ),
        PipelineStep(
            name="token_matching",
            observation_name="token_matching",
            observation_mappings=[
                ObservationMapping(
                    pipeline_key="token_matched_candidates", output_field="candidates",
                ),
            ],
        ),
        PipelineStep(
            name="llm_ranking",
            observation_name="llm_ranking",
            observation_mappings=[
                ObservationMapping(
                    pipeline_key="ranked_candidates",
                    output_field="ranked_candidates", is_llm=True,
                ),
            ],
        ),
    ],
)


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



def test_full_trace_extracts_all_fields():

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
    result = _extract_eval_from_traces(_make_exp([trace]), schema=_TEST_SCHEMA)

    assert len(result) == 1
    pd = result[0]["pipeline_data"]
    assert pd["entity_profile"] == ENTITY_PROFILE_OUTPUT
    assert pd["token_matched_candidates"] == TOKEN_MATCHING_OUTPUT["candidates"]
    assert pd["ranked_candidates"] == LLM_RANKING_OUTPUT["ranked_candidates"]
    assert pd["web_sources"] == WEB_SEARCH_OUTPUT["sources"]
    assert pd["web_search_status"] == "completed"


def test_minimal_trace_only_required():

    trace = _make_trace(
        query="STEEL-001 / hot rolled",
        observations=[
            _make_obs("entity_profiling", ENTITY_PROFILE_OUTPUT),
        ],
    )
    result = _extract_eval_from_traces(_make_exp([trace]), schema=_TEST_SCHEMA)

    assert len(result) == 1
    pd = result[0]["pipeline_data"]
    assert pd["entity_profile"] == ENTITY_PROFILE_OUTPUT
    assert "token_matched_candidates" not in pd
    assert "ranked_candidates" not in pd
    assert "web_sources" not in pd


def test_optional_observations_missing():

    trace = _make_trace(
        query="STEEL-001 / hot rolled",
        observations=[
            _make_obs("entity_profiling", ENTITY_PROFILE_OUTPUT),
            _make_obs("token_matching", TOKEN_MATCHING_OUTPUT),
        ],
    )
    result = _extract_eval_from_traces(_make_exp([trace]), schema=_TEST_SCHEMA)

    pd = result[0]["pipeline_data"]
    assert "web_sources" not in pd
    assert "web_search_status" not in pd
    assert "ranked_candidates" not in pd
    assert pd["entity_profile"] is not None
    assert pd["token_matched_candidates"] is not None


def test_missing_entity_profile_skips():

    trace = _make_trace(
        query="STEEL-001 / hot rolled",
        observations=[
            _make_obs("token_matching", TOKEN_MATCHING_OUTPUT),
            _make_obs("web_search", WEB_SEARCH_OUTPUT),
        ],
    )
    result = _extract_eval_from_traces(_make_exp([trace]), schema=_TEST_SCHEMA)
    assert len(result) == 0


def test_llm_provider_extraction():

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
    result = _extract_eval_from_traces(_make_exp([trace]), schema=_TEST_SCHEMA)
    assert result[0]["pipeline_data"]["llm_provider"] == "model-a"

    # No metadata -> no llm_provider
    trace2 = _make_trace(
        query="STEEL-001 / hot rolled",
        observations=[
            _make_obs("entity_profiling", ENTITY_PROFILE_OUTPUT),
        ],
    )
    result2 = _extract_eval_from_traces(_make_exp([trace2]), schema=_TEST_SCHEMA)
    assert "llm_provider" not in result2[0]["pipeline_data"]


def test_total_time_from_scores():

    trace = _make_trace(
        query="STEEL-001 / hot rolled",
        observations=[
            _make_obs("entity_profiling", ENTITY_PROFILE_OUTPUT),
        ],
        scores=[
            {"name": "latency_ms", "value": 1500},
        ],
    )
    result = _extract_eval_from_traces(_make_exp([trace]), schema=_TEST_SCHEMA)
    assert result[0]["pipeline_data"]["total_time"] == 1.5


def test_custom_schema_obs_mapping():

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

    result = load_eval_dataset(mock_store, "backend-1", "exp-1", schema=_TEST_SCHEMA)

    assert len(result) == 1
    assert result[0]["query"] == "STEEL-001 / hot rolled"
    assert result[0]["ground_truth"] == "Steel Rod 10mm"
    assert result[0]["pipeline_data"]["entity_profile"] == ENTITY_PROFILE_OUTPUT
