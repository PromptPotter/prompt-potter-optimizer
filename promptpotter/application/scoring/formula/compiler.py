"""Restricted ``eval`` alone is bypassable: the AST allowlist is the actual boundary."""

from __future__ import annotations

import ast
import math
import statistics
from collections.abc import Callable, Iterable, Mapping
from types import SimpleNamespace
from typing import Any, Literal, NamedTuple

from promptpotter.application.scoring.formula.matchers import SCORING_FUNCTIONS
from promptpotter.domain.results_health import is_degraded
from promptpotter.domain.scoring import (
    DEFAULT_SCORER_ID,
    TURN_SCALAR_KEYS,
    Dial,
    DialKind,
    GradedCell,
    MeasuredCell,
    Scorer,
    ScoringFormulaError,
    ScoringSpec,
    ScoringTermMissingError,
    anchored_criterion,
)
from promptpotter.domain.spend import StepUsage, TokenAccount, bill_or_rate_usd
from promptpotter.shared.errors import PayloadInvalidError
from promptpotter.shared.hashing import stable_hash

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


# No Attribute (kills ``().__class__...``), comprehension, lambda, walrus or subscript.
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


_CALLABLE_NAMES: frozenset[str] = frozenset(SAFE_BUILTINS["__builtins__"]) | frozenset(
    SCORING_FUNCTIONS
)


def validate_ast(tree: ast.AST, *, source: str) -> None:
    for node in ast.walk(tree):
        kind = type(node)
        if kind not in _ALLOWED_AST_NODES:
            raise ValueError(
                f"Scoring formula rejected — disallowed syntax {kind.__name__!r} "
                f"in {source}. Allowed: arithmetic, comparisons, calls to the "
                "registered scoring helpers, namespace name lookups."
            )
        # Refused here: at eval a mistyped function reads as a term the record lacks.
        if isinstance(node, ast.Call) and (
            not isinstance(node.func, ast.Name) or node.func.id not in _CALLABLE_NAMES
        ):
            called = ast.unparse(node.func)
            raise ValueError(
                f"Scoring formula rejected — {called!r} is not a scoring helper, in {source}. "
                f"Callable: {sorted(_CALLABLE_NAMES)}."
            )


class CompiledExpression(NamedTuple):
    names: frozenset[str]
    evaluate: Callable[[dict[str, Any], str], float]


def compile_expression(formula: str, *, source: str) -> CompiledExpression:
    """A term the record lacks raises ``ScoringTermMissingError``, which a read side reports as *unscorable*."""
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
    """NaN must never reach the clamp: ``min(1.0, nan)`` is 1.0, scoring a ``0/0`` as PERFECT."""
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


def _number(value: float | None) -> float | None:
    return None if value is None else float(value)


def _step_cost(steps: Mapping[str, StepUsage]) -> float | None:
    """``None`` where any node carries neither figure: a partial sum reads as the whole cost."""
    costs = [bill_or_rate_usd(usage.cost_usd, usage.rate_priced_usd) for usage in steps.values()]
    if not costs or any(cost is None for cost in costs):
        return None
    return sum(cost for cost in costs if cost is not None)


def _step_tokens(steps: Mapping[str, StepUsage]) -> float | None:
    account = TokenAccount.from_step_tokens(steps)
    return None if account is None else float(account.total)


def _own_else_steps(own: float | None, steps: float | None) -> float | None:
    """``is None`` rather than ``or``: a cell that genuinely cost 0.0 is a MEASUREMENT."""
    return own if own is not None else steps


_CHANNEL_READERS: dict[str, Callable[[MeasuredCell, float | None], float | None]] = {
    "fitness": lambda _facts, fitness: _number(fitness),
    "ground_truth_rank": lambda facts, _fitness: _number(facts.ground_truth_rank),
    # `cost_s`, never `total_time`: a replayed row's `total_time` is zeroed.
    "latency": lambda facts, _fitness: facts.cost_s,
    "unworked": lambda facts, _fitness: facts.pipeline.unworked_s,
    "lift": lambda facts, _fitness: facts.pipeline.mean_round_delta,
    "origin": lambda facts, _fitness: facts.pipeline.inner_origin_level,
    "final_lift": lambda facts, _fitness: facts.pipeline.inner_final_lift,
    "peak_lift": lambda facts, _fitness: facts.pipeline.inner_peak_lift,
    "rounds": lambda facts, _fitness: _number(facts.pipeline.inner_rounds_ran),
    "round_budget": lambda facts, _fitness: _number(facts.pipeline.inner_round_budget),
    "cost": lambda facts, _fitness: _own_else_steps(
        facts.pipeline.inner_sent_usd, _step_cost(facts.pipeline.step_tokens)
    ),
    "tokens": lambda facts, _fitness: _own_else_steps(
        _number(facts.pipeline.inner_tokens), _step_tokens(facts.pipeline.step_tokens)
    ),
    # Characters, not tokens: no tokenizer ships.
    "target_prompt_chars": lambda facts, _fitness: _number(facts.pipeline.target_prompt_chars),
}

# Deliberately NOT channels: a predicate answers for ANY cell, measured or not.
_ROW_HEALTH: dict[str, Callable[[MeasuredCell], float]] = {
    "errored": lambda facts: float(facts.errored),
    "degraded": lambda facts: float(is_degraded(facts)),
    "cached": lambda facts: float(facts.cached),
}

CELL_CHANNELS: tuple[str, ...] = tuple(_CHANNEL_READERS)


class CellTerm(NamedTuple):
    direction: Literal["high", "low"]
    description: str
    # ``None`` reaches a criterion through a typed expression only.
    dial: DialKind | None = None
    primary: bool = False


CELL_TERMS: dict[str, CellTerm] = {
    "fitness": CellTerm(
        "high", "The cell's correctness under the per-sample formula.", primary=True
    ),
    "ground_truth_rank": CellTerm("low", "Where the truth landed in the ranking; 1 is the top."),
    "latency": CellTerm(
        "low", "Seconds the cell took when measured; a replay keeps them.", "anchored", True
    ),
    "unworked": CellTerm(
        "low", "Seconds the cell sat blocked (suspend, rate-limit queue).", "anchored"
    ),
    "lift": CellTerm("high", "L4: the inner campaign's mean lift over its own origin."),
    "origin": CellTerm("high", "L4: the inner campaign's origin level."),
    "final_lift": CellTerm("high", "L4: the lift the inner campaign ended on."),
    "peak_lift": CellTerm("high", "L4: the best lift the inner campaign reached."),
    "rounds": CellTerm("low", "L4: rounds the inner campaign ran.", "anchored"),
    "round_budget": CellTerm("high", "L4: rounds the inner campaign was allowed."),
    "cost": CellTerm("low", "USD the cell cost.", "anchored", True),
    "tokens": CellTerm("low", "Input plus output tokens the cell spent.", "anchored", True),
    "target_prompt_chars": CellTerm(
        "low", "Characters of the candidate's prompt template.", "anchored", True
    ),
    "errored": CellTerm("low", "1 where the cell errored, else 0.", "unit"),
    "degraded": CellTerm("low", "1 where a pipeline node did not finish cleanly, else 0.", "unit"),
    "cached": CellTerm(
        "high", "1 where the cell was replayed from the archive, else 0.", "unit", True
    ),
}
assert set(CELL_TERMS) == {*_CHANNEL_READERS, *_ROW_HEALTH}, "a per-cell term went untaught"
assert all(term.direction == "low" for term in CELL_TERMS.values() if term.dial == "anchored"), (
    "an anchored dial reads a cost against its origin level, so it is lower-is-better"
)


def cell_channels_of(facts: MeasuredCell, fitness: float | None) -> dict[str, float]:
    """A key absent from the result is a channel the cell cannot answer."""
    out: dict[str, float] = {}
    for name, read in _CHANNEL_READERS.items():
        value = read(facts, fitness)
        if value is not None:
            out[name] = value
    return out


def cell_namespace(facts: MeasuredCell) -> dict[str, Any]:
    """An absent term stays unbound: a bound 0 would score a measurement nobody took."""
    # ``ground_truth_rank`` is bound even at ``None``, which ``rr`` scores as a miss.
    ns: dict[str, Any] = {
        "ground_truth_rank": facts.ground_truth_rank,
        "error": facts.error,
        "predicted": facts.predicted,
        "ground_truth": facts.ground_truth,
        **SCORING_FUNCTIONS,
    }
    if facts.n_candidates is not None:
        ns["n_candidates"] = facts.n_candidates
    if account := TokenAccount.from_step_tokens(facts.pipeline.step_tokens):
        ns["input_tokens"] = account.input
        ns["output_tokens"] = account.output

    for key, val in facts.pipeline.terms().items():
        if isinstance(val, Mapping):
            ns[key] = SimpleNamespace(**val)
        elif key not in ns:
            ns[key] = val

    return ns


CELL_INTRINSIC_NAMES: frozenset[str] = frozenset(
    {
        "ground_truth_rank",
        "error",
        "predicted",
        "ground_truth",
        "n_candidates",
        "input_tokens",
        "output_tokens",
        *SCORING_FUNCTIONS,
    }
)
"""A ``pipeline_data`` key colliding with one of these is SILENTLY dropped by ``cell_namespace``'s splat."""

_INTRINSIC_PROBE = MeasuredCell.from_wire(
    {
        "sample_id": 0,
        # Maximal: a thinner probe would under-report and let a real collision through.
        "n_candidates": 0,
        "pipeline_data": {"step_tokens": {"_": {}}},
    }
)

assert (
    set(cell_namespace(_INTRINSIC_PROBE)) - set(_INTRINSIC_PROBE.pipeline.terms())
    == CELL_INTRINSIC_NAMES
), "CELL_INTRINSIC_NAMES drifted from what cell_namespace binds"
assert not (TURN_SCALAR_KEYS & CELL_INTRINSIC_NAMES), (
    "a projected turn scalar colliding with an intrinsic is dropped by cell_namespace's splat, "
    "silently, leaving a key no formula can reach"
)


def objective_namespace(facts: MeasuredCell, fitness: float) -> dict[str, Any]:
    """The per-sample side wins every collision: one term never means two things across formulas."""
    health = {name: read(facts) for name, read in _ROW_HEALTH.items()}
    return {**cell_channels_of(facts, fitness), **health, **cell_namespace(facts)}


_LABEL_TERM = "ground_truth"

# The share of its SOLVED composite a missed cell keeps; `auto_scorer_id` hashes it.
MISS_COST_SHARE = 0.2


def _refuse_label_formula(formula: str, names: frozenset[str], *, source: str) -> None:
    """``label_match`` scores an empty prediction against the empty label as a PERFECT 1.0."""
    if _LABEL_TERM not in names:
        return
    raise PayloadInvalidError(
        f"the {source} {formula!r} compares against {_LABEL_TERM}, but this dataset's cells carry "
        f"no label — its backend answers with a number that its own verifier decided. Every cell "
        f"would be graded against an empty string, which `label_match` scores as a PERFECT 1.0 "
        f"wherever the prediction is also empty. Score the observation the backend emits instead "
        f"(the key the connector declares in `required_observation_keys`, e.g. "
        f"`max(0.0, min(1.0, env_reward))`).",
        code="pipeline_config_invalid",
        details={"formula": formula, "source": source},
    )


def compile_scorer(
    per_sample: str | None,
    per_cell: str | None = None,
    *,
    verifier_graded: bool,
    judge_instrument: str | None = None,
) -> Scorer:
    """``verifier_graded`` has no default: ``False`` would read as armed wherever a caller forgot it."""
    if not per_sample:
        raise ValueError(
            "compile_scorer: scoring formula is required. "
            "Set ``campaign_config.scoring`` (e.g. "
            '"label_match(predicted, ground_truth)") — a trace carries a prediction '
            "and a ground truth, never a verdict; the formula IS the verdict."
        )

    compiled = compile_expression(per_sample, source="per_sample scoring formula")
    if verifier_graded:
        _refuse_label_formula(per_sample, compiled.names, source="per_sample scoring formula")

    scorer_id = auto_scorer_id(per_sample, per_cell, judge_instrument=judge_instrument)

    def _fitness(facts: MeasuredCell) -> float:
        query = facts.query[:80]
        value = compiled.evaluate(cell_namespace(facts), f"query {query!r}")
        return clamp_unit_score(value, formula=per_sample, subject=f"query {query!r}")

    if not per_cell:
        return Scorer(id=scorer_id, per_cell=None, fitness=_fitness, objective=None)

    composite = compile_expression(per_cell, source="per_cell scoring formula")
    if verifier_graded:
        _refuse_label_formula(per_cell, composite.names, source="per_cell scoring formula")

    def _objective(facts: MeasuredCell, fitness: float) -> float:
        subject = f"query {facts.query[:80]!r}"
        namespace = objective_namespace(facts, fitness)
        charged = clamp_unit_score(
            composite.evaluate(namespace, subject), formula=per_cell, subject=subject
        )
        solved = clamp_unit_score(
            composite.evaluate({**namespace, "fitness": 1.0}, subject),
            formula=per_cell,
            subject=subject,
        )
        # Written as a step from `charged`, so a solved cell returns its composite bit-for-bit.
        return charged + MISS_COST_SHARE * (solved - charged)

    return Scorer(id=scorer_id, per_cell=per_cell, fitness=_fitness, objective=_objective)


def auto_scorer_id(
    per_sample: str | None, per_cell: str | None, *, judge_instrument: str | None
) -> str:
    """``per_cell`` and the judges are in the id: a δ ruler is fit on the grades cached under it."""
    if not per_sample:
        return DEFAULT_SCORER_ID
    formula = {
        "per_sample": per_sample,
        "per_cell": per_cell or None,
        "miss_cost_share": MISS_COST_SHARE if per_cell else None,
        "judge": judge_instrument,
    }
    return f"auto_{stable_hash(formula, length=8)}"


# Becomes ``per_cell`` once the origin is measured (`application/origin.py::lock_criterion`).
DIALS_KEY = "dials"


def _refuse_dials(text: str, why: str) -> PayloadInvalidError:
    dialable = sorted(name for name, term in CELL_TERMS.items() if term.dial is not None)
    return PayloadInvalidError(
        f"the dials {text!r} cannot be read: {why}. Dials are spelled 'term=weight' joined by "
        f"commas, each weight between 0 and 1, over {dialable}.",
        code="pipeline_config_invalid",
        details={"dials": text},
    )


def parse_dials(text: str) -> dict[str, float]:
    """Refuses a term no dial can weigh: read as weight 0 it would score an undeclared criterion."""
    weights: dict[str, float] = {}
    for part in filter(None, (p.strip() for p in text.split(","))):
        name, sep, raw = (piece.strip() for piece in part.partition("="))
        term = CELL_TERMS.get(name)
        if not sep or term is None or term.dial is None:
            raise _refuse_dials(text, f"{name!r} is not a term a dial weighs")
        if name in weights:
            raise _refuse_dials(text, f"{name!r} is weighed twice")
        try:
            weight = float(raw)
        except ValueError:
            raise _refuse_dials(text, f"{raw!r} is not a number") from None
        if not 0.0 <= weight <= 1.0:
            raise _refuse_dials(text, f"the weight {weight} on {name!r} is outside 0..1")
        weights[name] = weight
    return weights


def spell_dials(weights: Mapping[str, float]) -> str:
    """Vocabulary order, a zero dial dropped: two declarations of one criterion are one string."""
    return ",".join(f"{name}={weights[name]}" for name in CELL_TERMS if weights.get(name))


def origin_anchors(rows: Iterable[GradedCell]) -> dict[str, float]:
    """The MEDIAN, so one runaway cell cannot lift the anchor out of reach of every other."""
    carried: dict[str, list[float]] = {}
    for cell in rows:
        if cell.facts.errored:
            continue
        for name, value in cell_channels_of(cell.facts, cell.grade.fitness).items():
            if CELL_TERMS[name].dial == "anchored":
                carried.setdefault(name, []).append(value)
    levels = {name: statistics.median(values) for name, values in carried.items()}
    return {name: round(level, 6) for name, level in levels.items() if level > 0.0}


def realize_dials(weights: Mapping[str, float], anchors: Mapping[str, float]) -> str:
    dials: dict[str, Dial] = {}
    for name, weight in weights.items():
        if not weight:
            continue
        term = CELL_TERMS[name]
        if term.dial != "anchored":
            dials[name] = Dial(weight, rewards=term.direction == "high")
            continue
        if name not in anchors:
            raise PayloadInvalidError(
                f"the dial on {name!r} has nothing to be read against: the origin's cells "
                f"measured no {name} above zero. Set it to 0, or score an origin that carries it.",
                code="pipeline_config_invalid",
                details={"term": name},
            )
        dials[name] = Dial(weight, anchors[name])
    return anchored_criterion(dials)


def split_scoring_block(
    block: str | dict[str, str] | None, *, judge_instrument: str | None
) -> ScoringSpec:
    if isinstance(block, dict):
        unknown = set(block) - {"per_sample", "per_cell", DIALS_KEY}
        if unknown:
            raise ValueError(
                f"campaign scoring block names {sorted(unknown)}. It carries 'per_sample' (the "
                "cell's correctness) and 'per_cell' (the composite θ is fit on). 'id' is DERIVED "
                "from both — a hand-set one naming only 'per_sample' pooled two composites' grades "
                "onto one δ ruler. 'per_round' was the composite at ROUND scope and is gone — a "
                "latency or reliability term meaned over a panel cannot say which prompt provoked it."
            )
        if DIALS_KEY in block and "per_cell" in block:
            raise ValueError(
                f"campaign scoring block carries both '{DIALS_KEY}' and 'per_cell'. Dials DECLARE "
                "the composite and the origin's measurement turns them into 'per_cell'; a block "
                "naming both says two criteria."
            )
        per_sample = block.get("per_sample")
        # Dials not yet locked grade on correctness alone, which is all an unmeasured origin has.
        per_cell = block.get("per_cell")
        scorer_id = auto_scorer_id(per_sample, per_cell, judge_instrument=judge_instrument)
        return ScoringSpec(per_sample, per_cell, scorer_id)
    if isinstance(block, str) and block:
        return ScoringSpec(
            block, None, auto_scorer_id(block, None, judge_instrument=judge_instrument)
        )
    return ScoringSpec(None, None, DEFAULT_SCORER_ID)


__all__ = [
    "CELL_CHANNELS",
    "CELL_TERMS",
    "DIALS_KEY",
    "SAFE_BUILTINS",
    "CellTerm",
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
    "origin_anchors",
    "parse_dials",
    "realize_dials",
    "spell_dials",
    "split_scoring_block",
    "validate_ast",
]
