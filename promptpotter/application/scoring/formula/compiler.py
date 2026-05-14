"""Per-sample formula compiler + scoring-spec block parser.

AST-restricted ``eval`` over a namespace built from the result dict's
``pipeline_data`` + scoring-function registry. Restricted-eval is
bypassable by default — block at the AST allowlist instead (no attribute
access, no comprehensions, no lambdas, no walrus, no subscript).
"""

from __future__ import annotations

import ast
import hashlib
import logging
import math
from collections.abc import Callable
from types import SimpleNamespace

from promptpotter.application.scoring.formula.matchers import SCORING_FUNCTIONS
from promptpotter.domain.scoring import DEFAULT_SCORER_ID, ScoringSpec

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


def validate_ast(tree: ast.AST, *, source: str) -> None:
    """Walk *tree*; reject anything outside ``_ALLOWED_AST_NODES``.

    Raises ``ValueError`` (callers report).
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
    validate_ast(tree, source="per_sample scoring formula")
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
