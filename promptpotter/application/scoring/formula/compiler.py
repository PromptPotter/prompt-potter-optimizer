"""Per-sample formula compiler + scoring-spec block parser.

AST-restricted ``eval`` over a namespace from the result's ``pipeline_data`` +
scoring-function registry. Restricted-eval alone is bypassable; the AST
allowlist (no attribute access / comprehensions / lambdas / walrus / subscript) is the boundary."""

from __future__ import annotations

import ast
import hashlib
import math
from collections.abc import Callable
from types import SimpleNamespace
from typing import Any

from promptpotter.application.scoring.formula.matchers import SCORING_FUNCTIONS
from promptpotter.domain.scoring import DEFAULT_SCORER_ID, ScoringSpec


class ScoringFormulaError(Exception):
    """A scoring formula raised or returned a non-numeric value while scoring a sample.

    Deterministic: the same formula runs on every sample, so this fires identically
    across the round — it is a formula↔trace contract bug (a name the trace doesn't
    carry, a matcher rejecting an input shape, a non-numeric result), never one odd
    sample. It MUST halt loud: the prior behaviour swallowed it to ``0.0``, which is
    indistinguishable from a genuinely wrong answer and silently zeroed an entire
    campaign's fitness (the exact trap ``compile_scorer`` already guards at compile)."""


class ScoringTermMissingError(ScoringFormulaError):
    """The formula names a term the measurement does not carry.

    Distinct from its parent because the two readers want opposite things. The live scorer
    materializes its namespace fresh, so an absent term means a broken formula and must halt.
    The read-side mask (``metrics.value_with_mask_applied``) scores stored records under
    criteria they may predate — a schema-bound evaluator that never applied to that record —
    and reports *unscorable*, never a fabricated number. Both need to tell "this term was
    never measured" apart from "this formula divided by zero"."""


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


# AST allowlist — no Attribute (kills ``().__class__...``), comprehensions, lambdas, walrus, subscript.
# Names are unrestricted (per-sample namespace varies per dataset); every Call must resolve to a name
# in _SAFE_BUILTINS ∪ SCORING_FUNCTIONS ∪ namespace.
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
    """Walk *tree*; raise ``ValueError`` on anything outside ``_ALLOWED_AST_NODES``."""
    for node in ast.walk(tree):
        kind = type(node)
        if kind in _ALLOWED_AST_NODES:
            continue
        raise ValueError(
            f"Scoring formula rejected — disallowed syntax {kind.__name__!r} "
            f"in {source}. Allowed: arithmetic, comparisons, calls to the "
            "registered scoring helpers, namespace name lookups."
        )


def clamp_unit_score(raw: Any, *, formula: str, subject: str) -> float:
    """The ONE gate every scoring formula's result passes through, per-sample and per-round.

    NaN/inf must never reach the clamp: ``min(1.0, nan)`` short-circuits to its FIRST
    argument, so ``max(0.0, min(1.0, nan))`` is ``1.0`` — a ``0/0`` anywhere in a formula
    would score a PERFECT sample, silently. A non-finite result is missing data, not a
    verdict. *subject* names what was being scored (a query, a round)."""
    try:
        value = float(raw)
    except (TypeError, ValueError) as exc:
        raise ScoringFormulaError(
            f"Scoring formula {formula!r} returned non-numeric {raw!r} on {subject} — "
            "it must evaluate to a number."
        ) from exc
    if not math.isfinite(value):
        raise ScoringFormulaError(
            f"Scoring formula {formula!r} evaluated to {value!r} on {subject} — a non-finite "
            "score is missing data (a division by zero, or a term that was never measured), "
            "not a perfect one. Fix the formula or exclude the measurement."
        )
    return max(0.0, min(1.0, value))


def _build_namespace(result: dict[str, Any]) -> dict[str, Any]:
    pd = result.get("pipeline_data") or {}

    step_tokens = pd.get("step_tokens") or {}
    input_tokens = 0
    output_tokens = 0
    for entry in step_tokens.values():
        if isinstance(entry, dict):
            input_tokens += int(entry.get("input", 0))
            output_tokens += int(entry.get("output", 0))

    ns: dict[str, Any] = {
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


def compile_scorer(formula: str | None) -> Callable[[dict[str, Any]], float]:
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

    def _scorer(result: dict[str, Any]) -> float:
        ns = _build_namespace(result)
        query = str(result.get("query", "?"))[:80]
        try:
            raw = eval(code, _SAFE_BUILTINS, ns)
        except Exception as exc:
            raise ScoringFormulaError(
                f"Scoring formula {formula!r} raised on query {query!r}: "
                f"{type(exc).__name__}: {exc}. The formula references something the "
                "trace doesn't carry (or a matcher rejected the input) — fix the "
                "formula or the pipeline output, don't score it as a wrong answer."
            ) from exc
        return clamp_unit_score(raw, formula=formula, subject=f"query {query!r}")

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


__all__ = [
    "ScoringFormulaError",
    "ScoringTermMissingError",
    "auto_scorer_id",
    "clamp_unit_score",
    "compile_scorer",
    "split_scoring_block",
    "validate_ast",
]
