"""Tests for compute_composite_score() and _ensure_composite()."""

import pytest

from api.services.prompt_eval import (
    _ensure_composite,
    compute_composite_score,
)


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
                "step_timings": {"llm_ranking": 0.5, "token_matching": 0.3},
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
        # No queries reached llm_ranking → token_recall = 0
        assert scores["token_recall"] == pytest.approx(0.0)
        # composite = 0.9 * accuracy + 0.1 * 0.0
        assert scores["composite"] == pytest.approx(0.9 * 0.5)

    def test_empty_results(self):
        scores = compute_composite_score([])
        assert scores["accuracy"] == 0.0
        assert scores["composite"] == 0.0

    def test_with_pipeline_schema(self):
        from api.services.pipeline_discovery import TERMNORM_DEFAULT_SCHEMA
        results = _make_results(3, 5)
        scores = compute_composite_score(results, TERMNORM_DEFAULT_SCHEMA)
        assert "composite" in scores
        assert scores["composite"] > 0


class TestEnsureComposite:
    def test_already_has_composite(self):
        scores = {"accuracy": 0.5, "composite": 0.6}
        assert _ensure_composite(scores) is scores

    def test_missing_composite_no_results(self):
        scores = {"accuracy": 0.5, "hits": 2, "total": 4, "errors": 0}
        result = _ensure_composite(scores)
        assert result["composite"] == 0.5
        assert result["token_recall"] == 0.0

    def test_missing_composite_with_results(self):
        results = _make_results(3, 5)
        scores = {"accuracy": 0.6, "hits": 3, "total": 5, "errors": 0}
        result = _ensure_composite(scores, results)
        assert "composite" in result
        assert result["composite"] > 0
