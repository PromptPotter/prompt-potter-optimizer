"""Tests for evaluators."""
import json

import pytest

from api.evaluators.base import EvalResult
from api.evaluators.exact_match import ExactMatchEvaluator
from api.evaluators.criteria import CriteriaEvaluator
from api.evaluators import get_evaluator


def test_exact_match():
    ev = ExactMatchEvaluator({"case_insensitive": True, "normalize_whitespace": True})
    assert ev.evaluate("Hello  World", "hello world").result == EvalResult.PASS

    batch = ev.evaluate_batch([
        {"expected": "a", "actual": "a"},
        {"expected": "b", "actual": "x"},
    ])
    assert batch.passed == 1 and batch.failed == 1


def test_registry():
    assert isinstance(get_evaluator("exact"), ExactMatchEvaluator)
    assert isinstance(get_evaluator("criteria"), CriteriaEvaluator)
    with pytest.raises(ValueError, match="Unknown evaluator type"):
        get_evaluator("nonexistent_evaluator_xyz")
