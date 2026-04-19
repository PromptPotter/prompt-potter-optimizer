"""Per-dataset scoring formula evaluator.

Each dataset declares a scoring formula in ``campaign.json`` under the
``"scoring"`` key. The block accepts two shapes:

- **String shorthand** — legacy form, interpreted as ``per_query``. The
  ``per_round`` side uses the evaluator-registry default.
- **Twin form** — ``{"per_query": "...", "per_round": "..."}``. Either key
  may be omitted; missing keys fall back to the default.

Per-query formula namespace:

    hit                 — bool (1/0), exact match at rank 1
    ground_truth_rank   — int or None, 1-based position in ranking
    n_candidates        — int, total candidates returned
    predicted           — str, model output
    ground_truth        — str, expected answer
    error               — str or None
    <node_name>         — SimpleNamespace of that node's pipeline_data
    evaluators          — SimpleNamespace of per-query Evaluator values

Per-round formula namespace is built from ``application/scoring/evaluators``
— every registered per-round evaluator whose ``applies(schema)`` is True
contributes one named value. Undefined names raise ``NameError`` (fail loud).

Scoring functions (add new ones here):
    rr(k)                              — reciprocal rank: 1/k if k else 0
    gsm8k_match(predicted, ground_truth) — numeric extraction + comparison
    aime_match(predicted, ground_truth)  — \\boxed{} extraction + integer comparison (AIME 0-999)
    exact_match(predicted, ground_truth) — case-insensitive whitespace-stripped equality

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
_BOLD_RE = re.compile(r"\*\*([^*]+?)\*\*")


def _extract_bold(text: str) -> str:
    """Return the last ``**…**`` run, else *text* unchanged.

    BBEH final answers are wrapped in bold markers. No markers → no-op.
    """
    if not text:
        return ""
    matches = _BOLD_RE.findall(text)
    if matches:
        return matches[-1].strip()
    return text


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


def _exact_match(predicted: str, ground_truth: str) -> float:
    """Return 1.0 iff predicted == ground_truth after bold-strip + lowercase.

    Strips ``**…**`` markers from both sides (BBEH convention); no-op
    when markers are absent, so plain exact-match datasets are unaffected.
    """
    p = _extract_bold(predicted or "").strip().lower()
    g = _extract_bold(ground_truth or "").strip().lower()
    return 1.0 if p == g else 0.0


def _extract_gsm8k_display(text: str) -> str:
    """Return the extracted GSM8K answer as a short string."""
    n = _extract_gsm8k_number(text or "")
    if n is None:
        return (text or "").strip()
    return str(int(n)) if n.is_integer() else str(n)


def _extract_boxed_display(text: str) -> str:
    """Return the last ``\\boxed{…}`` content, else last number, else stripped."""
    if not text:
        return ""
    boxed = _BOXED_RE.findall(text)
    if boxed:
        return boxed[-1].strip()
    nums = _NUMBER_RE.findall(text)
    if nums:
        return nums[-1]
    return text.strip()


SCORING_FUNCTIONS: dict[str, Callable] = {
    "rr": _rr,
    "gsm8k_match": _gsm8k_match,
    "aime_match": _aime_match,
    "exact_match": _exact_match,
}
"""All scoring helpers available in formulas. Add new ones here."""


DISPLAY_EXTRACTORS: dict[str, Callable[[str], str]] = {
    "exact_match": _extract_bold,
    "gsm8k_match": _extract_gsm8k_display,
    "aime_match": _extract_boxed_display,
}
"""Per-scoring-function display extractors — reused by the eval UI."""


def extract_display_answer(predicted: str, formula: str | None) -> str:
    """Return the parsed answer for *predicted* under *formula*.

    Dispatches on the first scoring-function name found in *formula*.
    Falls back to a whitespace-stripped copy of *predicted* when no
    formula or no match is given.
    """
    text = (predicted or "").strip()
    if not formula:
        return text
    for name, extractor in DISPLAY_EXTRACTORS.items():
        if name in formula:
            return extractor(predicted or "").strip()
    return text


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


# ---------------------------------------------------------------------------
# Per-round scoring formula — mirrors compile_scorer for round aggregates
# ---------------------------------------------------------------------------

# Type alias for per-round scoring callable: (values dict) -> float.
RoundScorer = Callable[[dict[str, float]], float]


def compile_round_scorer(formula: str | None) -> RoundScorer:
    """Pre-compile a per-round scoring formula into a callable.

    Returns a function ``(dict[str, float]) -> float`` clamped to [0, 1].
    The input dict is the output of ``materialize_round_values()`` — every
    registered per-round evaluator that applies to the current schema.
    Referencing an undefined name in the formula raises ``NameError``
    (fail loud — misconfigured formulas should not silently produce 0.0).
    """
    if not formula:
        return _default_round_scorer

    code = compile(formula, "<round_scoring>", "eval")

    def _scorer(values: dict[str, float]) -> float:
        raw = eval(code, _SAFE_BUILTINS, dict(values))
        return max(0.0, min(1.0, float(raw)))

    return _scorer


def _default_round_scorer(values: dict[str, float]) -> float:
    """Fallback per-round scorer: the registry's ``accuracy`` value, or 0."""
    return max(0.0, min(1.0, float(values.get("accuracy", 0.0))))


# ---------------------------------------------------------------------------
# Twin-form scoring block parsing — single source of truth
# ---------------------------------------------------------------------------


def split_scoring_block(
    block: str | dict[str, str] | None,
) -> tuple[str | None, str | None]:
    """Normalize the campaign ``scoring`` field to ``(per_query, per_round)``.

    Three accepted shapes:

    - ``None`` / ``""`` → ``(None, None)`` (defaults apply downstream)
    - ``str`` → legacy shorthand, interpreted as ``per_query`` only
    - ``dict`` → ``{"per_query": ..., "per_round": ...}``; missing keys
      become ``None``.
    """
    if isinstance(block, dict):
        return block.get("per_query"), block.get("per_round")
    if isinstance(block, str) and block:
        return block, None
    return None, None
