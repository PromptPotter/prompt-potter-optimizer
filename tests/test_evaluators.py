"""Tests for evaluators (ExactMatch, Criteria, registry)."""
import json

import pytest

from api.evaluators.base import EvalResult
from api.evaluators.exact_match import ExactMatchEvaluator
from api.evaluators.criteria import CriteriaEvaluator
from api.evaluators import get_evaluator


# ---------------------------------------------------------------------------
# ExactMatchEvaluator
# ---------------------------------------------------------------------------

class TestExactMatchEvaluator:
    def test_case_insensitive(self):
        ev = ExactMatchEvaluator({"case_insensitive": True})
        out = ev.evaluate("Hello", "hELLO")
        assert out.result == EvalResult.PASS

    def test_whitespace_normalization(self):
        ev = ExactMatchEvaluator({"normalize_whitespace": True})
        out = ev.evaluate("a  b   c", "a b c")
        assert out.result == EvalResult.PASS


class TestExactMatchBatch:
    def test_batch_accuracy(self):
        ev = ExactMatchEvaluator()
        examples = [
            {"expected": "a", "actual": "a"},
            {"expected": "b", "actual": "b"},
            {"expected": "c", "actual": "x"},
        ]
        batch = ev.evaluate_batch(examples)
        assert batch.total == 3
        assert batch.passed == 2
        assert batch.failed == 1
        assert batch.accuracy == pytest.approx(2 / 3)

    def test_workflow_output_field_level(self):
        ev = ExactMatchEvaluator()
        expected = {"name": "Alice", "age": "30"}
        actual = {"name": "Alice", "age": "31"}
        out = ev.evaluate_workflow_output(expected, actual)
        assert out.result == EvalResult.FAIL
        assert out.field_results["name"]["result"] == "pass"
        assert out.field_results["age"]["result"] == "fail"


# ---------------------------------------------------------------------------
# CriteriaEvaluator (async, uses MockLLMClient)
# ---------------------------------------------------------------------------

class TestCriteriaEvaluator:
    async def test_pass_with_mock_llm(self, mock_llm_client):
        mock_llm_client.responses = [
            json.dumps({"match": True, "score": 0.95, "reasoning": "semantically identical"})
        ]
        ev = CriteriaEvaluator({"threshold": 0.7})
        out = await ev.evaluate_async("dog", "canine")
        assert out.result == EvalResult.PASS
        assert out.score == pytest.approx(0.95)
        assert mock_llm_client._call_count == 1

    async def test_below_threshold_fails(self, mock_llm_client):
        mock_llm_client.responses = [
            json.dumps({"match": False, "score": 0.3, "reasoning": "different meaning"})
        ]
        ev = CriteriaEvaluator({"threshold": 0.7})
        out = await ev.evaluate_async("cat", "airplane")
        assert out.result == EvalResult.FAIL
        assert out.score == pytest.approx(0.3)


# ---------------------------------------------------------------------------
# Evaluator registry
# ---------------------------------------------------------------------------

class TestEvaluatorRegistry:
    @pytest.mark.parametrize("alias,cls", [
        ("exact", ExactMatchEvaluator),
        ("exact_match", ExactMatchEvaluator),
        ("criteria", CriteriaEvaluator),
        ("llm", CriteriaEvaluator),
        ("semantic", CriteriaEvaluator),
    ])
    def test_alias_lookup(self, alias, cls):
        ev = get_evaluator(alias)
        assert isinstance(ev, cls)

    def test_unknown_type_raises(self):
        with pytest.raises(ValueError, match="Unknown evaluator type"):
            get_evaluator("nonexistent_evaluator_xyz")
