"""
Evaluator base class for workflow output validation.

Supports three-tier validation following the query-preprocessing-workflow pattern:
1. Field-level rules (Exact, OneOf, Substring, Criteria)
2. Equality check against expected output
3. Failure with detailed diagnostics
"""
import logging
from abc import ABC, abstractmethod
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)


class EvalResult(str, Enum):
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
        description="Evaluation score (0.0 = complete failure, 1.0 = perfect match)"
    )
    expected: Any = Field(..., description="Expected value")
    actual: Any = Field(..., description="Actual value from workflow")
    reason: str | None = Field(
        None,
        description="Explanation for the result"
    )
    field_results: dict[str, dict[str, Any]] | None = Field(
        None,
        description="Per-field evaluation results (for structured outputs)"
    )
    metadata: dict[str, Any] = Field(
        default_factory=dict,
        description="Additional evaluation metadata"
    )


class EvaluatorBase(ABC):
    """
    Abstract base class for evaluators.

    Evaluators compare workflow outputs against expected values
    using configurable validation rules.

    Subclasses implement the `evaluate` method for specific
    comparison strategies (exact match, substring, LLM-based, etc.).
    """

    def __init__(self, config: dict[str, Any] | None = None):
        """
        Initialize evaluator with configuration.

        Args:
            config: Evaluator-specific configuration
        """
        self.config = config or {}

    @abstractmethod
    def evaluate(self, expected: Any, actual: Any) -> EvaluationOutput:
        """
        Compare expected and actual values.

        Args:
            expected: Ground truth value
            actual: Workflow output value

        Returns:
            EvaluationOutput with result, score, and details
        """
        pass


