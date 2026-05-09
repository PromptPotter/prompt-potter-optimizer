"""Scoring formula compiler + match/display primitives.

The compiler turns a ``campaign.json::scoring`` formula string into a
callable that scores one ``QueryMeasurement`` dict; ``rescore_results`` is the
sole writer of top-level ``hit``/``score`` and the ``scored`` audit map.

Per-sample formula namespace:

    hit                 — bool (1/0), exact match at rank 1
    ground_truth_rank   — int or None, 1-based position in ranking
    n_candidates        — int, total candidates returned
    predicted           — str, model output
    ground_truth        — str, expected answer
    error               — str or None
    <node_name>         — SimpleNamespace of that node's pipeline_data
    evaluators          — SimpleNamespace of per-sample Evaluator values

Per-round formula namespace is built from ``application/scoring/evaluators`` —
every registered per-round evaluator whose ``applies(schema)`` is True
contributes one named value. Undefined names raise ``NameError`` (fail loud).
"""

from __future__ import annotations

import ast
import hashlib
import logging
import math
import re
from collections.abc import Callable
from types import SimpleNamespace
from typing import TYPE_CHECKING, Any

from promptpotter.domain.scoring import (
    DEFAULT_SCORER_ID,
    RoundScorer,
    Scorer,
    ScoringSpec,
)

if TYPE_CHECKING:
    pass

logger = logging.getLogger(__name__)

__all__ = [
    "DISPLAY_EXTRACTORS",
    "SCORING_FUNCTIONS",
    "auto_scorer_id",
    "compile_round_scorer",
    "compile_scorer",
    "extract_display_answer",
    "extract_item_label",
    "rescore_results",
    "split_scoring_block",
]


# ---------------------------------------------------------------------------
# Ranked-item label extraction — one definition, used by metrics/evaluators/views
# ---------------------------------------------------------------------------


def extract_item_label(c: Any) -> str:
    """Return display label of a ranked item (dict ``{candidate: ...}``, list/tuple, or string).

    The wire-side dict key remains ``candidate`` (TermNorm output shape).
    """
    if isinstance(c, dict):
        return str(c.get("candidate", c))
    return c[0] if isinstance(c, (list, tuple)) else str(c)


# ---------------------------------------------------------------------------
# Scoring functions — one registry, add new helpers here
# ---------------------------------------------------------------------------

_GSM8K_ANSWER_RE = re.compile(r"####\s*(-?[\d,]+\.?\d*)")
_NUMBER_RE = re.compile(r"-?\d[\d,]*\.?\d*")
_BOXED_RE = re.compile(r"\\boxed\{([^{}]+)\}")
_BOLD_RE = re.compile(r"\*\*([^*]+?)\*\*")


def _extract_bold(text: str) -> str:
    """Return the last ``**…**`` run, else *text* unchanged."""
    if not text:
        return ""
    matches = _BOLD_RE.findall(text)
    if matches:
        return matches[-1].strip()
    return text


def _extract_gsm8k_number(text: str) -> float | None:
    """Extract GSM8K answer: ``#### N`` first, else the last number."""
    m = _GSM8K_ANSWER_RE.search(text)
    if m:
        return float(m.group(1).replace(",", ""))
    matches = _NUMBER_RE.findall(text)
    if matches:
        return float(matches[-1].replace(",", ""))
    return None


def _gsm8k_match(predicted: str, ground_truth: str) -> float:
    gt = _extract_gsm8k_number(ground_truth or "")
    pred = _extract_gsm8k_number(predicted or "")
    if gt is None or pred is None:
        return 0.0
    return 1.0 if gt == pred else 0.0


def _rr(k: int | None) -> float:
    return 1.0 / k if k else 0.0


def _aime_match(predicted: str, ground_truth: str) -> float:
    """Match AIME integers in [0, 999]; prefers ``\\boxed{N}``, falls back to last number."""
    try:
        gt = int(ground_truth.strip())
    except (ValueError, AttributeError):
        return 0.0

    text = predicted or ""

    boxed = _BOXED_RE.findall(text)
    if boxed:
        raw = boxed[-1].strip()
        try:
            pred = int(float(raw.replace(",", "")))
            return 1.0 if pred == gt else 0.0
        except (ValueError, OverflowError):
            pass

    matches = _NUMBER_RE.findall(text)
    if not matches:
        return 0.0
    try:
        pred = int(float(matches[-1].replace(",", "")))
    except (ValueError, OverflowError):
        return 0.0
    return 1.0 if pred == gt else 0.0


def _exact_match(predicted: str, ground_truth: str) -> float:
    """Exact match after bold-strip + lowercase. BBEH bold markers stripped both sides."""
    p = _extract_bold(predicted or "").strip().lower()
    g = _extract_bold(ground_truth or "").strip().lower()
    return 1.0 if p == g else 0.0


def _relu(x: float) -> float:
    return max(0.0, float(x))


def _hockeystick(x: float, threshold: float, slope: float = 1.0) -> float:
    return max(0.0, (float(x) - float(threshold)) * float(slope))


def _sigmoid(x: float) -> float:
    x = float(x)
    if x >= 0:
        z = math.exp(-x)
        return 1.0 / (1.0 + z)
    z = math.exp(x)
    return z / (1.0 + z)


def _smoothstep(x: float, edge0: float, edge1: float) -> float:
    e0 = float(edge0)
    e1 = float(edge1)
    if e1 == e0:
        return 0.0 if float(x) < e0 else 1.0
    t = max(0.0, min(1.0, (float(x) - e0) / (e1 - e0)))
    return t * t * (3 - 2 * t)


def _extract_gsm8k_display(text: str) -> str:
    n = _extract_gsm8k_number(text or "")
    if n is None:
        return (text or "").strip()
    return str(int(n)) if n.is_integer() else str(n)


def _extract_boxed_display(text: str) -> str:
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
    "relu": _relu,
    "hockeystick": _hockeystick,
    "sigmoid": _sigmoid,
    "smoothstep": _smoothstep,
}


DISPLAY_EXTRACTORS: dict[str, Callable[[str], str]] = {
    "exact_match": _extract_bold,
    "gsm8k_match": _extract_gsm8k_display,
    "aime_match": _extract_boxed_display,
}


def extract_display_answer(predicted: str, formula: str | None) -> str:
    """Return the parsed answer for *predicted* under *formula*; falls back to stripped text."""
    text = (predicted or "").strip()
    if not formula:
        return text
    for name, extractor in DISPLAY_EXTRACTORS.items():
        if name in formula:
            return extractor(predicted or "").strip()
    return text


# ---------------------------------------------------------------------------
# Formula compiler
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


# AST allowlist — restricted-eval is bypassable by default
# (``().__class__.__base__.__subclasses__()`` reaches anything). Block at the
# AST instead: no Attribute access (kills .__class__), no comprehensions, no
# lambdas, no walrus, no subscript. Names are unrestricted because the
# per-sample namespace is shaped by ``pipeline_data`` and varies per dataset;
# the boundary is "no attribute access, no unknown calls" — every Call must
# resolve to a name in ``_SAFE_BUILTINS`` ∪ ``SCORING_FUNCTIONS`` ∪ namespace.
_ALLOWED_AST_NODES: frozenset[type[ast.AST]] = frozenset(
    {
        ast.Expression,
        ast.BinOp,
        ast.UnaryOp,
        ast.BoolOp,
        ast.Compare,
        ast.Name,
        ast.Load,
        ast.Constant,
        ast.Call,
        ast.IfExp,
        ast.keyword,
        # Operators
        ast.Add,
        ast.Sub,
        ast.Mult,
        ast.Div,
        ast.FloorDiv,
        ast.Mod,
        ast.Pow,
        ast.UAdd,
        ast.USub,
        ast.Not,
        ast.And,
        ast.Or,
        ast.Eq,
        ast.NotEq,
        ast.Lt,
        ast.LtE,
        ast.Gt,
        ast.GtE,
    }
)


def _validate_ast(tree: ast.AST, *, source: str) -> None:
    """Walk *tree*; reject anything outside ``_ALLOWED_AST_NODES``.

    Raises ``ValueError`` (caught by ``apply_steer_file`` and reported by
    ``compile_scorer`` callers).
    """
    for node in ast.walk(tree):
        kind = type(node)
        if kind in _ALLOWED_AST_NODES:
            continue
        raise ValueError(
            f"Scoring formula rejected — disallowed syntax {kind.__name__!r} "
            f"in {source}. Allowed: arithmetic, comparisons, calls to the "
            "registered scoring helpers, namespace name lookups."
        )


def _build_namespace(result: dict) -> dict:
    pd = result.get("pipeline_data") or {}

    step_tokens = pd.get("step_tokens") or {}
    input_tokens = 0
    output_tokens = 0
    for entry in step_tokens.values():
        if isinstance(entry, dict):
            input_tokens += int(entry.get("input", 0))
            output_tokens += int(entry.get("output", 0))

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

    for key, val in pd.items():
        if isinstance(val, dict):
            ns[key] = SimpleNamespace(**val)
        elif key not in ns:
            ns[key] = val

    return ns


def compile_scorer(formula: str | None) -> Callable[[dict], float]:
    """Pre-compile a scoring formula into a callable returning a [0, 1]-clamped float."""
    if not formula:
        raise ValueError(
            "compile_scorer: scoring formula is required. "
            "Set ``campaign_config.scoring`` (e.g. "
            '"exact_match(predicted, ground_truth)") — otherwise every '
            "query scores 0 because fresh traces carry no ``hit`` field."
        )

    tree = ast.parse(formula, "<scoring>", "eval")
    _validate_ast(tree, source="per_sample scoring formula")
    code = compile(tree, "<scoring>", "eval")

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
    scorer_id: str = "none",
    formula: str | None = None,
) -> list[dict]:
    """Apply *scorer* to each result, accumulating a multi-scorer audit map.

    Sole writer of top-level ``hit``/``fitness`` on result dicts. Skips error
    results. Idempotent under the same ``scorer_id``.
    """
    from promptpotter.shared.errors import is_error_result

    for r in results:
        if is_error_result(r):
            continue
        fitness = scorer(r)
        hit = fitness >= 1.0
        scored = r.setdefault("scored", {})
        scored[scorer_id] = {"fitness": fitness, "hit": hit, "formula": formula}
        r["fitness"] = fitness
        r["hit"] = hit
    return results


# ---------------------------------------------------------------------------
# Per-round scoring formula
# ---------------------------------------------------------------------------


def compile_round_scorer(formula: str | None) -> RoundScorer:
    """Pre-compile a per-round scoring formula. Undefined names raise ``NameError``."""
    if not formula:
        return _default_round_scorer

    tree = ast.parse(formula, "<round_scoring>", "eval")
    _validate_ast(tree, source="per_round scoring formula")
    code = compile(tree, "<round_scoring>", "eval")

    def _scorer(values: dict[str, float]) -> float:
        raw = eval(code, _SAFE_BUILTINS, dict(values))
        return max(0.0, min(1.0, float(raw)))

    return _scorer


def _default_round_scorer(values: dict[str, float]) -> float:
    return max(0.0, min(1.0, float(values.get("accuracy", 0.0))))


# ---------------------------------------------------------------------------
# Twin-form scoring block parsing
# ---------------------------------------------------------------------------


def auto_scorer_id(per_sample: str | None) -> str:
    """Stable scorer id from formula hash; ``None``/empty → ``default_hit``."""
    if not per_sample:
        return DEFAULT_SCORER_ID
    h = hashlib.sha256(per_sample.encode("utf-8")).hexdigest()[:10]
    return f"auto_{h}"


def split_scoring_block(
    block: str | dict[str, str] | None,
) -> ScoringSpec:
    """Normalize the campaign ``scoring`` field to ``(per_sample, per_round, scorer_id)``."""
    if isinstance(block, dict):
        per_sample = block.get("per_sample")
        per_round = block.get("per_round")
        scorer_id = block.get("id") or auto_scorer_id(per_sample)
        return ScoringSpec(per_sample, per_round, scorer_id)
    if isinstance(block, str) and block:
        return ScoringSpec(block, None, auto_scorer_id(block))
    return ScoringSpec(None, None, DEFAULT_SCORER_ID)
