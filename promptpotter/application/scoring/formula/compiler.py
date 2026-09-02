"""Per-sample formula compiler. Restricted ``eval`` alone is bypassable — the AST allowlist (no attribute access,
comprehensions, lambdas, walrus or subscript) is the actual boundary."""

from __future__ import annotations

import ast
import hashlib
import math
from collections.abc import Callable, Mapping
from types import SimpleNamespace
from typing import Any, NamedTuple, cast

from promptpotter.application.scoring.formula.matchers import SCORING_FUNCTIONS
from promptpotter.domain.l4.proxies import OUTER_PROXY_KEYS
from promptpotter.domain.scoring import (
    DEFAULT_SCORER_ID,
    CellScorer,
    QueryMeasurement,
    ScoringSpec,
    recorded_cost_s,
)
from promptpotter.shared.errors import has_pipeline_warnings, is_error_result

# The L4 recursion's measurand, in logits: one inner campaign's mean-over-rounds lift over its OWN
# origin. Absent on an ordinary campaign, where a cell is a sample and has no origin of its own.
_LIFT_KEY = OUTER_PROXY_KEYS[0]


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


def _number(value: object) -> float | None:
    return float(value) if isinstance(value, int | float) and not isinstance(value, bool) else None


def _step_tokens_sum(pipeline_data: Mapping[str, Any], *keys: str) -> float | None:
    steps = pipeline_data.get("step_tokens")
    if not isinstance(steps, dict) or not steps:
        return None
    total = 0.0
    for entry in steps.values():
        if not isinstance(entry, dict):
            return None
        for key in keys:
            number = _number(entry.get(key))
            if number is None:
                return None
            total += number
    return total


def _own_else_steps(own: float | None, steps: float | None) -> float | None:
    """Two homes, never a fallback chain: a row is EITHER an inner campaign, whose cost the spawn
    site forwards, OR a pipeline sample, whose cost rides ``step_tokens`` — no outer node is
    ``is_llm``, so the two cannot both answer. ``is None`` rather than ``or``, because a cell that
    genuinely cost 0.0 is a MEASUREMENT."""
    return own if own is not None else steps


# THE per-cell numeric vocabulary, one reader per name. `CELL_CHANNELS` derives from this table
# rather than being authored beside it — the set IS the type, so a channel added here reaches the
# evidence picker and the per-cell composite with nothing else to remember.
#
# `latency` is `recorded_cost_s`, which sums `step_timings` — the cache-surviving half. A replayed
# row's `total_time` is zeroed by `query_loop.py::_materialize_cached`, so reading THAT would price
# every cached cell as instantaneous.
_CHANNEL_READERS: dict[str, Callable[[Mapping[str, Any], Mapping[str, Any]], float | None]] = {
    "fitness": lambda row, _pd: _number(row.get("fitness")),
    # Named for the field, never shortened to `rank`: the per-sample formula already binds it under
    # this name (`rr(ground_truth_rank)`), and one field answering to two names in two namespaces
    # is the synonym the root CLAUDE.md forbids.
    "ground_truth_rank": lambda row, _pd: _number(row.get("ground_truth_rank")),
    "latency": lambda row, _pd: recorded_cost_s(cast("QueryMeasurement", row)),
    # The seed's own trajectory. `lift` is what the outer loop SCORES — the mean over the round
    # budget — while `final_lift` is where it actually ended and `peak_lift` the best it reached;
    # a run can score well and end badly, and only carrying all three can show it.
    "lift": lambda _row, pd: _number(pd.get(_LIFT_KEY)),
    "origin": lambda _row, pd: _number(pd.get("inner_origin_level")),
    "final_lift": lambda _row, pd: _number(pd.get("inner_final_lift")),
    "peak_lift": lambda _row, pd: _number(pd.get("inner_peak_lift")),
    "rounds": lambda _row, pd: _number(pd.get("inner_rounds_ran")),
    "round_budget": lambda _row, pd: _number(pd.get("inner_round_budget")),
    "unworked": lambda _row, pd: _number(pd.get("inner_unworked_s")),
    "cost": lambda _row, pd: _own_else_steps(
        _number(pd.get("inner_spend_usd")), _step_tokens_sum(pd, "cost_usd")
    ),
    "tokens": lambda _row, pd: _own_else_steps(
        _number(pd.get("inner_tokens")), _step_tokens_sum(pd, "input", "output")
    ),
}

# The three health facts a cell answers about ITSELF, as 0/1 so a composite can price them — at
# round scope they are one rate over the panel and which prompt provoked it cannot be recovered.
#
# Deliberately NOT channels: a predicate answers for ANY mapping (`is_error_result({})` is False),
# so in the table above `cell_channels_of` would claim a health reading for a row carrying no
# measurement, and the evidence side would score a cell it cannot read at a fabricated 0.
_ROW_HEALTH: dict[str, Callable[[Mapping[str, Any]], float]] = {
    "errored": lambda row: float(is_error_result(row)),
    "degraded": lambda row: float(has_pipeline_warnings(row)),
    "cached": lambda row: float(bool(row.get("cached", False))),
}

CELL_CHANNELS: tuple[str, ...] = tuple(_CHANNEL_READERS)


def cell_channels_of(result: Mapping[str, Any]) -> dict[str, float]:
    """Every channel this ONE row can answer. A key absent from the result is a channel the row
    cannot answer, and every caller downstream treats it that way."""
    pipeline_data = result.get("pipeline_data")
    pd: Mapping[str, Any] = pipeline_data if isinstance(pipeline_data, dict) else {}
    out: dict[str, float] = {}
    for name, read in _CHANNEL_READERS.items():
        value = read(result, pd)
        if value is not None:
            out[name] = value
    return out


def cell_namespace(result: dict[str, Any]) -> dict[str, Any]:
    """A term is bound only where the row CARRIES it. Binding a 0 for an absent count scores the row
    against a measurement nobody took; leaving it out is what raises ``ScoringTermMissingError``, the
    verdict this module's own contract promises and the read side renders as *unscorable*.

    Deliberately WIDER than ``cell_channels_of``: a dataset's per-sample formula reaches whatever its
    own trace carries (``mean_round_delta``), while the declared channels are the tight vocabulary a
    comparison across campaigns can rely on.

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


def objective_namespace(result: dict[str, Any]) -> dict[str, Any]:
    """What the per-cell COMPOSITE reads: the declared channels and row health, plus everything
    the per-sample formula already reaches.

    The per-sample side wins every collision, so one term cannot mean two things across the two
    formulas. Prefer ``latency`` to the ``total_time`` the ``pipeline_data`` splat also binds: a
    replayed row has ``total_time`` zeroed by ``query_loop.py::_materialize_cached``."""
    health = {name: read(result) for name, read in _ROW_HEALTH.items()}
    return {**cell_channels_of(result), **health, **cell_namespace(result)}


def compile_scorer(per_sample: str | None, per_cell: str | None = None) -> CellScorer:
    """The two per-cell numbers, compiled together. ``per_cell`` absent ⇒ the objective IS the
    fitness, which is what makes adopting this cost nothing on a campaign that declares no
    composite: the same float, computed once and stamped twice."""
    if not per_sample:
        raise ValueError(
            "compile_scorer: scoring formula is required. "
            "Set ``campaign_config.scoring`` (e.g. "
            '"exact_match(predicted, ground_truth)") — a trace carries a prediction '
            "and a ground truth, never a verdict; the formula IS the verdict."
        )

    compiled = compile_expression(per_sample, source="per_sample scoring formula")

    def _fitness(result: dict[str, Any]) -> float:
        query = str(result.get("query", "?"))[:80]
        value = compiled.evaluate(cell_namespace(result), f"query {query!r}")
        return clamp_unit_score(value, formula=per_sample, subject=f"query {query!r}")

    if not per_cell:
        return CellScorer(fitness=_fitness, objective=_fitness)

    composite = compile_expression(per_cell, source="per_cell scoring formula")

    def _objective(result: dict[str, Any]) -> float:
        query = str(result.get("query", "?"))[:80]
        value = composite.evaluate(objective_namespace(result), f"query {query!r}")
        return clamp_unit_score(value, formula=per_cell, subject=f"query {query!r}")

    return CellScorer(fitness=_fitness, objective=_objective)


def auto_scorer_id(per_sample: str | None, per_cell: str | None) -> str:
    """Stable id over the WHOLE grading function; ``None``/empty ``per_sample`` → ``default_hit``.

    ``per_cell`` is half of it — the composite IS ``objective`` — and grades cached under this id
    are what a δ ruler is fit on (`hard_sample_archive`), so an id naming only ``per_sample``
    hands one arm the other's grades. Absent, the payload is unchanged, so a campaign declaring
    no composite keeps the id it already has."""
    if not per_sample:
        return DEFAULT_SCORER_ID
    payload = f"{per_sample}\x1f{per_cell}" if per_cell else per_sample
    h = hashlib.sha256(payload.encode("utf-8")).hexdigest()[:10]
    return f"auto_{h}"


def split_scoring_block(
    block: str | dict[str, str] | None,
) -> ScoringSpec:
    if isinstance(block, dict):
        unknown = set(block) - {"per_sample", "per_cell"}
        if unknown:
            raise ValueError(
                f"campaign scoring block names {sorted(unknown)}. It carries 'per_sample' (the "
                "cell's correctness) and 'per_cell' (the composite θ is fit on). 'id' is DERIVED "
                "from both — a hand-set one naming only 'per_sample' pooled two composites' grades "
                "onto one δ ruler. 'per_round' was the composite at ROUND scope and is gone — a "
                "latency or reliability term meaned over a panel cannot say which prompt provoked it."
            )
        per_sample = block.get("per_sample")
        per_cell = block.get("per_cell")
        return ScoringSpec(per_sample, per_cell, auto_scorer_id(per_sample, per_cell))
    if isinstance(block, str) and block:
        return ScoringSpec(block, None, auto_scorer_id(block, None))
    return ScoringSpec(None, None, DEFAULT_SCORER_ID)


__all__ = [
    "CELL_CHANNELS",
    "SAFE_BUILTINS",
    "CompiledExpression",
    "ScoringFormulaError",
    "ScoringTermMissingError",
    "auto_scorer_id",
    "cell_channels_of",
    "cell_namespace",
    "clamp_unit_score",
    "compile_expression",
    "compile_scorer",
    "objective_namespace",
    "split_scoring_block",
    "validate_ast",
]
