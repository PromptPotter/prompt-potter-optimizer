"""Tests for critique_stats — prompt assembly and inline stat computation."""


from api.services.campaign.critique_stats import (
    CritiqueContext,
    assemble_critique_prompt,
    _find_rank,
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
# _find_rank
# ---------------------------------------------------------------------------


class TestFindRank:
    def test_found_at_position_1(self):
        assert _find_rank([{"candidate": "A"}], "A") == 1

    def test_found_at_position_3(self):
        assert _find_rank(
            [{"candidate": "X"}, {"candidate": "Y"}, {"candidate": "A"}], "A"
        ) == 3

    def test_not_found(self):
        assert _find_rank([{"candidate": "X"}], "A") is None

    def test_empty_candidates(self):
        assert _find_rank([], "A") is None

    def test_empty_gt(self):
        assert _find_rank([{"candidate": "A"}], "") is None


# ---------------------------------------------------------------------------
# Prompt assembly (integration — covers all inline stats)
# ---------------------------------------------------------------------------


class TestAssemblePrompt:
    def test_assembles_with_all_sections(self):
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

    def test_handles_empty_results(self):
        ctx = CritiqueContext(results=[], accuracy=0.0)
        prompt = assemble_critique_prompt(ctx)
        assert "EVALUATION SUMMARY" in prompt

    def test_includes_degradation_anomaly(self):
        results = [_make_result(warnings=["bad"]) for _ in range(5)]
        ctx = CritiqueContext(results=results, accuracy=0.0)
        prompt = assemble_critique_prompt(ctx)
        assert "ANOMALY FLAGS" in prompt
        assert "high_degradation" in prompt

    def test_includes_near_miss_info(self):
        results = [
            _make_result(
                gt="Target",
                candidates=[{"candidate": "Wrong"}, {"candidate": "Target"}],
            ),
        ]
        ctx = CritiqueContext(results=results, accuracy=0.0)
        prompt = assemble_critique_prompt(ctx)
        assert "Near misses" in prompt

    def test_includes_round_evolution(self):
        ctx = CritiqueContext(
            results=[_make_result()],
            accuracy=0.5,
            round_history=[
                {"round": 0, "accuracy": 0.3},
                {"round": 1, "accuracy": 0.5},
            ],
        )
        prompt = assemble_critique_prompt(ctx)
        assert "ROUND EVOLUTION" in prompt

    def test_includes_plateau_anomaly(self):
        ctx = CritiqueContext(
            results=[_make_result()],
            accuracy=0.5,
            round_history=[
                {"round": 0, "accuracy": 0.5},
                {"round": 1, "accuracy": 0.5},
                {"round": 2, "accuracy": 0.505},
            ],
        )
        prompt = assemble_critique_prompt(ctx)
        assert "plateau_signal" in prompt

    def test_termination_distribution(self):
        results = [
            _make_result(terminated_at="fuzzy_matching"),
            _make_result(terminated_at="token_matching"),
            _make_result(terminated_at="token_matching"),
        ]
        ctx = CritiqueContext(results=results, accuracy=0.0)
        prompt = assemble_critique_prompt(ctx)
        assert "token_matching: 2/3" in prompt
        assert "fuzzy_matching: 1/3" in prompt
