"""Per-dataset scoring formula evaluator.

Each dataset declares a scoring formula in ``campaign.json`` under the
``"scoring"`` key.  The formula is a Python expression evaluated per
query result with these names in scope:

    hit                 — bool (1/0), exact match at rank 1
    ground_truth_rank   — int or None, 1-based position in ranking
    n_candidates        — int, total candidates returned
    error               — str or None
    <node_name>         — SimpleNamespace of that node's pipeline_data

Built-in helpers:
    rr(k)  — reciprocal rank: 1/k if k else 0

No ``scoring`` key → defaults to ``float(hit)`` (exact-match, legacy).
"""

from __future__ import annotations

import logging
import math
from collections.abc import Callable
from types import SimpleNamespace

logger = logging.getLogger(__name__)

_SAFE_BUILTINS = {
    "__builtins__": {
        "min": min,
        "max": max,
        "float": float,
        "int": int,
        "bool": bool,
        "abs": abs,
        "round": round,
        "log": math.log,
        "sqrt": math.sqrt,
    }
}


def _rr(k: int | None) -> float:
    """Reciprocal rank: 1/k if k else 0."""
    return 1.0 / k if k else 0.0


def _build_namespace(result: dict) -> dict:
    """Build eval namespace from a QueryResult dict."""
    ns: dict = {
        "hit": int(result.get("hit", False)),
        "ground_truth_rank": result.get("ground_truth_rank"),
        "n_candidates": result.get("n_candidates", 0),
        "error": result.get("error"),
        "rr": _rr,
    }

    # Flatten pipeline_data nodes into SimpleNamespace objects
    pd = result.get("pipeline_data") or {}
    for key, val in pd.items():
        if isinstance(val, dict):
            ns[key] = SimpleNamespace(**val)
        elif key not in ns:
            ns[key] = val

    return ns


def compile_scorer(formula: str | None) -> Callable[[dict], float]:
    """Pre-compile a scoring formula into a callable.

    Returns a function ``(QueryResult dict) -> float`` clamped to [0, 1].
    ``None`` or empty string → exact-match default ``float(hit)``.
    """
    if not formula:
        return _default_scorer

    code = compile(formula, "<scoring>", "eval")

    def _scorer(result: dict) -> float:
        ns = _build_namespace(result)
        try:
            raw = eval(code, _SAFE_BUILTINS, ns)
            return max(0.0, min(1.0, float(raw)))
        except Exception:
            logger.warning("Scoring formula error on query %s", result.get("query", "?")[:60])
            return 0.0

    return _scorer


def _default_scorer(result: dict) -> float:
    return float(result.get("hit", False))
