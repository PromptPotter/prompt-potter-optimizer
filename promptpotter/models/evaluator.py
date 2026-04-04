"""
Exact match evaluator.

Compares expected and actual values for exact equality,
with optional whitespace stripping.
"""
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, Field


class EvalResult(StrEnum):
    """Evaluation result status."""
    PASS = "pass"
    FAIL = "fail"
    ERROR = "error"


class EvaluationOutput(BaseModel):
    """Result of a single evaluation."""

    result: EvalResult = Field(..., description="pass, fail, or error")
    score: float = Field(
        ...,
        ge=0.0,
        le=1.0,
        description="Evaluation score (0.0 = failure, 1.0 = match)"
    )
    expected: Any = Field(..., description="Expected value")
    actual: Any = Field(..., description="Actual value from workflow")


class ExactMatchEvaluator:
    """
    Evaluator that checks for exact equality.

    Config options:
        strip: Strip leading/trailing whitespace (default: True)
    """

    def __init__(self, config: dict[str, Any] | None = None):
        self.config = config or {}
        self.strip = self.config.get("strip", True)

    def _normalize(self, value: Any) -> Any:
        """Normalize a value for comparison."""
        if isinstance(value, str) and self.strip:
            return value.strip()
        return value

    def evaluate(self, expected: Any, actual: Any) -> EvaluationOutput:
        """Compare expected and actual for exact match."""
        if expected is None and actual is None:
            return EvaluationOutput(
                result=EvalResult.PASS,
                score=1.0,
                expected=expected,
                actual=actual,
            )

        if expected is None or actual is None:
            return EvaluationOutput(
                result=EvalResult.FAIL,
                score=0.0,
                expected=expected,
                actual=actual,
            )

        norm_expected = self._normalize(expected)
        norm_actual = self._normalize(actual)

        if norm_expected == norm_actual:
            return EvaluationOutput(
                result=EvalResult.PASS,
                score=1.0,
                expected=expected,
                actual=actual,
            )
        return EvaluationOutput(
            result=EvalResult.FAIL,
            score=0.0,
            expected=expected,
            actual=actual,
        )
