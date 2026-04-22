"""Per-dataset scoring formula evaluator.

Each dataset declares a scoring formula in ``campaign.json`` under ``"scoring"``.
Accepted shapes:

- **String shorthand** — interpreted as ``per_query``; ``per_round`` uses the evaluator-registry default.
- **Twin form** — ``{"per_query": "...", "per_round": "..."}``; omitted keys fall back to defaults.

Per-query formula namespace:

    hit                 — bool (1/0), exact match at rank 1
    ground_truth_rank   — int or None, 1-based position in ranking
    n_candidates        — int, total candidates returned
    predicted           — str, model output
    ground_truth        — str, expected answer
    error               — str or None
    <node_name>         — SimpleNamespace of that node's pipeline_data
    evaluators          — SimpleNamespace of per-query Evaluator values

Per-round formula namespace is built from ``application/scoring/evaluators`` —
every registered per-round evaluator whose ``applies(schema)`` is True
contributes one named value. Undefined names raise ``NameError`` (fail loud).

No ``scoring`` key → defaults to ``float(hit)`` (exact-match, legacy).
"""

from __future__ import annotations

import hashlib
import logging
import math
import re
from collections.abc import Callable
from types import SimpleNamespace
from typing import NamedTuple

logger = logging.getLogger(__name__)

# Type alias for per-dataset scoring callable
Scorer = Callable[[dict], float]

# Sentinel scorer ids. ``default_hit`` tags the ``float(hit)`` fallback
# used when no formula is configured; ``none`` tags untagged/legacy paths
# (rescore helpers called without a scorer). Exposed so trace readers
# can recognize them when inspecting the per-result ``scored`` map.
DEFAULT_SCORER_ID = "default_hit"
EMPTY_SCORER_ID = "none"

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


def _relu(x: float) -> float:
    """ReLU activation: ``max(0, x)``."""
    return max(0.0, float(x))


def _hockeystick(x: float, threshold: float, slope: float = 1.0) -> float:
    """Hockey-stick penalty: 0 below *threshold*, linear above with *slope*."""
    return max(0.0, (float(x) - float(threshold)) * float(slope))


def _sigmoid(x: float) -> float:
    """Logistic sigmoid — numerically safe for large |x|."""
    x = float(x)
    if x >= 0:
        z = math.exp(-x)
        return 1.0 / (1.0 + z)
    z = math.exp(x)
    return z / (1.0 + z)


def _smoothstep(x: float, edge0: float, edge1: float) -> float:
    """Smoothstep Hermite interpolation clamped to [0, 1]."""
    e0 = float(edge0)
    e1 = float(edge1)
    if e1 == e0:
        return 0.0 if float(x) < e0 else 1.0
    t = max(0.0, min(1.0, (float(x) - e0) / (e1 - e0)))
    return t * t * (3 - 2 * t)


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
    # Activation / shaping helpers — useful for token-length penalties etc.
    "relu": _relu,
    "hockeystick": _hockeystick,
    "sigmoid": _sigmoid,
    "smoothstep": _smoothstep,
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
        "exp": math.exp,
        "pow": pow,
    }
}


def _build_namespace(result: dict) -> dict:
    """Build eval namespace from a QueryResult dict."""
    pd = result.get("pipeline_data") or {}

    # Cross-LLM-node token sums from pipeline_data.step_tokens.
    # Per-node breakdown is still reachable via ``step_tokens.<node>.<key>``
    # through the SimpleNamespace flattening below.
    step_tokens = pd.get("step_tokens") or {}
    input_tokens = 0
    output_tokens = 0
    for entry in step_tokens.values():
        if isinstance(entry, dict):
            input_tokens += int(entry.get("input", 0) or 0)
            output_tokens += int(entry.get("output", 0) or 0)

    ns: dict = {
        "hit": int(result.get("hit", False)),
        "ground_truth_rank": result.get("ground_truth_rank"),
        "n_candidates": result.get("n_candidates", 0),
        "error": result.get("error"),
        "predicted": result.get("predicted", ""),
        "ground_truth": result.get("ground_truth", ""),
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        **SCORING_FUNCTIONS,
    }

    # Flatten pipeline_data nodes into SimpleNamespace objects
    for key, val in pd.items():
        if isinstance(val, dict):
            ns[key] = SimpleNamespace(**val)
        elif key not in ns:
            ns[key] = val

    return ns


def compile_scorer(formula: str | None) -> Callable[[dict], float]:
    """Pre-compile a scoring formula into a callable.

    Returns a function ``(QueryResult dict) -> float`` clamped to [0, 1].
    ``None`` or empty string raises — since ``rescore_results`` is the sole
    writer of ``hit``/``score`` on traces, a missing formula would silently
    mark every query a MISS (the prior fallback read ``r["hit"]`` which
    fresh traces no longer carry).
    """
    if not formula:
        raise ValueError(
            "compile_scorer: scoring formula is required. "
            "Set ``campaign_config.scoring`` (e.g. "
            '"exact_match(predicted, ground_truth)") — otherwise every '
            "query scores 0 because fresh traces carry no ``hit`` field."
        )

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


def rescore_results(
    results: list[dict],
    scorer: Scorer,
    scorer_id: str = EMPTY_SCORER_ID,
    formula: str | None = None,
) -> list[dict]:
    """Apply *scorer* to each result, accumulating a multi-scorer audit map.

    For each non-error result:

    - Compute ``score = scorer(r)`` and ``hit = score >= 1.0``.
    - Write ``r["scored"][scorer_id] = {"score", "hit", "formula"}`` —
      accumulates across rescoring passes so a trace carries one entry
      per scorer it's been evaluated under.
    - Project the active scorer's ``score`` / ``hit`` onto the top level
      of *r* for existing readers (loop decisions, display, SearchMemory).

    Raw trace fields (``query``, ``predicted``, ``ground_truth``,
    ``pipeline_data``, ``error``, ``n_candidates``, ``ground_truth_rank``) are
    never touched. Error results (tagged ``error`` or ``predicted == "ERROR"``)
    are skipped — their ``hit`` was never a policy question.

    **Contract:** this is the sole writer of top-level ``hit`` / ``score`` on
    result dicts. Traces emitted by ``measure_sample`` carry only raw facts
    until this function has run. Callers that load historical traces without
    a scorer (SearchMemory cold paths) must skip the rescore step explicitly
    rather than passing ``None``.

    Idempotent under the same ``scorer_id``: running twice overwrites the
    same map entry identically.
    """
    from promptpotter.shared.errors import is_error_result

    for r in results:
        if is_error_result(r):
            continue
        score = scorer(r)
        hit = score >= 1.0
        scored = r.setdefault("scored", {})
        scored[scorer_id] = {"score": score, "hit": hit, "formula": formula}
        r["score"] = score
        r["hit"] = hit
    return results


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


class ScoringSpec(NamedTuple):
    """Parsed ``campaign.json::scoring`` block.

    ``scorer_id`` tags every score computed under ``per_query`` so traces
    accumulate a multi-scorer audit map. When the campaign config omits
    ``id``, it is auto-derived from the formula hash (stable as long as
    the formula is stable).

    Tuple layout preserves legacy ``(per_query, per_round)`` destructuring:
    ``(per_query, per_round, scorer_id)``.
    """

    per_query: str | None
    per_round: str | None
    scorer_id: str


DEFAULT_SCORER_ID = "default_hit"
_EMPTY_SCORER_ID = "none"


def auto_scorer_id(per_query: str | None) -> str:
    """Derive a stable scorer id from the formula string.

    ``None``/empty → ``"default_hit"`` (the ``float(hit)`` fallback).
    Non-empty formula → ``"auto_" + sha256(formula)[:10]``.
    """
    if not per_query:
        return DEFAULT_SCORER_ID
    h = hashlib.sha256(per_query.encode("utf-8")).hexdigest()[:10]
    return f"auto_{h}"


def split_scoring_block(
    block: str | dict[str, str] | None,
) -> ScoringSpec:
    """Normalize the campaign ``scoring`` field to ``(per_query, per_round, scorer_id)``.

    Accepted shapes:

    - ``None`` / ``""`` → ``(None, None, "default_hit")``.
    - ``str`` → shorthand, interpreted as ``per_query``; id auto-derived.
    - ``dict`` → ``{"id": ..., "per_query": ..., "per_round": ...}``;
      missing ``per_query``/``per_round`` become ``None``; missing
      ``id`` auto-derived from ``per_query``.
    """
    if isinstance(block, dict):
        per_query = block.get("per_query")
        per_round = block.get("per_round")
        scorer_id = block.get("id") or auto_scorer_id(per_query)
        return ScoringSpec(per_query, per_round, scorer_id)
    if isinstance(block, str) and block:
        return ScoringSpec(block, None, auto_scorer_id(block))
    return ScoringSpec(None, None, DEFAULT_SCORER_ID)
