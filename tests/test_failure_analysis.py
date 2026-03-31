"""Tests for Wave 1b failure analysis compilation functions."""

import pytest

from api.models.pipeline_schema import PipelineNode, PipelineSchema
from api.services.metrics import (
    compile_failure_analysis,
    compile_query_difficulty,
    compile_temporal_trends,
)

_TEST_SCHEMA = PipelineSchema(nodes=[
    PipelineNode(name="cache_lookup", node_type="cache"),
    PipelineNode(name="token_matching", node_type="candidate_source"),
    PipelineNode(name="llm_ranking", node_type="ranker"),
])


def _result(query, *, hit, gt_in_source=True, gt_in_ranked=True, terminated_at="llm_ranking"):
    gt = f"gt_{query}"
    src = [(gt, 0.9)] if gt_in_source else [("other", 0.9)]
    ranked = [{"candidate": gt, "score": 0.95}] if gt_in_ranked else [{"candidate": "other", "score": 0.9}]
    return {
        "query": query,
        "predicted": gt if hit else "WRONG",
        "ground_truth": gt,
        "hit": hit,
        "score": 1.0 if hit else 0.0,
        "error": None,
        "pipeline_data": {
            "terminated_at": terminated_at,
            "total_time": 100.0,
            "token_matched_candidates": src,
            "ranked_candidates": ranked,
            "step_timings": {"cache_lookup": None, "token_matching": 0.3, "llm_ranking": 0.5},
            "diagnostics": {},
        },
    }


class TestCompileFailureAnalysis:
    def test_all_hits_returns_empty(self):
        results = [_result(f"q{i}", hit=True) for i in range(5)]
        fa = compile_failure_analysis(results, _TEST_SCHEMA)
        assert fa.total_failures == 0
        assert fa.patterns == []
        assert fa.total_results == 5

    def test_single_failure_pattern(self):
        results = [
            _result("q0", hit=True),
            _result("q1", hit=False, gt_in_source=False),
            _result("q2", hit=False, gt_in_source=False),
        ]
        fa = compile_failure_analysis(results, _TEST_SCHEMA)
        assert fa.total_failures == 2
        assert len(fa.patterns) == 1
        assert fa.patterns[0].query_count == 2
        assert fa.patterns[0].fraction == pytest.approx(1.0)
        assert "source_miss" in fa.patterns[0].name

    def test_multiple_patterns_sorted_by_count(self):
        results = [
            # 3 source misses
            _result("q0", hit=False, gt_in_source=False),
            _result("q1", hit=False, gt_in_source=False),
            _result("q2", hit=False, gt_in_source=False),
            # 1 rank miss (GT in source but not ranked)
            _result("q3", hit=False, gt_in_source=True, gt_in_ranked=False),
        ]
        fa = compile_failure_analysis(results, _TEST_SCHEMA)
        assert len(fa.patterns) == 2
        assert fa.patterns[0].query_count == 3  # source_miss first
        assert fa.patterns[1].query_count == 1  # rank_miss second

    def test_max_patterns_cap(self):
        # Create 6 distinct patterns
        results = [
            _result(f"q{i}", hit=False, gt_in_source=(i % 2 == 0), terminated_at=f"step_{i}")
            for i in range(6)
        ]
        fa = compile_failure_analysis(results, _TEST_SCHEMA, max_patterns=3)
        assert len(fa.patterns) <= 3

    def test_example_queries_capped(self):
        results = [_result(f"q{i}", hit=False, gt_in_source=False) for i in range(10)]
        fa = compile_failure_analysis(results, _TEST_SCHEMA, max_examples=2)
        assert len(fa.patterns[0].example_queries) == 2

    def test_error_results_excluded(self):
        results = [
            _result("q0", hit=False, gt_in_source=False),
            {**_result("q1", hit=False), "error": "timeout"},
        ]
        fa = compile_failure_analysis(results, _TEST_SCHEMA)
        assert fa.total_failures == 1  # error result excluded


class TestCompileQueryDifficulty:
    def test_classification_thresholds(self):
        # 3 rounds: q0 always hits, q1 always misses, q2 mixed
        rounds = [
            [{"query": "q0", "hit": True}, {"query": "q1", "hit": False}, {"query": "q2", "hit": True}],
            [{"query": "q0", "hit": True}, {"query": "q1", "hit": False}, {"query": "q2", "hit": False}],
            [{"query": "q0", "hit": True}, {"query": "q1", "hit": False}, {"query": "q2", "hit": True}],
        ]
        qd = compile_query_difficulty(rounds)
        profiles = {p.query: p for p in qd.profiles}

        assert profiles["q0"].classification == "dead"  # always hit -> dead
        assert profiles["q0"].hit_rate == pytest.approx(1.0)
        assert profiles["q1"].classification == "dead"  # always miss -> dead
        assert profiles["q1"].hit_rate == pytest.approx(0.0)
        assert profiles["q2"].classification == "discriminating"  # 2/3 ≈ 0.67
        assert profiles["q2"].n_evaluations == 3

    def test_empty_input(self):
        qd = compile_query_difficulty([])
        assert qd.profiles == []

    def test_convenience_properties(self):
        rounds = [
            [{"query": "q0", "hit": True}, {"query": "q1", "hit": False}, {"query": "q2", "hit": True}],
            [{"query": "q0", "hit": True}, {"query": "q1", "hit": False}, {"query": "q2", "hit": False}],
        ]
        qd = compile_query_difficulty(rounds)
        assert len(qd.dead) == 2  # q0 always hit, q1 always miss
        assert len(qd.discriminating) == 1  # q2 = 0.5


class TestCompileTemporalTrends:
    def test_improved_and_regressed(self):
        round_results = [
            (1, [{"query": "q0", "hit": False}, {"query": "q1", "hit": True}]),
            (2, [{"query": "q0", "hit": True}, {"query": "q1", "hit": False}]),
        ]
        ta = compile_temporal_trends(round_results)
        assert len(ta.snapshots) == 1
        s = ta.snapshots[0]
        assert s.from_round == 1
        assert s.to_round == 2
        assert "q0" in s.improved_queries
        assert "q1" in s.regressed_queries
        assert s.unchanged_queries == 0

    def test_three_rounds(self):
        round_results = [
            (1, [{"query": "q0", "hit": False}]),
            (2, [{"query": "q0", "hit": True}]),
            (3, [{"query": "q0", "hit": True}]),
        ]
        ta = compile_temporal_trends(round_results)
        assert len(ta.snapshots) == 2
        assert ta.snapshots[0].improved_queries == ["q0"]
        assert ta.snapshots[1].unchanged_queries == 1

    def test_single_round(self):
        ta = compile_temporal_trends([(1, [{"query": "q0", "hit": True}])])
        assert ta.snapshots == []

    def test_empty(self):
        ta = compile_temporal_trends([])
        assert ta.snapshots == []
