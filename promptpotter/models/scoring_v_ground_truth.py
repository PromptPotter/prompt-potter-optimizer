"""
Ground truth comparison — compares pipeline output to expected answer.

Leaf of the scoring chain:
  scoring_searchpoint (scores a SearchPoint across a dataset)
    └── sample_measurement (runs one sample through the pipeline)
          └── ground_truth (compares output to expected answer)
"""

from enum import StrEnum
from typing import Any

from pydantic import BaseModel, Field


class GroundTruthResult(StrEnum):
    """Single-sample comparison outcome."""

    PASS = "pass"
    FAIL = "fail"
    ERROR = "error"


class GroundTruthOutput(BaseModel):
    """Result of comparing pipeline output to ground truth."""

    result: GroundTruthResult = Field(..., description="pass, fail, or error")
    score: float = Field(
        ..., ge=0.0, le=1.0, description="Comparison score (0.0 = mismatch, 1.0 = match)"
    )
    expected: Any = Field(..., description="Expected value (ground truth)")
    actual: Any = Field(..., description="Actual value from pipeline")


class ExactMatchComparator:
    """
    Compares expected and actual for exact equality.

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

    def compare(self, expected: Any, actual: Any) -> GroundTruthOutput:
        """Compare expected and actual for exact match."""
        if expected is None and actual is None:
            return GroundTruthOutput(
                result=GroundTruthResult.PASS,
                score=1.0,
                expected=expected,
                actual=actual,
            )

        if expected is None or actual is None:
            return GroundTruthOutput(
                result=GroundTruthResult.FAIL,
                score=0.0,
                expected=expected,
                actual=actual,
            )

        norm_expected = self._normalize(expected)
        norm_actual = self._normalize(actual)

        if norm_expected == norm_actual:
            return GroundTruthOutput(
                result=GroundTruthResult.PASS,
                score=1.0,
                expected=expected,
                actual=actual,
            )
        return GroundTruthOutput(
            result=GroundTruthResult.FAIL,
            score=0.0,
            expected=expected,
            actual=actual,
        )
