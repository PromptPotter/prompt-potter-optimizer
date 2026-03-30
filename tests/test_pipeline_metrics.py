"""Tests for pipeline metrics: composite scoring, node types, and compute_pipeline_metrics().

Merged from test_composite_score.py and test_node_type_metrics.py.
"""

import pydantic
import pytest

from api.models.pipeline_schema import (
    NODE_TYPE_METRICS,
    IntermediateMetric,
    PipelineNode,
    PipelineSchema,
)
from api.services.metrics import compute_composite_score, compute_pipeline_metrics


def _make_results(hits, total, *, terminated_at="llm_ranking", gt_in_candidates=True):
    """Build synthetic eval results with pipeline_data."""
    results = []
    for i in range(total):
        hit = i < hits
        gt = f"gt_{i}"
        candidates = [(gt, 0.9)] if gt_in_candidates else [("other", 0.9)]
        results.append({
            "query": f"q{i}",
            "predicted": gt if hit else "WRONG",
            "ground_truth": gt,
            "hit": hit,
            "score": 1.0 if hit else 0.0,
            "error": None,
            "pipeline_data": {
                "terminated_at": terminated_at,
                "token_matched_candidates": candidates,
                "ranked_candidates": [{"candidate": gt if hit else "WRONG"}],
                "step_timings": {
                    "cache_lookup": None,
                    "token_matching": 0.3,
                    "llm_ranking": 0.5,
                },
            },
        })
    return results



class TestComputeCompositeScore:
    def test_basic_with_no_schema(self):
        results = _make_results(3, 5)
        scores = compute_composite_score(results)
        assert scores["hits"] == 3
        assert scores["total"] == 5
        assert scores["accuracy"] == pytest.approx(0.6)
        assert "composite" in scores
        assert "token_recall" in scores

    @pytest.mark.parametrize("gt_in_candidates,expected_recall", [
        (True, 1.0),
        (False, 0.0),
    ])
    def test_token_recall(self, gt_in_candidates, expected_recall):
        results = _make_results(2, 4, gt_in_candidates=gt_in_candidates)
        scores = compute_composite_score(results)
        assert scores["token_recall"] == pytest.approx(expected_recall)

    def test_composite_formula(self):
        results = _make_results(3, 5, gt_in_candidates=True)
        scores = compute_composite_score(results, accuracy_weight=0.8)
        expected = 0.8 * 0.6 + 0.2 * 1.0  # acc=0.6, recall=1.0
        assert scores["composite"] == pytest.approx(expected, abs=1e-5)

    def test_non_llm_queries_excluded_from_token_recall(self):
        results = _make_results(2, 4, terminated_at="fuzzy_matching")
        scores = compute_composite_score(results)
        assert scores["token_recall"] == pytest.approx(0.0)
        assert scores["composite"] == pytest.approx(0.9 * 0.5)

    @pytest.mark.parametrize("hits,total,expected_acc", [
        (3, 5, 0.6),
        (0, 0, 0.0),
    ])
    def test_accuracy(self, hits, total, expected_acc):
        results = _make_results(hits, total)
        scores = compute_composite_score(results)
        assert scores["accuracy"] == pytest.approx(expected_acc)

    def test_with_pipeline_schema(self):
        schema = PipelineSchema(nodes=[
            PipelineNode(name="cache_lookup", node_type="cache"),
            PipelineNode(name="token_matching", node_type="candidate_source"),
            PipelineNode(name="llm_ranking", node_type="ranker"),
        ])
        results = _make_results(3, 5)
        scores = compute_composite_score(results, schema)
        assert "composite" in scores
        assert scores["composite"] > 0


class TestIntermediateMetric:
    def test_frozen(self):
        m = IntermediateMetric(
            name="test", node_type="ranker", pipeline_data_key="ranked_candidates",
        )
        with pytest.raises(pydantic.ValidationError):
            m.name = "changed"

    def test_registry_has_expected_types(self):
        for t in ("candidate_source", "ranker", "cache", "enricher"):
            assert t in NODE_TYPE_METRICS
        assert any(m.name == "source_recall" for m in NODE_TYPE_METRICS["candidate_source"])



class TestDeriveMetrics:
    def test_basic_derive(self):
        schema = PipelineSchema(nodes=[
            PipelineNode(name="token_matching", node_type="candidate_source"),
            PipelineNode(name="llm_ranking", node_type="ranker"),
        ])
        results = _make_results(3, 5, gt_in_candidates=True)
        metrics = compute_pipeline_metrics(schema, results)
        assert "composite" in metrics
        assert "source_recall" in metrics
        assert metrics["source_recall"] == pytest.approx(1.0)

    def test_no_roles_returns_accuracy_only(self):
        schema = PipelineSchema(nodes=[PipelineNode(name="step1")])
        results = _make_results(2, 4)
        metrics = compute_pipeline_metrics(schema, results)
        assert metrics["accuracy"] == pytest.approx(0.5)
        assert "composite" in metrics

    def test_namespaced_when_multiple_same_role(self):
        schema = PipelineSchema(nodes=[
            PipelineNode(name="fuzzy_matching", node_type="candidate_source"),
            PipelineNode(name="token_matching", node_type="candidate_source"),
        ])
        metrics = compute_pipeline_metrics(schema, _make_results(3, 5))
        assert any(k.endswith("_source_recall") for k in metrics)

    def test_cache_hit_rate(self):
        schema = PipelineSchema(nodes=[
            PipelineNode(name="cache_lookup", node_type="cache"),
        ])
        metrics = compute_pipeline_metrics(schema, _make_results(2, 4))
        assert metrics["cache_hit_rate"] == pytest.approx(0.0)

    def test_custom_weights(self):
        schema = PipelineSchema(nodes=[
            PipelineNode(name="token_matching", node_type="candidate_source"),
        ])
        results = _make_results(3, 5, gt_in_candidates=True)
        metrics = compute_pipeline_metrics(
            schema, results,
            metric_weights={"source_recall": 0.05},
            accuracy_weight=0.95,
        )
        assert metrics["composite"] == pytest.approx(0.95 * 0.6 + 0.05 * 1.0, abs=1e-5)
