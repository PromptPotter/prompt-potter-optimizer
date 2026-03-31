"""Tests for extract_sample_diagnostics() — Wave 1a per-query diagnostic extraction."""

import pytest

from api.models.pipeline_schema import (
    ObservationMapping,
    PipelineNode,
    PipelineSchema,
)
from api.services.metrics import extract_sample_diagnostics

_TEST_SCHEMA = PipelineSchema(nodes=[
    PipelineNode(name="cache_lookup", node_type="cache"),
    PipelineNode(name="token_matching", node_type="candidate_source"),
    PipelineNode(name="llm_ranking", node_type="ranker"),
])


def _make_result(
    *,
    hit: bool = True,
    ground_truth: str = "aspirin",
    predicted: str | None = None,
    terminated_at: str = "llm_ranking",
    source_candidates: list | None = None,
    ranked_candidates: list | None = None,
    step_timings: dict | None = None,
    error: str | None = None,
    diagnostics: dict | None = None,
    total_time: float = 150.0,
) -> dict:
    if predicted is None:
        predicted = ground_truth if hit else "WRONG"
    if source_candidates is None:
        source_candidates = [("aspirin", 0.9), ("ibuprofen", 0.7)]
    if ranked_candidates is None:
        ranked_candidates = [
            {"candidate": "aspirin", "score": 0.95},
            {"candidate": "ibuprofen", "score": 0.80},
        ]
    if step_timings is None:
        step_timings = {
            "cache_lookup": None,
            "token_matching": 0.3,
            "llm_ranking": 0.5,
        }
    return {
        "query": "test query",
        "predicted": predicted,
        "ground_truth": ground_truth,
        "hit": hit,
        "score": 1.0 if hit else 0.0,
        "error": error,
        "pipeline_data": {
            "terminated_at": terminated_at,
            "total_time": total_time,
            "token_matched_candidates": source_candidates,
            "ranked_candidates": ranked_candidates,
            "step_timings": step_timings,
            "diagnostics": diagnostics or {},
        },
    }


class TestInfrastructureDiagnostics:
    def test_basic_fields(self):
        result = _make_result()
        diag = extract_sample_diagnostics(result, _TEST_SCHEMA)
        assert diag["terminated_at"] == "llm_ranking"
        assert diag["total_time_ms"] == 150.0
        assert diag["degraded"] is False
        assert diag["error"] is False

    def test_error_result(self):
        result = _make_result(error="connection timeout")
        diag = extract_sample_diagnostics(result, _TEST_SCHEMA)
        assert diag["error"] is True

    def test_degraded_result(self):
        result = _make_result(diagnostics={"warnings": ["slow response"]})
        diag = extract_sample_diagnostics(result, _TEST_SCHEMA)
        assert diag["degraded"] is True

    def test_missing_pipeline_data(self):
        result = {"query": "q", "ground_truth": "gt", "error": "fail", "pipeline_data": None}
        diag = extract_sample_diagnostics(result, _TEST_SCHEMA)
        assert diag["error"] is True
        assert diag["terminated_at"] is None
        # No node-type diagnostics when pipeline_data is missing
        assert "gt_in_source" not in diag


class TestCandidateSourceDiagnostics:
    def test_gt_in_source(self):
        result = _make_result()
        diag = extract_sample_diagnostics(result, _TEST_SCHEMA)
        assert diag["gt_in_source"] is True
        assert diag["n_source_candidates"] == 2
        assert diag["gt_source_rank"] == 0  # 0-based position

    def test_gt_not_in_source(self):
        result = _make_result(
            hit=False,
            ground_truth="paracetamol",
            source_candidates=[("aspirin", 0.9), ("ibuprofen", 0.7)],
        )
        diag = extract_sample_diagnostics(result, _TEST_SCHEMA)
        assert diag["gt_in_source"] is False
        assert diag["gt_source_rank"] is None

    def test_gt_at_later_position(self):
        result = _make_result(
            source_candidates=[("ibuprofen", 0.9), ("other", 0.8), ("aspirin", 0.7)],
        )
        diag = extract_sample_diagnostics(result, _TEST_SCHEMA)
        assert diag["gt_in_source"] is True
        assert diag["gt_source_rank"] == 2

    def test_empty_candidates(self):
        result = _make_result(source_candidates=[])
        diag = extract_sample_diagnostics(result, _TEST_SCHEMA)
        assert diag["gt_in_source"] is False
        assert diag["n_source_candidates"] == 0


class TestRankerDiagnostics:
    def test_gt_ranked_first(self):
        result = _make_result()
        diag = extract_sample_diagnostics(result, _TEST_SCHEMA)
        assert diag["gt_in_ranked"] is True
        assert diag["gt_rank"] == 0
        assert diag["n_ranked_candidates"] == 2
        assert diag["top_score_gap"] == pytest.approx(0.15)

    def test_gt_not_ranked(self):
        result = _make_result(
            hit=False,
            ground_truth="paracetamol",
            ranked_candidates=[
                {"candidate": "aspirin", "score": 0.95},
                {"candidate": "ibuprofen", "score": 0.80},
            ],
        )
        diag = extract_sample_diagnostics(result, _TEST_SCHEMA)
        assert diag["gt_in_ranked"] is False
        assert diag["gt_rank"] is None

    def test_single_ranked_candidate(self):
        result = _make_result(
            ranked_candidates=[{"candidate": "aspirin", "score": 0.95}],
        )
        diag = extract_sample_diagnostics(result, _TEST_SCHEMA)
        assert diag["n_ranked_candidates"] == 1
        assert diag["top_score_gap"] is None


class TestCacheDiagnostics:
    def test_cache_miss(self):
        result = _make_result(step_timings={"cache_lookup": None, "token_matching": 0.3})
        diag = extract_sample_diagnostics(result, _TEST_SCHEMA)
        assert diag["cache_hit"] is False

    def test_cache_hit(self):
        result = _make_result(
            terminated_at="cache_lookup",
            step_timings={"cache_lookup": 0.01},
        )
        diag = extract_sample_diagnostics(result, _TEST_SCHEMA)
        assert diag["cache_hit"] is True


class TestEnricherDiagnostics:
    def test_enricher_with_mappings(self):
        schema = PipelineSchema(nodes=[
            PipelineNode(
                name="entity_profiling",
                node_type="enricher",
                observation_mappings=[
                    ObservationMapping(pipeline_key="entity_data"),
                    ObservationMapping(pipeline_key="profile_data"),
                ],
            ),
        ])
        result = _make_result()
        result["pipeline_data"]["entity_data"] = {"type": "drug"}
        result["pipeline_data"]["profile_data"] = None
        diag = extract_sample_diagnostics(result, schema)
        assert diag["n_enriched_fields"] == 1  # only entity_data is non-None

    def test_enricher_no_mappings(self):
        schema = PipelineSchema(nodes=[
            PipelineNode(name="entity_profiling", node_type="enricher"),
        ])
        diag = extract_sample_diagnostics(_make_result(), schema)
        assert diag["n_enriched_fields"] == 0


class TestNamespacing:
    def test_single_type_no_namespace(self):
        schema = PipelineSchema(nodes=[
            PipelineNode(name="token_matching", node_type="candidate_source"),
        ])
        diag = extract_sample_diagnostics(_make_result(), schema)
        assert "gt_in_source" in diag
        assert "token_matching_gt_in_source" not in diag

    def test_multiple_same_type_namespaced(self):
        schema = PipelineSchema(nodes=[
            PipelineNode(name="fuzzy_matching", node_type="candidate_source"),
            PipelineNode(name="token_matching", node_type="candidate_source"),
        ])
        diag = extract_sample_diagnostics(_make_result(), schema)
        assert "fuzzy_matching_gt_in_source" in diag
        assert "token_matching_gt_in_source" in diag
        assert "gt_in_source" not in diag


class TestNoTypedNodes:
    def test_untyped_schema_returns_infra_only(self):
        schema = PipelineSchema(nodes=[PipelineNode(name="step1")])
        diag = extract_sample_diagnostics(_make_result(), schema)
        assert "terminated_at" in diag
        assert "gt_in_source" not in diag
        assert "gt_in_ranked" not in diag
