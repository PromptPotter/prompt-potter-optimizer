"""
Evaluator base class for workflow output validation.

Supports three-tier validation following the query-preprocessing-workflow pattern:
1. Field-level rules (Exact, OneOf, Substring, Criteria)
2. Equality check against expected output
3. Failure with detailed diagnostics

Integrates with Langfuse for score logging when trace_id is provided.
"""
from abc import ABC, abstractmethod
from typing import Any, Dict, List, Optional
from pydantic import BaseModel, Field
from enum import Enum


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
    reason: Optional[str] = Field(
        None,
        description="Explanation for the result"
    )
    field_results: Optional[Dict[str, Dict[str, Any]]] = Field(
        None,
        description="Per-field evaluation results (for structured outputs)"
    )
    metadata: Dict[str, Any] = Field(
        default_factory=dict,
        description="Additional evaluation metadata"
    )


class BatchEvaluationOutput(BaseModel):
    """Aggregate results from batch evaluation."""

    total: int = Field(..., description="Total items evaluated")
    passed: int = Field(..., description="Items that passed")
    failed: int = Field(..., description="Items that failed")
    errors: int = Field(default=0, description="Items that errored")
    accuracy: float = Field(
        ...,
        ge=0.0,
        le=1.0,
        description="Pass rate (passed / total)"
    )
    mean_score: float = Field(
        ...,
        ge=0.0,
        le=1.0,
        description="Average score across all items"
    )
    results: List[EvaluationOutput] = Field(
        default_factory=list,
        description="Individual evaluation results"
    )


class EvaluatorBase(ABC):
    """
    Abstract base class for evaluators.

    Evaluators compare workflow outputs against expected values
    using configurable validation rules.

    Subclasses implement the `evaluate` method for specific
    comparison strategies (exact match, substring, LLM-based, etc.).
    """

    def __init__(self, config: Optional[Dict[str, Any]] = None):
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

    def evaluate_batch(
        self,
        examples: List[Dict[str, Any]]
    ) -> BatchEvaluationOutput:
        """
        Evaluate a batch of examples.

        Args:
            examples: List of {expected, actual} dicts

        Returns:
            Aggregate metrics and individual results
        """
        results: List[EvaluationOutput] = []

        for ex in examples:
            try:
                result = self.evaluate(ex["expected"], ex["actual"])
                results.append(result)
            except Exception as e:
                results.append(EvaluationOutput(
                    result=EvalResult.ERROR,
                    score=0.0,
                    expected=ex.get("expected"),
                    actual=ex.get("actual"),
                    reason=f"Evaluation error: {str(e)}"
                ))

        passed = sum(1 for r in results if r.result == EvalResult.PASS)
        failed = sum(1 for r in results if r.result == EvalResult.FAIL)
        errors = sum(1 for r in results if r.result == EvalResult.ERROR)
        scores = [r.score for r in results]

        return BatchEvaluationOutput(
            total=len(results),
            passed=passed,
            failed=failed,
            errors=errors,
            accuracy=passed / len(results) if results else 0.0,
            mean_score=sum(scores) / len(scores) if scores else 0.0,
            results=results
        )

    def evaluate_workflow_output(
        self,
        expected: Dict[str, Any],
        actual: Dict[str, Any],
        field_rules: Optional[Dict[str, str]] = None
    ) -> EvaluationOutput:
        """
        Evaluate workflow output with optional field-level rules.

        Args:
            expected: Expected output dict
            actual: Actual output dict
            field_rules: Optional per-field evaluator types
                         e.g., {"name": "exact", "description": "substring"}

        Returns:
            EvaluationOutput with field-level details
        """
        field_results: Dict[str, Dict[str, Any]] = {}
        all_passed = True
        total_score = 0.0
        field_count = 0

        for field_name, expected_value in expected.items():
            actual_value = actual.get(field_name)
            field_count += 1

            # Use field-specific rule or default to this evaluator
            if field_rules and field_name in field_rules:
                # Would dispatch to appropriate evaluator
                # For now, use self.evaluate
                field_eval = self.evaluate(expected_value, actual_value)
            else:
                field_eval = self.evaluate(expected_value, actual_value)

            field_results[field_name] = {
                "expected": expected_value,
                "actual": actual_value,
                "result": field_eval.result.value,
                "score": field_eval.score,
                "reason": field_eval.reason
            }

            total_score += field_eval.score
            if field_eval.result != EvalResult.PASS:
                all_passed = False

        avg_score = total_score / field_count if field_count > 0 else 0.0

        return EvaluationOutput(
            result=EvalResult.PASS if all_passed else EvalResult.FAIL,
            score=avg_score,
            expected=expected,
            actual=actual,
            field_results=field_results,
            reason=None if all_passed else "One or more fields did not match"
        )

    @classmethod
    def get_evaluator_type(cls) -> str:
        """Return the evaluator type identifier."""
        return cls.__name__

    def evaluate_and_log(
        self,
        expected: Any,
        actual: Any,
        trace_id: Optional[str] = None,
        score_name: Optional[str] = None,
    ) -> EvaluationOutput:
        """
        Evaluate and optionally log score to Langfuse.

        Args:
            expected: Ground truth value
            actual: Workflow output value
            trace_id: Langfuse trace ID (if provided, logs score to Langfuse)
            score_name: Custom score name (defaults to evaluator type)

        Returns:
            EvaluationOutput with result, score, and details
        """
        result = self.evaluate(expected, actual)

        if trace_id:
            self._log_score_to_langfuse(
                trace_id=trace_id,
                score_name=score_name or self.get_evaluator_type(),
                score=result.score,
                comment=result.reason,
            )

        return result

    def _log_score_to_langfuse(
        self,
        trace_id: str,
        score_name: str,
        score: float,
        comment: Optional[str] = None,
    ) -> None:
        """
        Log evaluation score to Langfuse.

        Args:
            trace_id: Langfuse trace ID
            score_name: Score metric name
            score: Score value (0.0 to 1.0)
            comment: Optional explanation
        """
        try:
            from api.services.langfuse_client import LangfuseLogger
            langfuse = LangfuseLogger.get_instance()
            langfuse.create_score(
                trace_id=trace_id,
                name=score_name,
                value=score,
                comment=comment,
            )
        except Exception as e:
            # Don't fail evaluation if logging fails
            print(f"Warning: Failed to log score to Langfuse: {e}")


def log_evaluation_score(
    trace_id: str,
    evaluator_name: str,
    score: float,
    comment: Optional[str] = None,
) -> bool:
    """
    Convenience function to log an evaluation score to Langfuse.

    Args:
        trace_id: Langfuse trace ID from workflow execution
        evaluator_name: Name of the score metric
        score: Score value (0.0 to 1.0)
        comment: Optional explanation

    Returns:
        True if successful, False otherwise
    """
    try:
        from api.services.langfuse_client import LangfuseLogger
        langfuse = LangfuseLogger.get_instance()
        return langfuse.create_score(
            trace_id=trace_id,
            name=evaluator_name,
            value=score,
            comment=comment,
        )
    except Exception as e:
        print(f"Warning: Failed to log score to Langfuse: {e}")
        return False
