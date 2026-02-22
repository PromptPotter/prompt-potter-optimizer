"""
Evaluator registry for workflow evaluation.

Provides registration and discovery of evaluator types.
"""
from typing import Dict, Type, Optional, Any

from .base import (
    EvaluatorBase,
    EvaluationOutput,
    BatchEvaluationOutput,
    EvalResult,
    log_evaluation_score,
)
from .exact_match import ExactMatchEvaluator
from .criteria import CriteriaEvaluator


# Global evaluator registry
_EVALUATOR_REGISTRY: Dict[str, Type[EvaluatorBase]] = {}


def register_evaluator(evaluator_class: Type[EvaluatorBase]) -> Type[EvaluatorBase]:
    """
    Register an evaluator class in the registry.

    Args:
        evaluator_class: Evaluator class to register

    Returns:
        The evaluator class (for decorator usage)
    """
    evaluator_type = evaluator_class.get_evaluator_type()
    _EVALUATOR_REGISTRY[evaluator_type] = evaluator_class

    # Also register lowercase version for convenience
    _EVALUATOR_REGISTRY[evaluator_type.lower()] = evaluator_class

    return evaluator_class


def get_evaluator(
    evaluator_type: str,
    config: Optional[Dict[str, Any]] = None
) -> EvaluatorBase:
    """
    Get an evaluator instance by type name.

    Args:
        evaluator_type: Evaluator type name (e.g., "exact_match", "criteria")
        config: Optional evaluator configuration

    Returns:
        Configured evaluator instance

    Raises:
        ValueError: If evaluator type not found
    """
    evaluator_class = _EVALUATOR_REGISTRY.get(evaluator_type)

    if not evaluator_class:
        # Try common aliases
        aliases = {
            "exact": ExactMatchEvaluator,
            "exact_match": ExactMatchEvaluator,
            "exactmatch": ExactMatchEvaluator,
            "criteria": CriteriaEvaluator,
            "llm": CriteriaEvaluator,
            "llm_judge": CriteriaEvaluator,
            "semantic": CriteriaEvaluator,
        }
        evaluator_class = aliases.get(evaluator_type.lower())

    if not evaluator_class:
        available = list_evaluator_types()
        raise ValueError(
            f"Unknown evaluator type: '{evaluator_type}'. "
            f"Available: {available}"
        )

    return evaluator_class(config)


def list_evaluator_types() -> list[str]:
    """
    List all registered evaluator types.

    Returns:
        List of evaluator type names
    """
    return [k for k in _EVALUATOR_REGISTRY if not k.islower()]


# Register built-in evaluators
register_evaluator(ExactMatchEvaluator)
register_evaluator(CriteriaEvaluator)


__all__ = [
    "EvaluatorBase",
    "EvaluationOutput",
    "BatchEvaluationOutput",
    "EvalResult",
    "ExactMatchEvaluator",
    "CriteriaEvaluator",
    "register_evaluator",
    "get_evaluator",
    "list_evaluator_types",
    "log_evaluation_score",
]
