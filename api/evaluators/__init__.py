"""
Evaluator module.

Provides ExactMatchEvaluator for hit@1 accuracy scoring.
"""

from .exact_match import ExactMatchEvaluator, EvalResult, EvaluationOutput

__all__ = [
    "EvaluationOutput",
    "EvalResult",
    "ExactMatchEvaluator",
]
