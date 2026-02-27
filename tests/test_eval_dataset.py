"""Tests for mapping-driven observation extraction in eval_dataset."""

from unittest.mock import MagicMock

from api.services.search.eval_dataset import (
    OBS_EXTRACTION_MAP,
    REQUIRED_PIPELINE_KEY,
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


class TestFullTraceExtraction:
    """All 4 observations present → all pipeline_data keys extracted."""

    def test_full_trace_extracts_all_fields(self):
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
        result = _extract_eval_from_traces(_make_exp([trace]))

        assert len(result) == 1
        pd = result[0]["pipeline_data"]

        # entity_profiling → full output under entity_profile
        assert pd["entity_profile"] == ENTITY_PROFILE_OUTPUT
        # token_matching → candidates sub-field
        assert pd["token_matched_candidates"] == TOKEN_MATCHING_OUTPUT["candidates"]
        # llm_ranking → ranked_candidates sub-field
        assert pd["ranked_candidates"] == LLM_RANKING_OUTPUT["ranked_candidates"]
        # web_search → two keys
        assert pd["web_sources"] == WEB_SEARCH_OUTPUT["sources"]
        assert pd["web_search_status"] == "completed"


class TestMinimalTrace:
    """Only entity_profiling present — minimum viable extraction."""

    def test_minimal_trace_only_required(self):
        trace = _make_trace(
            query="STEEL-001 / hot rolled",
            observations=[
                _make_obs("entity_profiling", ENTITY_PROFILE_OUTPUT),
            ],
        )
        result = _extract_eval_from_traces(_make_exp([trace]))

        assert len(result) == 1
        pd = result[0]["pipeline_data"]
        assert pd["entity_profile"] == ENTITY_PROFILE_OUTPUT
        assert "token_matched_candidates" not in pd
        assert "ranked_candidates" not in pd
        assert "web_sources" not in pd


class TestOptionalObservations:
    """Missing optional observations produce no keys (not errors)."""

    def test_missing_web_search_ok(self):
        trace = _make_trace(
            query="STEEL-001 / hot rolled",
            observations=[
                _make_obs("entity_profiling", ENTITY_PROFILE_OUTPUT),
                _make_obs("token_matching", TOKEN_MATCHING_OUTPUT),
                _make_obs("llm_ranking", LLM_RANKING_OUTPUT),
            ],
        )
        result = _extract_eval_from_traces(_make_exp([trace]))

        pd = result[0]["pipeline_data"]
        assert "web_sources" not in pd
        assert "web_search_status" not in pd
        assert pd["token_matched_candidates"] is not None

    def test_missing_llm_ranking_ok(self):
        trace = _make_trace(
            query="STEEL-001 / hot rolled",
            observations=[
                _make_obs("entity_profiling", ENTITY_PROFILE_OUTPUT),
                _make_obs("token_matching", TOKEN_MATCHING_OUTPUT),
            ],
        )
        result = _extract_eval_from_traces(_make_exp([trace]))

        pd = result[0]["pipeline_data"]
        assert "ranked_candidates" not in pd
        assert pd["entity_profile"] is not None


class TestGateLogic:
    """Traces without the required observation are skipped."""

    def test_missing_entity_profile_skips(self):
        trace = _make_trace(
            query="STEEL-001 / hot rolled",
            observations=[
                _make_obs("token_matching", TOKEN_MATCHING_OUTPUT),
                _make_obs("web_search", WEB_SEARCH_OUTPUT),
            ],
        )
        result = _extract_eval_from_traces(_make_exp([trace]))
        assert len(result) == 0

    def test_token_matching_without_entity_profile(self):
        """token_matching alone does not satisfy the gate."""
        trace = _make_trace(
            query="STEEL-001 / hot rolled",
            observations=[
                _make_obs("token_matching", TOKEN_MATCHING_OUTPUT),
            ],
        )
        result = _extract_eval_from_traces(_make_exp([trace]))
        assert len(result) == 0


class TestMetadataExtraction:
    """LLM provider and timing metadata extracted correctly."""

    def test_llm_provider_from_metadata(self):
        trace = _make_trace(
            query="STEEL-001 / hot rolled",
            observations=[
                _make_obs("entity_profiling", ENTITY_PROFILE_OUTPUT,
                          metadata={"model": "llama-4-maverick"}),
            ],
        )
        result = _extract_eval_from_traces(_make_exp([trace]))

        assert result[0]["pipeline_data"]["llm_provider"] == "llama-4-maverick"

    def test_llm_provider_from_first_llm_obs(self):
        """Provider comes from the first is_llm observation encountered."""
        trace = _make_trace(
            query="STEEL-001 / hot rolled",
            observations=[
                _make_obs("entity_profiling", ENTITY_PROFILE_OUTPUT,
                          metadata={"model": "model-a"}),
                _make_obs("llm_ranking", LLM_RANKING_OUTPUT,
                          metadata={"model": "model-b"}),
            ],
        )
        result = _extract_eval_from_traces(_make_exp([trace]))

        assert result[0]["pipeline_data"]["llm_provider"] == "model-a"

    def test_no_llm_provider_when_no_metadata(self):
        trace = _make_trace(
            query="STEEL-001 / hot rolled",
            observations=[
                _make_obs("entity_profiling", ENTITY_PROFILE_OUTPUT),
            ],
        )
        result = _extract_eval_from_traces(_make_exp([trace]))

        assert "llm_provider" not in result[0]["pipeline_data"]

    def test_total_time_from_scores(self):
        trace = _make_trace(
            query="STEEL-001 / hot rolled",
            observations=[
                _make_obs("entity_profiling", ENTITY_PROFILE_OUTPUT),
            ],
            scores=[
                {"name": "latency_ms", "value": 1500},
            ],
        )
        result = _extract_eval_from_traces(_make_exp([trace]))

        assert result[0]["pipeline_data"]["total_time"] == 1.5


class TestMappingIsUsed:
    """Verify extraction is driven by OBS_EXTRACTION_MAP, not hardcoded names."""

    def test_custom_obs_mapping(self, monkeypatch):
        """Patching OBS_EXTRACTION_MAP with a custom entry works."""
        from api.services.search import eval_dataset

        custom_map = {
            "entity_profiling": OBS_EXTRACTION_MAP["entity_profiling"],
            "custom_step": [
                eval_dataset._ObsField("custom_output", output_field="data"),
            ],
        }
        monkeypatch.setattr(eval_dataset, "OBS_EXTRACTION_MAP", custom_map)

        trace = _make_trace(
            query="STEEL-001 / hot rolled",
            observations=[
                _make_obs("entity_profiling", ENTITY_PROFILE_OUTPUT),
                _make_obs("custom_step", {"data": [1, 2, 3]}),
            ],
        )
        result = _extract_eval_from_traces(_make_exp([trace]))

        pd = result[0]["pipeline_data"]
        assert pd["custom_output"] == [1, 2, 3]
        # Original hardcoded names should NOT produce output
        assert "token_matched_candidates" not in pd

    def test_required_pipeline_key_constant(self):
        """REQUIRED_PIPELINE_KEY matches the expected entity_profile key."""
        assert REQUIRED_PIPELINE_KEY == "entity_profile"


class TestLoadEvalDatasetIntegration:
    """Full flow through load_eval_dataset() with mock store."""

    def test_load_eval_dataset_from_traces(self):
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

    def test_load_eval_dataset_empty_traces(self):
        mock_store = MagicMock()
        mock_store.backends.load_sync.return_value = {
            "mappings": [], "runs": [{"traces": []}],
        }
        mock_store.executions.list_all.return_value = []

        result = load_eval_dataset(mock_store, "backend-1", "exp-1")
        assert result == []


class TestEdgeCases:
    """Edge cases and boundary conditions."""

    def test_empty_observations_list(self):
        trace = _make_trace(
            query="STEEL-001 / hot rolled",
            observations=[],
        )
        result = _extract_eval_from_traces(_make_exp([trace]))
        assert len(result) == 0

    def test_observation_with_empty_output(self):
        """Observations with falsy output are skipped."""
        trace = _make_trace(
            query="STEEL-001 / hot rolled",
            observations=[
                _make_obs("entity_profiling", {}),  # empty dict is falsy-like but truthy
            ],
        )
        # empty dict is truthy in Python, but has no useful content
        # The gate checks pipeline_data.get("entity_profile") which will be {}
        # {} is falsy, so trace should be skipped
        result = _extract_eval_from_traces(_make_exp([trace]))
        assert len(result) == 0

    def test_observation_with_none_output(self):
        """Observations with None output are skipped in the loop."""
        trace = _make_trace(
            query="STEEL-001 / hot rolled",
            observations=[
                {"name": "entity_profiling", "output": None},
            ],
        )
        result = _extract_eval_from_traces(_make_exp([trace]))
        assert len(result) == 0

    def test_unknown_observation_ignored(self):
        """Observations not in OBS_EXTRACTION_MAP are silently skipped."""
        trace = _make_trace(
            query="STEEL-001 / hot rolled",
            observations=[
                _make_obs("entity_profiling", ENTITY_PROFILE_OUTPUT),
                _make_obs("unknown_step", {"foo": "bar"}),
            ],
        )
        result = _extract_eval_from_traces(_make_exp([trace]))

        assert len(result) == 1
        pd = result[0]["pipeline_data"]
        assert "foo" not in pd

    def test_latency_zero_not_added(self):
        """latency_ms with value 0 is falsy — total_time not added."""
        trace = _make_trace(
            query="STEEL-001 / hot rolled",
            observations=[
                _make_obs("entity_profiling", ENTITY_PROFILE_OUTPUT),
            ],
            scores=[{"name": "latency_ms", "value": 0}],
        )
        result = _extract_eval_from_traces(_make_exp([trace]))
        assert "total_time" not in result[0]["pipeline_data"]
