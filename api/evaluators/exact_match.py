"""
Exact match evaluator.

Compares expected and actual values for exact equality,
with optional case-insensitive and whitespace-normalized modes.
"""
from typing import Any, Dict, Optional

from .base import EvaluatorBase, EvaluationOutput, EvalResult


class ExactMatchEvaluator(EvaluatorBase):
    """
    Evaluator that checks for exact equality.

    Config options:
        case_insensitive: Ignore case for string comparison (default: False)
        normalize_whitespace: Normalize whitespace in strings (default: False)
        strip: Strip leading/trailing whitespace (default: True)
    """

    def __init__(self, config: Optional[Dict[str, Any]] = None):
        super().__init__(config)
        self.case_insensitive = self.config.get("case_insensitive", False)
        self.normalize_whitespace = self.config.get("normalize_whitespace", False)
        self.strip = self.config.get("strip", True)

    def _normalize(self, value: Any) -> Any:
        """Normalize a value for comparison."""
        if not isinstance(value, str):
            return value

        result = value

        if self.strip:
            result = result.strip()

        if self.normalize_whitespace:
            import re
            result = re.sub(r'\s+', ' ', result)

        if self.case_insensitive:
            result = result.lower()

        return result

    def evaluate(self, expected: Any, actual: Any) -> EvaluationOutput:
        """
        Compare expected and actual for exact match.

        Args:
            expected: Expected value
            actual: Actual value

        Returns:
            EvaluationOutput with pass/fail and score
        """
        # Handle None cases
        if expected is None and actual is None:
            return EvaluationOutput(
                result=EvalResult.PASS,
                score=1.0,
                expected=expected,
                actual=actual,
                reason="Both values are None"
            )

        if expected is None or actual is None:
            return EvaluationOutput(
                result=EvalResult.FAIL,
                score=0.0,
                expected=expected,
                actual=actual,
                reason=f"One value is None: expected={expected}, actual={actual}"
            )

        # Normalize values
        norm_expected = self._normalize(expected)
        norm_actual = self._normalize(actual)

        # Compare
        if norm_expected == norm_actual:
            return EvaluationOutput(
                result=EvalResult.PASS,
                score=1.0,
                expected=expected,
                actual=actual,
                reason="Exact match"
            )
        else:
            # Calculate partial score for strings using character overlap
            score = 0.0
            if isinstance(expected, str) and isinstance(actual, str):
                score = self._string_similarity(str(norm_expected), str(norm_actual))

            return EvaluationOutput(
                result=EvalResult.FAIL,
                score=score,
                expected=expected,
                actual=actual,
                reason="Values do not match"
            )

    def _string_similarity(self, s1: str, s2: str) -> float:
        """
        Calculate simple string similarity (Jaccard on characters).

        Returns a score between 0.0 and 1.0.
        """
        if not s1 and not s2:
            return 1.0
        if not s1 or not s2:
            return 0.0

        set1 = set(s1)
        set2 = set(s2)

        intersection = len(set1 & set2)
        union = len(set1 | set2)

        return intersection / union if union > 0 else 0.0
