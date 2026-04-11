"""Per-dataset scoring formula evaluator.

Each dataset declares a scoring formula in ``campaign.json`` under the
``"scoring"`` key.  The formula is a Python expression evaluated per
query result with these names in scope:

    hit                 — bool (1/0), exact match at rank 1
    ground_truth_rank   — int or None, 1-based position in ranking
    n_candidates        — int, total candidates returned
    predicted           — str, model output
    ground_truth        — str, expected answer
    error               — str or None
    <node_name>         — SimpleNamespace of that node's pipeline_data

Scoring functions (add new ones here):
    rr(k)                              — reciprocal rank: 1/k if k else 0
    gsm8k_match(predicted, ground_truth) — numeric extraction + comparison
    aime_match(predicted, ground_truth)  — \\boxed{} extraction + integer comparison (AIME 0-999)

No ``scoring`` key → defaults to ``float(hit)`` (exact-match, legacy).
"""

from __future__ import annotations

import logging
import math
import re
from collections.abc import Callable
from types import SimpleNamespace

logger = logging.getLogger(__name__)

# Type alias for per-dataset scoring callable
Scorer = Callable[[dict], float]

# ---------------------------------------------------------------------------
# Scoring functions — one registry, add new helpers here
# ---------------------------------------------------------------------------

_GSM8K_ANSWER_RE = re.compile(r"####\s*(-?[\d,]+\.?\d*)")
_NUMBER_RE = re.compile(r"-?\d[\d,]*\.?\d*")
_BOXED_RE = re.compile(r"\\boxed\{([^{}]+)\}")


def _extract_gsm8k_number(text: str) -> float | None:
    """Extract numeric answer from a GSM8K-style string.

    Tries ``#### N`` first, falls back to the last number in the text.
    """
    m = _GSM8K_ANSWER_RE.search(text)
    if m:
        return float(m.group(1).replace(",", ""))
    matches = _NUMBER_RE.findall(text)
    if matches:
        return float(matches[-1].replace(",", ""))
    return None


def _gsm8k_match(predicted: str, ground_truth: str) -> float:
    """Return 1.0 if predicted and ground_truth contain the same number."""
    gt = _extract_gsm8k_number(ground_truth or "")
    pred = _extract_gsm8k_number(predicted or "")
    if gt is None or pred is None:
        return 0.0
    return 1.0 if gt == pred else 0.0


def _rr(k: int | None) -> float:
    """Reciprocal rank: 1/k if k else 0."""
    return 1.0 / k if k else 0.0


def _aime_match(predicted: str, ground_truth: str) -> float:
    """Return 1.0 if predicted contains the same integer as ground_truth.

    AIME answers are integers in [0, 999]. Extraction priority:
    1. Last ``\\boxed{N}`` value (standard math benchmark convention)
    2. Last number in text (fallback)
    """
    try:
        gt = int(ground_truth.strip())
    except (ValueError, AttributeError):
        return 0.0

    text = predicted or ""

    # Primary: extract from \boxed{N} (MathArena / standard benchmark convention)
    boxed = _BOXED_RE.findall(text)
    if boxed:
        raw = boxed[-1].strip()
        try:
            pred = int(float(raw.replace(",", "")))
            return 1.0 if pred == gt else 0.0
        except (ValueError, OverflowError):
            pass  # non-numeric boxed content, fall through

    # Fallback: last number in text
    matches = _NUMBER_RE.findall(text)
    if not matches:
        return 0.0
    try:
        pred = int(float(matches[-1].replace(",", "")))
    except (ValueError, OverflowError):
        return 0.0
    return 1.0 if pred == gt else 0.0


SCORING_FUNCTIONS: dict[str, Callable] = {
    "rr": _rr,
    "gsm8k_match": _gsm8k_match,
    "aime_match": _aime_match,
}
"""All scoring helpers available in formulas. Add new ones here."""

# ---------------------------------------------------------------------------

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


def _build_namespace(result: dict) -> dict:
    """Build eval namespace from a QueryResult dict."""
    ns: dict = {
        "hit": int(result.get("hit", False)),
        "ground_truth_rank": result.get("ground_truth_rank"),
        "n_candidates": result.get("n_candidates", 0),
        "error": result.get("error"),
        "predicted": result.get("predicted", ""),
        "ground_truth": result.get("ground_truth", ""),
        **SCORING_FUNCTIONS,
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
