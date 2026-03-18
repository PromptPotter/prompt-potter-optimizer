"""Tests for critique_stats — stat computers and anomaly detection."""

import pytest

from api.services.campaign.critique_stats import (
    CritiqueContext,
    assemble_critique_prompt,
    compute_pipeline_health,
    compute_query_categories,
    compute_rank_analysis,
    compute_round_evolution,
    detect_anomalies,
)


def _make_result(
    query: str = "test",
    gt: str = "GT",
    hit: bool = False,
    error: str | None = None,
    terminated_at: str = "token_matching",
    candidates: list | None = None,
    warnings: list | None = None,
    total_time: float = 100.0,
) -> dict:
    pd = {
        "terminated_at": terminated_at,
        "total_time": total_time,
        "ranked_candidates": candidates or [],
        "diagnostics": {"warnings": warnings or []},
    }
    return {
        "query": query,
        "predicted": "PRED",
        "ground_truth": gt,
        "hit": hit,
        "score": 1.0 if hit else 0.0,
        "error": error,
        "pipeline_data": pd,
    }


# ---------------------------------------------------------------------------
# Pipeline health
# ---------------------------------------------------------------------------


class TestPipelineHealth:
    def test_empty_results(self):
        assert compute_pipeline_health([]) == {}

    def test_degradation_rate(self):
        results = [
            _make_result(warnings=["2 of 14 fetched URLs returned content"]),
            _make_result(warnings=[]),
            _make_result(warnings=["0 of 10 fetched URLs returned content"]),
        ]
        health = compute_pipeline_health(results)
        assert health["web_search_degradation_rate"] == pytest.approx(2 / 3)
        assert health["degraded_count"] == 2
        assert health["total"] == 3

    def test_termination_distribution(self):
        results = [
            _make_result(terminated_at="fuzzy_matching"),
            _make_result(terminated_at="token_matching"),
            _make_result(terminated_at="token_matching"),
        ]
        health = compute_pipeline_health(results)
        assert health["termination_distribution"]["token_matching"] == 2
        assert health["termination_distribution"]["fuzzy_matching"] == 1

    def test_timing_stats(self):
        results = [
            _make_result(total_time=100),
            _make_result(total_time=200),
            _make_result(total_time=300),
        ]
        health = compute_pipeline_health(results)
        assert health["timing_stats"]["p50_ms"] == 200
        assert health["timing_stats"]["max_ms"] == 300

    def test_error_rate(self):
        results = [
            _make_result(error="timeout"),
            _make_result(),
            _make_result(),
        ]
        health = compute_pipeline_health(results)
        assert health["error_rate"] == pytest.approx(1 / 3)


# ---------------------------------------------------------------------------
# Rank analysis
# ---------------------------------------------------------------------------


class TestRankAnalysis:
    def test_near_misses(self):
        results = [
            _make_result(
                gt="Target",
                candidates=[
                    {"candidate": "Wrong1"},
                    {"candidate": "Wrong2"},
                    {"candidate": "Target"},
                ],
            ),
        ]
        ranks = compute_rank_analysis(results)
        assert ranks["rank_distribution"]["2-5"] == 1
        assert len(ranks["near_misses"]) == 1
        assert ranks["near_misses"][0]["rank"] == 3

    def test_not_found(self):
        results = [_make_result(gt="Missing", candidates=[{"candidate": "Other"}])]
        ranks = compute_rank_analysis(results)
        assert ranks["rank_distribution"]["not_found"] == 1

    def test_top_k_recall(self):
        results = [
            _make_result(gt="A", candidates=[{"candidate": "A"}]),
            _make_result(gt="B", candidates=[
                {"candidate": "X"}, {"candidate": "B"},
            ]),
            _make_result(gt="C", candidates=[]),
        ]
        ranks = compute_rank_analysis(results)
        assert ranks["top_k_recall"][1] == pytest.approx(1 / 3)
        assert ranks["top_k_recall"][3] == pytest.approx(2 / 3)


# ---------------------------------------------------------------------------
# Round evolution
# ---------------------------------------------------------------------------


class TestRoundEvolution:
    def test_empty(self):
        assert compute_round_evolution([]) == {}

    def test_trajectory(self):
        history = [
            {"round": 0, "accuracy": 0.5, "composite": 0.5},
            {"round": 1, "accuracy": 0.6, "composite": 0.6},
            {"round": 2, "accuracy": 0.6, "composite": 0.6},
        ]
        evo = compute_round_evolution(history)
        assert len(evo["trajectory"]) == 3
        assert evo["trajectory"][1]["delta"] == pytest.approx(0.1)
        assert evo["trajectory"][2]["delta"] == pytest.approx(0.0)

    def test_plateau_detection(self):
        history = [
            {"round": 0, "accuracy": 0.5},
            {"round": 1, "accuracy": 0.5},
            {"round": 2, "accuracy": 0.505},
        ]
        evo = compute_round_evolution(history)
        assert evo["plateau_rounds"] >= 2

    def test_param_changes(self):
        history = [
            {"round": 0, "accuracy": 0.5, "pipeline_params": {"temp": 0.3}},
            {"round": 1, "accuracy": 0.6, "pipeline_params": {"temp": 0.7}},
        ]
        evo = compute_round_evolution(history)
        assert len(evo["param_changes"]) == 1
        assert "temp" in evo["param_changes"][0]["changed_params"]


# ---------------------------------------------------------------------------
# Query categories
# ---------------------------------------------------------------------------


class TestQueryCategories:
    def test_failures_by_step(self):
        results = [
            _make_result(query="q1", terminated_at="fuzzy_matching"),
            _make_result(query="q2", terminated_at="token_matching"),
            _make_result(query="q3", hit=True),
        ]
        cats = compute_query_categories(results)
        assert cats["failures_by_step"]["fuzzy_matching"] == 1
        assert cats["failures_by_step"]["token_matching"] == 1
        assert len(cats["hit_queries"]) == 1

    def test_blindspot_terms(self):
        results = [
            _make_result(query="Copper Wire/cold forming"),
            _make_result(query="Copper Strip EN"),
            _make_result(query="Copper alloy tube"),
        ]
        cats = compute_query_categories(results)
        blindspots = dict(cats["blindspot_terms"])
        assert "Copper" in blindspots
        assert blindspots["Copper"] == 3


# ---------------------------------------------------------------------------
# Anomaly detection
# ---------------------------------------------------------------------------


class TestAnomalyDetection:
    def test_high_degradation(self):
        results = [_make_result(warnings=["bad"]) for _ in range(5)]
        ctx = CritiqueContext(results=results, accuracy=0.0)
        health = compute_pipeline_health(results)
        flags = detect_anomalies(ctx, health, {}, {})
        names = [f.name for f in flags]
        assert "high_degradation" in names

    def test_plateau_signal(self):
        ctx = CritiqueContext(results=[], accuracy=0.5)
        evolution = {"plateau_rounds": 3}
        flags = detect_anomalies(ctx, {}, {}, evolution)
        names = [f.name for f in flags]
        assert "plateau_signal" in names

    def test_near_miss_pattern(self):
        misses = [_make_result() for _ in range(10)]
        ctx = CritiqueContext(results=misses, accuracy=0.0)
        ranks = {"near_misses": [{"rank": 3}] * 5}
        flags = detect_anomalies(ctx, {}, ranks, {})
        names = [f.name for f in flags]
        assert "near_miss_pattern" in names

    def test_no_flags_on_clean_data(self):
        results = [_make_result(hit=True) for _ in range(10)]
        ctx = CritiqueContext(results=results, accuracy=1.0)
        health = compute_pipeline_health(results)
        ranks = compute_rank_analysis(results)
        flags = detect_anomalies(ctx, health, ranks, {})
        assert len(flags) == 0


# ---------------------------------------------------------------------------
# Prompt assembly
# ---------------------------------------------------------------------------


class TestAssemblePrompt:
    def test_assembles_without_error(self):
        results = [
            _make_result(query="q1", hit=True),
            _make_result(query="q2", gt="Target", candidates=[{"candidate": "Wrong"}]),
        ]
        ctx = CritiqueContext(
            results=results,
            accuracy=0.5,
            composite=0.45,
            degraded_queries=1,
            round_history=[
                {"round": 0, "accuracy": 0.3, "composite": 0.3},
            ],
            current_round=1,
            stall_count=0,
            best_accuracy=0.5,
            best_round=1,
            scan_context={
                "leaderboard_text": "thinking_style  'Analytical'  acc=85%",
                "sensitivity_text": "1. thinking_style  sensitivity=0.05",
                "improving_axes": ["thinking_style"],
            },
        )
        prompt = assemble_critique_prompt(ctx)
        assert "EVALUATION SUMMARY" in prompt
        assert "PIPELINE HEALTH" in prompt
        assert "CANDIDATE RANK ANALYSIS" in prompt
        assert "SCAN CONTEXT" in prompt
        assert "FAILURE DETAILS" in prompt
        assert "SUCCESS DETAILS" in prompt
        assert len(prompt) < 100_000  # under 100k token budget

    def test_handles_empty_results(self):
        ctx = CritiqueContext(results=[], accuracy=0.0)
        prompt = assemble_critique_prompt(ctx)
        assert "EVALUATION SUMMARY" in prompt
