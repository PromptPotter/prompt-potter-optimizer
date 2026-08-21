"""Per-sample formula compiler. Restricted ``eval`` alone is bypassable — the AST allowlist (no attribute access,
comprehensions, lambdas, walrus or subscript) is the actual boundary."""

from __future__ import annotations

import ast
import hashlib
import math
from collections.abc import Callable
from types import SimpleNamespace
from typing import Any, NamedTuple

from promptpotter.application.scoring.formula.matchers import SCORING_FUNCTIONS
from promptpotter.domain.scoring import DEFAULT_SCORER_ID, ScoringSpec


class ScoringFormulaError(Exception):
    """A formula raised or returned a non-numeric while scoring. Deterministic — a formula↔trace contract bug, never one
    odd sample — so it MUST halt loud: swallowing it to ``0.0`` silently zeroed an entire campaign's fitness."""


class ScoringTermMissingError(ScoringFormulaError):
    """The formula names a term the measurement does not carry. Distinct from its parent because the readers want opposites:
    the live scorer must HALT, the read-side mask reports *unscorable* and never a fabricated number."""


SAFE_BUILTINS = {
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
# in SAFE_BUILTINS ∪ SCORING_FUNCTIONS ∪ namespace.
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
    for node in ast.walk(tree):
        kind = type(node)
        if kind in _ALLOWED_AST_NODES:
            continue
        raise ValueError(
            f"Scoring formula rejected — disallowed syntax {kind.__name__!r} "
            f"in {source}. Allowed: arithmetic, comparisons, calls to the "
            "registered scoring helpers, namespace name lookups."
        )


class CompiledExpression(NamedTuple):
    """A validated formula plus the names it reads. ``evaluate`` returns a FINITE float or raises."""

    names: frozenset[str]
    evaluate: Callable[[dict[str, Any], str], float]


def compile_expression(formula: str, *, source: str) -> CompiledExpression:
    """The one safe-eval path in the package: parse, allow-list, compile, and classify what goes wrong.

    The classification is the load-bearing half and the reason this is not three functions — a term the
    record does not carry raises ``ScoringTermMissingError`` so a read-side caller can report *unscorable*,
    while everything else raises the parent so the live scorer halts loud."""
    tree = ast.parse(formula, f"<{source}>", "eval")
    validate_ast(tree, source=source)
    code = compile(tree, f"<{source}>", "eval")
    names = frozenset(node.id for node in ast.walk(tree) if isinstance(node, ast.Name))

    def _evaluate(namespace: dict[str, Any], subject: str) -> float:
        try:
            raw = eval(code, SAFE_BUILTINS, namespace)
        except NameError as exc:
            carried = sorted(k for k, v in namespace.items() if not callable(v))
            raise ScoringTermMissingError(
                f"The {source} {formula!r} names a term {subject} does not carry: {exc}. "
                f"{subject} carries {carried} — either the formula is wrong, or this record "
                "predates the term."
            ) from exc
        except Exception as exc:
            raise ScoringFormulaError(
                f"The {source} {formula!r} raised on {subject}: {type(exc).__name__}: {exc}."
            ) from exc
        try:
            value = float(raw)
        except (TypeError, ValueError) as exc:
            raise ScoringFormulaError(
                f"The {source} {formula!r} returned non-numeric {raw!r} on {subject} — "
                "it must evaluate to a number."
            ) from exc
        if not math.isfinite(value):
            raise ScoringFormulaError(
                f"The {source} {formula!r} evaluated to {value!r} on {subject} — a non-finite "
                "result is missing data (a division by zero, or a term that was never measured), "
                "not a perfect one. Fix the formula or exclude the measurement."
            )
        return value

    return CompiledExpression(names=names, evaluate=_evaluate)


def clamp_unit_score(raw: Any, *, formula: str, subject: str) -> float:
    """The ONE gate every formula result passes, per-sample and per-round. NaN must never reach the clamp: ``min(1.0, nan)``
    short-circuits to its first argument, so a ``0/0`` anywhere would silently score a PERFECT sample."""
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
    """A term is bound only where the row CARRIES it. Binding a 0 for an absent count scores the row
    against a measurement nobody took; leaving it out is what raises ``ScoringTermMissingError``, the
    verdict this module's own contract promises and the read side renders as *unscorable*.

    ``ground_truth_rank`` is the exception and stays bound at ``None`` — that is a value, meaning the
    truth was not in the ranking, which is what ``rr`` scores as a miss."""
    pd = result.get("pipeline_data") or {}

    # No ``hit`` here: it is written by ``rescore_results`` AFTER this scorer runs, so a
    # formula naming it read 0 on every fresh row and the PREVIOUS scorer's value on a
    # rescore — order-dependent, and silently so. Ask the matchers instead; they are the
    # arm that decides a label.
    ns: dict[str, Any] = {
        "ground_truth_rank": result.get("ground_truth_rank"),
        "error": result.get("error"),
        "predicted": result.get("predicted", ""),
        "ground_truth": result.get("ground_truth", ""),
        **SCORING_FUNCTIONS,
    }
    if "n_candidates" in result:
        ns["n_candidates"] = result["n_candidates"]
    if step_tokens := (pd.get("step_tokens") or {}):
        entries = [e for e in step_tokens.values() if isinstance(e, dict)]
        ns["input_tokens"] = sum(int(e.get("input", 0)) for e in entries)
        ns["output_tokens"] = sum(int(e.get("output", 0)) for e in entries)

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
            '"exact_match(predicted, ground_truth)") — a trace carries a prediction '
            "and a ground truth, never a verdict; the formula IS the verdict."
        )

    compiled = compile_expression(formula, source="per_sample scoring formula")

    def _scorer(result: dict[str, Any]) -> float:
        query = str(result.get("query", "?"))[:80]
        value = compiled.evaluate(_build_namespace(result), f"query {query!r}")
        return clamp_unit_score(value, formula=formula, subject=f"query {query!r}")

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
    if isinstance(block, dict):
        per_sample = block.get("per_sample")
        per_round = block.get("per_round")
        scorer_id = block.get("id") or auto_scorer_id(per_sample)
        return ScoringSpec(per_sample, per_round, scorer_id)
    if isinstance(block, str) and block:
        return ScoringSpec(block, None, auto_scorer_id(block))
    return ScoringSpec(None, None, DEFAULT_SCORER_ID)


__all__ = [
    "SAFE_BUILTINS",
    "CompiledExpression",
    "ScoringFormulaError",
    "ScoringTermMissingError",
    "auto_scorer_id",
    "clamp_unit_score",
    "compile_expression",
    "compile_scorer",
    "split_scoring_block",
    "validate_ast",
]
