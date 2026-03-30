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

    def test_token_recall_all_found(self):
        results = _make_results(2, 4, gt_in_candidates=True)
        scores = compute_composite_score(results)
        assert scores["token_recall"] == pytest.approx(1.0)

    def test_token_recall_none_found(self):
        results = _make_results(0, 4, gt_in_candidates=False)
        scores = compute_composite_score(results)
        assert scores["token_recall"] == pytest.approx(0.0)

    def test_composite_formula(self):
        results = _make_results(3, 5, gt_in_candidates=True)
        scores = compute_composite_score(results, accuracy_weight=0.8)
        expected = 0.8 * 0.6 + 0.2 * 1.0  # acc=0.6, recall=1.0
        assert scores["composite"] == pytest.approx(expected, abs=1e-5)

    def test_non_llm_queries_excluded_from_token_recall(self):
        results = _make_results(2, 4, terminated_at="fuzzy_matching")
        scores = compute_composite_score(results)
        # No queries reached llm_ranking -> token_recall = 0
        assert scores["token_recall"] == pytest.approx(0.0)
        # composite = 0.9 * accuracy + 0.1 * 0.0
        assert scores["composite"] == pytest.approx(0.9 * 0.5)

    def test_empty_results(self):
        scores = compute_composite_score([])
        assert scores["accuracy"] == 0.0
        assert scores["composite"] == 0.0

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



class TestNodeType:
    def test_pipeline_step_has_node_type(self):
        step = PipelineNode(name="test", node_type="ranker")
        assert step.node_type == "ranker"

    def test_pipeline_step_default_empty(self):
        step = PipelineNode(name="test")
        assert step.node_type == ""

    def test_schema_node_types(self):
        schema = PipelineSchema(nodes=[
            PipelineNode(name="cache_lookup", node_type="cache"),
            PipelineNode(name="fuzzy_matching", node_type="candidate_source"),
            PipelineNode(name="web_search", node_type="enricher"),
            PipelineNode(name="entity_profiling", node_type="enricher"),
            PipelineNode(name="token_matching", node_type="candidate_source"),
            PipelineNode(name="llm_ranking", node_type="ranker"),
        ])
        types = {s.name: s.node_type for s in schema.nodes}
        assert types["cache_lookup"] == "cache"
        assert types["fuzzy_matching"] == "candidate_source"
        assert types["web_search"] == "enricher"
        assert types["entity_profiling"] == "enricher"
        assert types["token_matching"] == "candidate_source"
        assert types["llm_ranking"] == "ranker"


class TestIntermediateMetric:
    def test_frozen(self):
        m = IntermediateMetric(
            name="test", node_type="ranker", pipeline_data_key="ranked_candidates",
        )
        with pytest.raises(pydantic.ValidationError):
            m.name = "changed"

    def test_registry_has_expected_types(self):
        assert "candidate_source" in NODE_TYPE_METRICS
        assert "ranker" in NODE_TYPE_METRICS
        assert "cache" in NODE_TYPE_METRICS
        assert "enricher" in NODE_TYPE_METRICS

    def test_source_recall_metric_exists(self):
        metrics = NODE_TYPE_METRICS["candidate_source"]
        assert any(m.name == "source_recall" for m in metrics)



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
        assert "candidate_recall" in metrics
        assert metrics["source_recall"] == pytest.approx(1.0)

    def test_no_roles_returns_accuracy_only(self):
        schema = PipelineSchema(nodes=[
            PipelineNode(name="step1"),
        ])
        results = _make_results(2, 4)
        metrics = compute_pipeline_metrics(schema, results)
        assert metrics["accuracy"] == pytest.approx(0.5)
        assert "composite" in metrics

    def test_namespaced_when_multiple_same_role(self):
        schema = PipelineSchema(nodes=[
            PipelineNode(name="fuzzy_matching", node_type="candidate_source"),
            PipelineNode(name="token_matching", node_type="candidate_source"),
        ])
        results = _make_results(3, 5)
        metrics = compute_pipeline_metrics(schema, results)
        has_ns = ("fuzzy_matching_source_recall" in metrics
                  or "token_matching_source_recall" in metrics)
        assert has_ns

    def test_empty_results(self):
        schema = PipelineSchema(nodes=[
            PipelineNode(name="llm_ranking", node_type="ranker"),
        ])
        metrics = compute_pipeline_metrics(schema, [])
        assert metrics["accuracy"] == 0.0
        assert metrics["composite"] == 0.0

    def test_cache_hit_rate(self):
        schema = PipelineSchema(nodes=[
            PipelineNode(name="cache_lookup", node_type="cache"),
        ])
        results = _make_results(2, 4)
        metrics = compute_pipeline_metrics(schema, results)
        assert "cache_hit_rate" in metrics
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
        expected = 0.95 * 0.6 + 0.05 * 1.0
        assert metrics["composite"] == pytest.approx(expected, abs=1e-5)
