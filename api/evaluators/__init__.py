"""
Evaluator module for workflow evaluation.

Provides ExactMatchEvaluator and a simple lookup function.
"""
from typing import Any

from .base import (
    EvaluatorBase,
    EvaluationOutput,
    EvalResult,
)
from .exact_match import ExactMatchEvaluator


def get_evaluator(
    evaluator_type: str,
    config: dict[str, Any] | None = None,
) -> EvaluatorBase:
    """Get an evaluator instance by type name.

    Args:
        evaluator_type: Evaluator type name (e.g., "exact_match", "exact")
        config: Optional evaluator configuration

    Returns:
        Configured evaluator instance

    Raises:
        ValueError: If evaluator type not found
    """
    if evaluator_type.lower() in ("exact_match", "exact", "exactmatch"):
        return ExactMatchEvaluator(config)
    raise ValueError(
        f"Unknown evaluator type: '{evaluator_type}'. "
        f"Available: ['ExactMatchEvaluator']"
    )


__all__ = [
    "EvaluatorBase",
    "EvaluationOutput",
    "EvalResult",
    "ExactMatchEvaluator",
    "get_evaluator",
]
