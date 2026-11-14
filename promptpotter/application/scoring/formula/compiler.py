"""Restricted ``eval`` alone is bypassable: the AST allowlist is the actual boundary."""

from __future__ import annotations

import ast
import functools
import math
import statistics
from collections.abc import Callable, Iterable, Mapping
from types import SimpleNamespace
from typing import Any, NamedTuple

from promptpotter import connectors
from promptpotter.application.scoring.formula.matchers import (
    MATCHERS,
    backend_rank,
    fitness_unit_of,
    matcher_of,
)
from promptpotter.domain.cell_terms import CELL_BOUND_NAMES, CELL_TERMS, CellTerm
from promptpotter.domain.results_health import is_degraded
from promptpotter.domain.scoring import (
    DEFAULT_SCORER_ID,
    PIPELINE_KEYS,
    TURN_SCALAR_KEYS,
    Dial,
    Grade,
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

# Every arithmetic operand passes through here: an int never grows past a float, so `9**9**9`
# raises OverflowError at once, and a string or list can never be repeated.
_NUMERIC = "__numeric__"


def _numeric(value: object) -> float:
    if isinstance(value, int | float):
        return float(value)
    raise TypeError(f"arithmetic takes numbers, not {type(value).__name__}")


def _pow(base: object, exponent: object) -> float:
    return float(_numeric(base) ** _numeric(exponent))


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
        "pow": _pow,
    }
}
_EVAL_BUILTINS = {"__builtins__": {**SAFE_BUILTINS["__builtins__"], _NUMERIC: _numeric}}
# Every name the eval binds beneath the namespace: a term named like one is never read.
EVAL_BUILTIN_NAMES: frozenset[str] = frozenset(_EVAL_BUILTINS["__builtins__"])


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


_CALLABLE_NAMES: frozenset[str] = frozenset(SAFE_BUILTINS["__builtins__"]) | frozenset(MATCHERS)


def _helper_arguments(tree: ast.AST) -> set[int]:
    return {
        id(arg)
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id in MATCHERS
        for arg in (*node.args, *(kw.value for kw in node.keywords))
    }


def validate_ast(tree: ast.AST, *, source: str) -> None:
    helper_arguments = _helper_arguments(tree)
    for node in ast.walk(tree):
        kind = type(node)
        if isinstance(node, ast.Constant) and not (
            node.value is None
            or isinstance(node.value, int | float)
            or (isinstance(node.value, str) and id(node) in helper_arguments)
        ):
            raise ScoringFormulaError(
                f"Scoring formula rejected — the constant {node.value!r} in {source}. A formula "
                "is numeric; a string is only an argument to a scoring helper."
            )
        if kind not in _ALLOWED_AST_NODES:
            raise ScoringFormulaError(
                f"Scoring formula rejected — disallowed syntax {kind.__name__!r} "
                f"in {source}. Allowed: arithmetic, comparisons, calls to the "
                "registered scoring helpers, namespace name lookups."
            )
        # Refused here: at eval a mistyped function reads as a term the record lacks.
        if isinstance(node, ast.Call) and (
            not isinstance(node.func, ast.Name) or node.func.id not in _CALLABLE_NAMES
        ):
            called = ast.unparse(node.func)
            raise ScoringFormulaError(
                f"Scoring formula rejected — {called!r} is not a scoring helper, in {source}. "
                f"Callable: {sorted(_CALLABLE_NAMES)}."
            )


def _as_number(operand: ast.expr) -> ast.expr:
    call = ast.Call(func=ast.Name(id=_NUMERIC, ctx=ast.Load()), args=[operand], keywords=[])
    return ast.copy_location(call, operand)


class _NumericOperands(ast.NodeTransformer):
    def visit_BinOp(self, node: ast.BinOp) -> ast.BinOp:
        self.generic_visit(node)
        node.left, node.right = _as_number(node.left), _as_number(node.right)
        return node

    def visit_UnaryOp(self, node: ast.UnaryOp) -> ast.UnaryOp:
        self.generic_visit(node)
        if not isinstance(node.op, ast.Not):
            node.operand = _as_number(node.operand)
        return node


class CompiledExpression(NamedTuple):
    names: frozenset[str]
    evaluate: Callable[[dict[str, Any], str], float]


# Pure over its two strings, and every read of a campaign's bench or rows compiles its formula.
@functools.lru_cache(maxsize=256)
def compile_expression(formula: str, *, source: str) -> CompiledExpression:
    """A term the record lacks raises ``ScoringTermMissingError``, which a read side reports as *unscorable*."""
    try:
        tree = ast.parse(formula, f"<{source}>", "eval")
        validate_ast(tree, source=source)
        names = frozenset(node.id for node in ast.walk(tree) if isinstance(node, ast.Name))
        numeric = ast.fix_missing_locations(_NumericOperands().visit(tree))
        code = compile(numeric, f"<{source}>", "eval")
    except (SyntaxError, ValueError) as exc:  # ValueError: a null byte
        raise ScoringFormulaError(
            f"Scoring formula rejected — {formula!r} in {source} does not parse: {exc}."
        ) from exc
    except (RecursionError, MemoryError) as exc:
        raise ScoringFormulaError(
            f"Scoring formula rejected — the one in {source} nests past what this server parses."
        ) from exc

    def _evaluate(namespace: dict[str, Any], subject: str) -> float:
        try:
            raw = eval(code, _EVAL_BUILTINS, namespace)
        except NameError as exc:
            carried = sorted(k for k, v in namespace.items() if not callable(v))
            raise ScoringTermMissingError(
                f"The {source} {formula!r} names a term {subject} does not carry: {exc}. "
                f"{subject} carries {carried}."
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


ChannelReader = Callable[[MeasuredCell, Grade | None], float | None]

# The core's own channels: where its reader cannot answer, a connector's report does.
_CHANNEL_READERS: dict[str, ChannelReader] = {
    "fitness": lambda _facts, grade: None if grade is None else _number(grade.fitness),
    # `cost_s`, never `total_time`: a replayed row's `total_time` is zeroed.
    "latency": lambda facts, _grade: facts.cost_s,
    "unworked": lambda facts, _grade: facts.pipeline.unworked_s,
    "cost": lambda facts, _grade: _step_cost(facts.pipeline.step_tokens),
    "tokens": lambda facts, _grade: _step_tokens(facts.pipeline.step_tokens),
}

# Channels whose value another owner serves, so no report answers them: the rank the cell's
# grade binds, the prompt length its `PipelineData` carries.
_BORROWED_CHANNELS: dict[str, ChannelReader] = {
    "ground_truth_rank": lambda _facts, grade: (
        None if grade is None else _number(grade.ground_truth_rank)
    ),
    # Characters, not tokens: no tokenizer ships.
    "target_prompt_chars": lambda facts, _grade: _number(facts.pipeline.target_prompt_chars),
}

# Deliberately NOT channels: a predicate answers for ANY cell, measured or not.
_ROW_HEALTH: dict[str, Callable[[MeasuredCell], float]] = {
    "errored": lambda facts: float(facts.errored),
    "degraded": lambda facts: float(is_degraded(facts)),
    "cached": lambda facts: float(facts.cached),
}

CELL_CHANNELS: tuple[str, ...] = tuple(
    name for name in CELL_TERMS if name in _CHANNEL_READERS or name in _BORROWED_CHANNELS
)
assert set(CELL_TERMS) == {*CELL_CHANNELS, *_ROW_HEALTH}, "a per-cell term went untaught"
# The core terms a report may answer; a health term is the row's own, never a report's.
REPORTABLE_CORE_TERMS: frozenset[str] = frozenset(_CHANNEL_READERS)
ROW_HEALTH_TERMS: frozenset[str] = frozenset(_ROW_HEALTH)


def campaign_terms(connector: str) -> dict[str, CellTerm]:
    """What a campaign's criterion, dial or anchor may name: the core's terms, then its connector's own."""
    declared = connectors.CONNECTORS.get(connector).cell_terms
    return {**CELL_TERMS, **{term.name: term for term in declared}}


def cell_channels_of(facts: MeasuredCell, grade: Grade | None) -> dict[str, float]:
    """A key absent from the result is a channel the cell cannot answer."""
    out: dict[str, float] = {}
    for name, read in (*_CHANNEL_READERS.items(), *_BORROWED_CHANNELS.items()):
        value = read(facts, grade)
        if value is not None:
            out[name] = value
    for name, value in facts.pipeline.cell_report.items():
        if isinstance(value, int | float) and not isinstance(value, bool):
            out.setdefault(name, float(value))
    return out


def cell_namespace(facts: MeasuredCell, grade: Grade) -> dict[str, Any]:
    """An absent term stays unbound: a bound 0 would score a measurement nobody took."""
    ns: dict[str, Any] = {
        "error": facts.error,
        "predicted": facts.predicted,
        "ground_truth": facts.ground_truth,
        **{name: m.score for name, m in MATCHERS.items()},
    }
    # Inside a ranking the rank is bound even at ``None``, which ``rr`` scores as a miss.
    if grade.n_candidates is not None:
        ns["ground_truth_rank"] = grade.ground_truth_rank
        ns["n_candidates"] = grade.n_candidates
    if account := TokenAccount.from_step_tokens(facts.pipeline.step_tokens):
        ns["input_tokens"] = account.input
        ns["output_tokens"] = account.output

    terms = facts.pipeline.terms()
    terms.pop("cell_report", None)
    # The connector's report is bound key by key; a key naming a core term answers that channel alone.
    report = {k: v for k, v in facts.pipeline.cell_report.items() if k not in CELL_TERMS}
    for key, val in {**report, **terms}.items():
        if isinstance(val, Mapping):
            ns[key] = SimpleNamespace(**val)
        elif key not in ns:
            ns[key] = val

    return ns


CELL_INTRINSIC_NAMES: frozenset[str] = CELL_BOUND_NAMES | frozenset(MATCHERS)
"""A ``pipeline_data`` key colliding with one of these is SILENTLY dropped by ``cell_namespace``'s splat."""

# What the namespace binds off `PipelineData`: its keys the cell's own binding does not take
# (`error` is the cell's message; the backend's `PipelineData.error` reaches no formula).
PIPELINE_BOUND_NAMES: frozenset[str] = PIPELINE_KEYS - CELL_INTRINSIC_NAMES

# Maximal: a thinner probe would under-report and let a real collision through.
_INTRINSIC_PROBE = MeasuredCell.from_wire(
    {"sample_key": "_", "pipeline_data": {"step_tokens": {"_": {}}}}
)
_RANKED_PROBE = Grade(None, None, None, None, None, 0)

assert (
    set(cell_namespace(_INTRINSIC_PROBE, _RANKED_PROBE)) - set(_INTRINSIC_PROBE.pipeline.terms())
    == CELL_INTRINSIC_NAMES
), "CELL_INTRINSIC_NAMES drifted from what cell_namespace binds"
assert not (TURN_SCALAR_KEYS & CELL_INTRINSIC_NAMES), (
    "a projected turn scalar colliding with an intrinsic is dropped by cell_namespace's splat, "
    "silently, leaving a key no formula can reach"
)


def objective_namespace(facts: MeasuredCell, grade: Grade) -> dict[str, Any]:
    """The per-sample side wins every collision: one term never means two things across formulas."""
    health = {name: read(facts) for name, read in _ROW_HEALTH.items()}
    return {**cell_channels_of(facts, grade), **health, **cell_namespace(facts, grade)}


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
        raise ScoringFormulaError(
            "compile_scorer: scoring formula is required. "
            "Set ``campaign_config.scoring`` (e.g. "
            '"label_match(predicted, ground_truth)") — a trace carries a prediction '
            "and a ground truth, never a verdict; the formula IS the verdict."
        )

    compiled = compile_expression(per_sample, source="per_sample scoring formula")
    if verifier_graded:
        _refuse_label_formula(per_sample, compiled.names, source="per_sample scoring formula")

    scorer_id = auto_scorer_id(per_sample, per_cell, judge_instrument=judge_instrument)
    matcher = matcher_of(per_sample)
    span = None if matcher is None else MATCHERS[matcher].answer
    own = None if matcher is None else MATCHERS[matcher].ranking
    fields = None if matcher is None else MATCHERS[matcher].fields
    # A rank is a position against a LABEL: a labelless cell sits in no ranking.
    ranking = None if verifier_graded else backend_rank if own is None else own

    def _fitness(facts: MeasuredCell, grade: Grade) -> float:
        query = facts.query[:80]
        value = compiled.evaluate(cell_namespace(facts, grade), f"query {query!r}")
        return clamp_unit_score(value, formula=per_sample, subject=f"query {query!r}")

    unit = fitness_unit_of(per_sample)
    if not per_cell:
        return Scorer(
            id=scorer_id,
            per_cell=None,
            fitness_unit=unit,
            terms=compiled.names,
            matcher=matcher,
            span=span,
            ranking=ranking,
            fields=fields,
            fitness=_fitness,
            objective=None,
        )

    composite = compile_expression(per_cell, source="per_cell scoring formula")
    if verifier_graded:
        _refuse_label_formula(per_cell, composite.names, source="per_cell scoring formula")

    def _objective(facts: MeasuredCell, grade: Grade) -> float:
        subject = f"query {facts.query[:80]!r}"
        namespace = objective_namespace(facts, grade)
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

    return Scorer(
        id=scorer_id,
        per_cell=per_cell,
        fitness_unit=unit,
        terms=compiled.names | composite.names,
        matcher=matcher,
        span=span,
        ranking=ranking,
        fields=fields,
        fitness=_fitness,
        objective=_objective,
    )


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


def _refuse_dials(text: str, why: str, terms: Mapping[str, CellTerm]) -> PayloadInvalidError:
    dialable = sorted(name for name, term in terms.items() if term.dial is not None)
    return PayloadInvalidError(
        f"the dials {text!r} cannot be read: {why}. Dials are spelled 'term=weight' joined by "
        f"commas, each weight between 0 and 1, over {dialable}.",
        code="pipeline_config_invalid",
        details={"dials": text},
    )


def parse_dials(text: str, terms: Mapping[str, CellTerm]) -> dict[str, float]:
    """Refuses a term no dial can weigh: read as weight 0 it would score an undeclared criterion."""
    weights: dict[str, float] = {}
    for part in filter(None, (p.strip() for p in text.split(","))):
        name, sep, raw = (piece.strip() for piece in part.partition("="))
        term = terms.get(name)
        if not sep or term is None or term.dial is None:
            raise _refuse_dials(text, f"{name!r} is not a term a dial weighs", terms)
        if name in weights:
            raise _refuse_dials(text, f"{name!r} is weighed twice", terms)
        try:
            weight = float(raw)
        except ValueError:
            raise _refuse_dials(text, f"{raw!r} is not a number", terms) from None
        if not 0.0 <= weight <= 1.0:
            raise _refuse_dials(text, f"the weight {weight} on {name!r} is outside 0..1", terms)
        weights[name] = weight
    return weights


def spell_dials(weights: Mapping[str, float], terms: Mapping[str, CellTerm]) -> str:
    """Vocabulary order, a zero dial dropped: two declarations of one criterion are one string."""
    return ",".join(f"{name}={weights[name]}" for name in terms if weights.get(name))


def origin_anchors(rows: Iterable[GradedCell], terms: Mapping[str, CellTerm]) -> dict[str, float]:
    """The MEDIAN, so one runaway cell cannot lift the anchor out of reach of every other."""
    carried: dict[str, list[float]] = {}
    for cell in rows:
        if cell.facts.errored:
            continue
        for name, value in cell_channels_of(cell.facts, cell.grade).items():
            if (term := terms.get(name)) is not None and term.dial == "anchored":
                carried.setdefault(name, []).append(value)
    levels = {name: statistics.median(values) for name, values in carried.items()}
    return {name: round(level, 6) for name, level in levels.items() if level > 0.0}


def realize_dials(
    weights: Mapping[str, float], anchors: Mapping[str, float], terms: Mapping[str, CellTerm]
) -> str:
    dials: dict[str, Dial] = {}
    for name, weight in weights.items():
        if not weight:
            continue
        term = terms[name]
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
            raise ScoringFormulaError(
                f"campaign scoring block names {sorted(unknown)}. It carries 'per_sample' (the "
                "cell's correctness) and 'per_cell' (the composite θ is fit on). 'id' is DERIVED "
                "from both — a hand-set one naming only 'per_sample' pooled two composites' grades "
                "onto one δ ruler. 'per_round' was the composite at ROUND scope and is gone — a "
                "latency or reliability term meaned over a panel cannot say which prompt provoked it."
            )
        if DIALS_KEY in block and "per_cell" in block:
            raise ScoringFormulaError(
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
