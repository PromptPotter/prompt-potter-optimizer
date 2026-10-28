from __future__ import annotations

import ast
import math
from collections import Counter
from collections.abc import Callable, Iterable, Iterator, Mapping, Sequence
from dataclasses import dataclass, field, replace
from functools import cached_property
from typing import (
    Annotated,
    Any,
    Literal,
    NamedTuple,
    TypedDict,
    get_args,
)

from pydantic import (
    PlainSerializer,
    PlainValidator,
    WithJsonSchema,
)
from pydantic_core import core_schema

from promptpotter.domain.l4.inner_facts import InnerCellFacts
from promptpotter.domain.paired_reading import MeasurandUnit
from promptpotter.domain.spend import StepUsage
from promptpotter.domain.wire_record import (
    ALWAYS,
    BY_HAND,
    REST,
    WireRecord,
    always_as,
    real,
    text,
    whole,
)
from promptpotter.shared.errors import (
    ERROR_IS_CHARGED,
    BadRequestError,
    ErrorCategory,
    error_category,
    is_error_result,
)
from promptpotter.shared.hashing import shapes_optimizer_prompt

# What a cell's `predicted` reads when its pipeline ran and emitted nothing parseable.
NO_RESULT: Annotated[str, shapes_optimizer_prompt] = "NO_RESULT"

# Above this many distinct ground truths the answer space is open: no enumerable label identity.
ANSWER_SPACE_CAP: Annotated[int, shapes_optimizer_prompt] = 10


class TurnRecord(TypedDict, total=False):
    """ONE turn of a multi-turn cell, a narrowed projection of an ATIF trajectory ``Step``."""

    index: int
    # ``system`` | ``user`` | ``agent`` — ATIF's ``source``.
    source: str
    # Absent on a single-step cell.
    step: str
    message: str
    reasoning: str
    # Tool NAMES only, in call order.
    tools: list[str]
    observation: str


# Disjoint from `CELL_INTRINSIC_NAMES` (`formula/compiler.py`): the splat drops a collision silently.
TURN_SCALAR_KEYS: frozenset[str] = frozenset({"n_turns", "n_tool_calls"})


def turn_scalars(turns: list[TurnRecord] | None) -> dict[str, float]:
    """Per-step keys are ``{step}_turns``, kept only where a formula can name them."""
    if not turns:
        return {}
    out: dict[str, float] = {
        "n_turns": float(len(turns)),
        "n_tool_calls": float(sum(len(t.get("tools") or ()) for t in turns)),
    }
    per_step: dict[str, int] = {}
    for t in turns:
        if name := str(t.get("step") or ""):
            per_step[name] = per_step.get(name, 0) + 1
    for name, count in per_step.items():
        if (term := f"{name}_turns").isidentifier():
            out[term] = float(count)
    return out


@dataclass(frozen=True, slots=True)
class NodeWarning:
    """``kind`` is the BACKEND's stamp: an absent or unrecognized one is SKIPPED, never guessed."""

    step: str = field(default="", metadata=ALWAYS)
    code: str = field(default="", metadata=ALWAYS)
    message: str = field(default="", metadata=ALWAYS)
    kind: str = field(default="", metadata=ALWAYS)
    details: tuple[Any, ...] | None = None
    stats: Mapping[str, Any] | None = None

    @classmethod
    def from_wire(cls, warning: Mapping[str, object]) -> NodeWarning:
        return _NODE_WARNING.read(warning)

    def wire(self) -> dict[str, object]:
        return _NODE_WARNING.write(self)


_NODE_WARNING = WireRecord.of(
    NodeWarning, "NodeWarningRecord", "A :class:`NodeWarning` on the wire."
)


@dataclass(frozen=True, slots=True)
class Diagnostics:
    step_statuses: Mapping[str, str] = field(default_factory=dict)
    warnings: tuple[NodeWarning, ...] = ()

    @classmethod
    def from_wire(cls, diagnostics: Mapping[str, object]) -> Diagnostics:
        return _DIAGNOSTICS.read(diagnostics)

    def wire(self) -> dict[str, object]:
        return _DIAGNOSTICS.write(self)


_DIAGNOSTICS = WireRecord.of(
    Diagnostics,
    "DiagnosticsRecord",
    "A :class:`Diagnostics` on the wire; a key is absent where it would be empty.",
)


@dataclass(frozen=True, slots=True)
class JudgeReading:
    """Beside the term's score, never in place of it; a failed grading has a reason and no label."""

    label: str | None = None
    why: str | None = None

    @classmethod
    def from_wire(cls, reading: Mapping[str, object]) -> JudgeReading:
        return _JUDGE_READING.read(reading)

    def wire(self) -> dict[str, object]:
        return _JUDGE_READING.write(self)


_JUDGE_READING = WireRecord.of(
    JudgeReading,
    "JudgeReadingRecord",
    "A :class:`JudgeReading` on the wire; a key is absent where the judge said nothing.",
)


@dataclass(frozen=True, slots=True)
class PipelineData:
    """``None`` is a key the record did not carry; ``0.0`` is a reading."""

    total_time: float | None = None
    # The DEEPEST node that ran — never a failure signal — and the archive's reuse depth.
    terminal_node: str | None = None
    step_timings: Mapping[str, float] = field(default_factory=dict)
    # The ONLY place a row's token counts live (`TokenAccount.from_step_tokens`): declare no twin.
    step_tokens: Mapping[str, StepUsage] = field(default_factory=dict)
    diagnostics: Diagnostics = field(default_factory=Diagnostics)
    # Seconds BLOCKED, not working. ABSENT, never 0.0, on a row no cell send measured.
    unworked_s: float | None = None
    # L4: one outer sample IS a whole inner campaign, so its "answer" is its held-out lift, the
    # scored term; `mean_round_delta`, the search split's θ lift, is an `InnerCellFacts` fact.
    mean_bench_lift: float | None = None
    # Written by the recursion alone, so a row carrying it IS an L4 outer row.
    inner: InnerCellFacts | None = None
    # The BACKEND reporting a fault on a row PromptPotter classified clean.
    error: str | None = None
    result_ranking: tuple[Any, ...] | None = None
    reasoning_trace: str | None = None
    # ``None`` means "this backend has no turn concept", never `[]`.
    turns: tuple[TurnRecord, ...] | None = None
    outcome_note: str | None = None
    # Sub-node structure beside ``step_timings``, summed by nobody. ``None``, never ``{}``.
    step_phases: Mapping[str, float] | None = None
    # By the TERM each judge graded, in declaration order.
    judge_readings: Mapping[str, JudgeReading] = field(default_factory=dict)
    # Chars of the prompt TEMPLATE, before a sample is interpolated: one number per candidate.
    target_prompt_chars: int | None = None
    # The bare question, where the dataset declared one distinct from `query` — what a judge reads.
    question: str | None = None
    # What only the DATASET names: observation keys, turn scalars, banked evaluator and judge terms.
    observations: Mapping[str, Any] = field(default_factory=dict, metadata=REST)

    @classmethod
    def from_wire(cls, data: Mapping[str, object]) -> PipelineData:
        return _PIPELINE.read(data)

    def wire(self) -> dict[str, object]:
        """A key is written only where it says something: an absent key and a default are one state."""
        return _PIPELINE.write(self)

    def terms(self) -> dict[str, object]:
        return _PIPELINE.said(self)


_PIPELINE = WireRecord.of(
    PipelineData,
    "PipelineRecord",
    "The flat ``pipeline_data`` record, as :meth:`PipelineData.wire` writes it.\n\n"
    "Each declared key is present only where its field says something; every other key is one of\n"
    "the dataset's own observations.",
)

PIPELINE_KEYS: frozenset[str] = _PIPELINE.keys

# The UNION of what the terminal tape and the dashboard render: the ledger holds what was shown.
LEDGER_PIPELINE_KEYS: frozenset[str] = frozenset(
    {
        "total_time",
        "terminal_node",
        "step_timings",
        "step_tokens",
        "diagnostics",
        "unworked_s",
        "mean_bench_lift",
        "inner",
        "error",
    }
)

assert LEDGER_PIPELINE_KEYS <= PIPELINE_KEYS, "a ledger key must be one PipelineData declares"


class RerunComparison(TypedDict):
    """What re-measuring a degraded cached cell changed, in the words the tape prints."""

    hit_change: str
    rank_change: str | None
    improved: bool


@dataclass(frozen=True, slots=True)
class MeasuredCell:
    """``sample_key`` is what the cell IS: replay matches on it. Its position is the bank's."""

    sample_key: str = field(metadata=ALWAYS)
    query: str = field(default="", metadata=ALWAYS)
    ground_truth: str = field(default="", metadata=ALWAYS)
    predicted: str = field(default="", metadata=ALWAYS)
    # A plain human message (no ``[TAG]`` prefix); the CATEGORY owns "this cell errored".
    error: str | None = field(default=None, metadata=ALWAYS)
    error_category: ErrorCategory | None = field(default=None, metadata=BY_HAND)
    pipeline: PipelineData = field(
        default_factory=PipelineData, metadata=always_as("pipeline_data")
    )
    cached: bool = field(default=False, metadata=ALWAYS)
    # The archive's address, ``{cell file}.{answer id}``; ``None`` where nothing was filed.
    answer: str | None = None
    retry_of_deprecated_cache: bool = False
    retry_of_degraded: bool = False
    rerun_comparison: RerunComparison | None = None
    switched_out: bool = False
    config_fundamental_skip: bool = False
    persistently_degraded: bool = False
    degraded_observed: bool = False
    degraded_obs_count: int | None = None
    degraded_obs_threshold: int | None = None

    @classmethod
    def from_wire(cls, row: Mapping[str, object]) -> MeasuredCell:
        """Tolerant of what a producer omits, deaf to undeclared keys, strict on the sample key."""
        key = row.get("sample_key")
        if not isinstance(key, str) or not key:
            raise KeyError(f"a measured cell names its sample; this record carries {key!r}")
        return _CELL.read(
            row,
            sample_key=key,
            error_category=(
                (error_category(row) or ErrorCategory.UNKNOWN) if is_error_result(row) else None
            ),
        )

    def wire(self) -> dict[str, object]:
        out = _CELL.write(self)
        if self.error_category is not None:
            out["error_category"] = self.error_category.value
        return out

    def ledger_wire(self) -> dict[str, object]:
        out = self.wire()
        if isinstance(pipeline_data := out["pipeline_data"], dict):
            out["pipeline_data"] = {
                k: v for k, v in pipeline_data.items() if k in LEDGER_PIPELINE_KEYS
            }
        return out

    @property
    def errored(self) -> bool:
        return self.error_category is not None

    @property
    def charged(self) -> bool:
        """Whether it errored for a reason the configuration under test answers for."""
        return self.error_category is not None and ERROR_IS_CHARGED[self.error_category]

    @property
    def verifier_graded(self) -> bool:
        return is_verifier_graded(self.ground_truth)

    def replayed(self) -> MeasuredCell:
        if self.pipeline == PipelineData():
            return replace(self, cached=True)
        return replace(self, cached=True, pipeline=replace(self.pipeline, total_time=0.0))

    @property
    def elapsed_s(self) -> float | None:
        """A replay's ``0.0`` is a true reading; what a cell COST is :attr:`cost_s`."""
        return self.pipeline.total_time

    @property
    def cost_s(self) -> float | None:
        """The first run's work, so a replay still prices the cell; an EMPTY map is unpriced."""
        timings = self.pipeline.step_timings
        return sum(timings.values()) if timings else None

    @property
    def shown_s(self) -> float | None:
        """A replay occupied no clock, so a clock column shows what the cell took when MEASURED."""
        return self.cost_s if self.cached else self.elapsed_s


_CELL = WireRecord.of(
    MeasuredCell,
    "MeasuredCellRecord",
    "A :class:`MeasuredCell` on the wire, as :meth:`MeasuredCell.wire` writes it.",
)


# What `archive_maintenance.py` may move to the cold store.
# No ranking may join: a row cannot tell a MOVED ranking from an empty one, so it grades a miss.
UNREAD_PIPELINE_KEYS: frozenset[str] = frozenset(
    {"reasoning_trace", "total_time", "turns", "step_phases"}
)

assert UNREAD_PIPELINE_KEYS <= PIPELINE_KEYS, "an unread key must be one PipelineData declares"


# ``(sample_key, answer, replayed)``: walks pair on the key.
WalkedCell = tuple[str, str, bool]


class ScoringFormulaError(BadRequestError):
    """A formula↔trace contract bug every cell fails: it halts loud, never swallowed to ``0.0``."""


class ScoringTermMissingError(ScoringFormulaError):
    """PER-CELL, unlike its parent: ``Scorer.grade`` reads the row UNSCORED and keeps the paid."""


class Grade(NamedTuple):
    """ERRORED is both ``0.0``, graded without the formula; UNSCORED is both ``None`` + a reason."""

    fitness: float | None
    objective: float | None
    unscored: str | None
    # What the matcher compared to the truth; `None` where the formula reads no answer text.
    span: str | None
    # Where the truth sits in the ranking the formula reads, and its width; both `None` where it
    # reads none. A `None` rank beside a width is a value: the truth was not in the ranking.
    ground_truth_rank: int | None
    n_candidates: int | None

    @classmethod
    def from_wire(cls, row: Mapping[str, object]) -> Grade:
        fitness, objective, unscored, span, rank, width = (row.get(key) for key in cls._fields)
        return cls(
            None if fitness is None else real(fitness, "Grade.fitness"),
            None if objective is None else real(objective, "Grade.objective"),
            None if unscored is None else text(unscored, "Grade.unscored"),
            None if span is None else text(span, "Grade.span"),
            None if rank is None else whole(rank, "Grade.ground_truth_rank"),
            None if width is None else whole(width, "Grade.n_candidates"),
        )

    def wire(self) -> dict[str, object]:
        if self.unscored is not None:
            return {"unscored": self.unscored}
        out: dict[str, object] = {"fitness": self.fitness, "objective": self.objective}
        if self.span is not None:
            out["span"] = self.span
        if self.n_candidates is not None:
            out["ground_truth_rank"] = self.ground_truth_rank
            out["n_candidates"] = self.n_candidates
        return out


_ERRORED = Grade(0.0, 0.0, None, None, None, None)

GRADE_KEYS: frozenset[str] = frozenset(Grade._fields)

assert not (GRADE_KEYS & _CELL.keys), "a grade is a formula's reading, never a fact of the cell"


#: ERR and UNSC are states of their own, not a bad MISS; a re-grade recovers UNSC.
SampleStatus = Literal["HIT", "MISS", "ERR", "UNSC"]


@dataclass(frozen=True, slots=True)
class GradedCell:
    facts: MeasuredCell
    grade: Grade

    @classmethod
    def of(cls, facts: MeasuredCell, grade: Grade) -> GradedCell:
        return cls(facts, _ERRORED if facts.errored else grade)

    @classmethod
    def from_wire(cls, row: Mapping[str, object]) -> GradedCell:
        return cls.of(MeasuredCell.from_wire(row), Grade.from_wire(row))

    @property
    def scored(self) -> bool:
        if self.grade.fitness is None:
            return False
        return not self.facts.errored or (self.facts.charged and not self.facts.verifier_graded)

    @property
    def key(self) -> str:
        """What the cell IS (``Sample.key``) — what two individuals pair on."""
        return self.facts.sample_key

    @property
    def address(self) -> WalkedCell | None:
        """Where the archive holds it; ``None`` for a cell never filed."""
        answer = self.facts.answer
        return None if answer is None else (self.key, answer, self.facts.cached)

    @property
    def hit(self) -> bool:
        return is_hit(self.grade.fitness)

    def filed(self, facts: MeasuredCell) -> GradedCell:
        """Only a field no formula reads may differ from the facts this grade was read off."""
        return GradedCell.of(facts, self.grade)

    def wire(self) -> dict[str, object]:
        return {**self.facts.wire(), **self.grade.wire()}

    def ledger_wire(self) -> dict[str, object]:
        return {**self.facts.ledger_wire(), **self.grade.wire()}


def _ledger_cell(cell: GradedCell | Mapping[str, object]) -> GradedCell:
    return GradedCell.from_wire(cell.ledger_wire() if isinstance(cell, GradedCell) else cell)


# Narrowed on the way IN as on the way out, so a record built live equals itself read back.
LedgerCell = Annotated[
    GradedCell,
    PlainValidator(_ledger_cell),
    PlainSerializer(GradedCell.ledger_wire),
    WithJsonSchema({"type": "object", "additionalProperties": True}),
]


@dataclass(frozen=True)
class CellSheet:
    """A cell's STANDING row is its last scored row, else its last; statistics read the scored."""

    scorer_id: str
    cells: tuple[GradedCell, ...] = ()

    def __len__(self) -> int:
        return len(self.cells)

    def __iter__(self) -> Iterator[GradedCell]:
        return iter(self.cells)

    def __bool__(self) -> bool:
        return bool(self.cells)

    def wire(self) -> list[dict[str, object]]:
        return [cell.wire() for cell in self.cells]

    def standing(self) -> CellSheet:
        """Folded AFTER the grade, so the replicate rule can read it."""
        return CellSheet(self.scorer_id, tuple(self.by_key().values()))

    def _standing[K](self, key: Callable[[GradedCell], K]) -> dict[K, GradedCell]:
        held: dict[K, GradedCell] = {}
        for cell in self.cells:
            k = key(cell)
            if cell.scored or k not in held or not held[k].scored:
                held[k] = cell
        return held

    def by_key(self) -> dict[str, GradedCell]:
        return self._standing(lambda cell: cell.key)

    @cached_property
    def scoreable(self) -> tuple[GradedCell, ...]:
        """The EVIDENCE population: one standing row per cell that carries a verdict."""
        return tuple(cell for cell in self.by_key().values() if cell.scored)

    def column(self, column: GradeColumn) -> dict[str, float]:
        return {cell.key: column.read(cell) for cell in self.scoreable}

    def where(self, keep: Callable[[GradedCell], bool]) -> CellSheet:
        return CellSheet(self.scorer_id, tuple(cell for cell in self.cells if keep(cell)))

    def merged(self, later: CellSheet) -> CellSheet:
        """*later* wins each cell both scored."""
        if not later.cells:
            return self
        if not self.cells:
            return later
        if self.scorer_id != later.scorer_id:
            raise ValueError(
                f"cells graded under {later.scorer_id!r} cannot join a sheet graded under "
                f"{self.scorer_id!r}"
            )
        return CellSheet(self.scorer_id, (*self.cells, *later.cells))

    def addresses(self) -> list[WalkedCell]:
        return [address for cell in self.cells if (address := cell.address) is not None]

    @classmethod
    def __get_pydantic_core_schema__(
        cls, _source: object, _handler: object
    ) -> core_schema.CoreSchema:
        # Held as itself, serialized as its rows: no reader can hand a model rows nobody graded.
        return core_schema.is_instance_schema(
            cls,
            serialization=core_schema.plain_serializer_function_ser_schema(
                lambda sheet: sheet.wire(),
                return_schema=core_schema.list_schema(
                    core_schema.dict_schema(core_schema.str_schema(), core_schema.any_schema())
                ),
            ),
        )


NO_CELLS = CellSheet("")


@dataclass(frozen=True)
class Scorer:
    """``fitness`` is CORRECTNESS; ``objective`` is WORTH, the one θ is fit on, ``None`` = same."""

    id: str
    per_cell: str | None
    # The one matcher ``per_sample`` calls, which decides what a prediction's answer is.
    matcher: str | None
    # That matcher's span: what its score reads off a reply.
    span: Callable[[str], str] | None
    # Where the truth sits in the ranking the formula reads, and its width; `None` on a cell in none.
    ranking: Callable[[MeasuredCell], tuple[int | None, int] | None] | None
    fitness: Callable[[MeasuredCell, Grade], float]
    # Reads the cell WITH its fitness: every shipped composite names it.
    objective: Callable[[MeasuredCell, Grade], float] | None

    def grade(self, facts: MeasuredCell) -> GradedCell:
        """Only ``ScoringTermMissingError`` resolves to UNSCORED; its parent halts loud."""
        if facts.errored:
            return GradedCell.of(facts, _ERRORED)
        ranked = None if self.ranking is None else self.ranking(facts)
        rank, width = (None, None) if ranked is None else ranked
        read = Grade(None, None, None, None, rank, width)
        try:
            read = read._replace(fitness=self.fitness(facts, read))
            # In this order: the composite reads the correctness it is composed OF.
            objective = read.fitness if self.objective is None else self.objective(facts, read)
        except ScoringTermMissingError as exc:
            return GradedCell.of(facts, Grade(None, None, str(exc), None, None, None))
        span = None if self.span is None else self.span(facts.predicted)
        return GradedCell.of(facts, read._replace(objective=objective, span=span))

    def sheet(self, facts: Iterable[MeasuredCell]) -> CellSheet:
        return CellSheet(self.id, tuple(self.grade(cell) for cell in facts))


CellGrade = Literal["fitness", "objective"]


DEFAULT_SCORER_ID = "default_hit"

# Every pass size — a definition's, a derived default's, a request's — is bounded here:
# docs/decisions.md § 0010.
MAX_PASS_ROWS: Annotated[int, shapes_optimizer_prompt] = 150

HIT_THRESHOLD: Annotated[float, shapes_optimizer_prompt] = 1.0


@shapes_optimizer_prompt
def extract_item_label(c: Any) -> str:
    if isinstance(c, dict):
        return str(c.get("candidate", c))
    return c[0] if isinstance(c, (list, tuple)) else str(c)


@shapes_optimizer_prompt
def find_rank(items: Sequence[Any], ground_truth: str) -> int | None:
    if not items or not ground_truth:
        return None
    for i, c in enumerate(items):
        if extract_item_label(c) == ground_truth:
            return i + 1
    return None


@shapes_optimizer_prompt
def is_hit(fitness: float | None) -> bool:
    """Display and stratification ONLY, never a rate: graded formulas never reach the ceiling."""
    return fitness is not None and fitness >= HIT_THRESHOLD


FITNESS_BANDS = 10


def fitness_bands(cells: Iterable[GradedCell]) -> list[int]:
    """Scored rows per tenth of [0, 1], each band closed above and a zero in the first."""
    bands = [0] * FITNESS_BANDS
    for cell in cells:
        fitness = cell.grade.fitness
        if not cell.scored or fitness is None:
            continue
        band = math.ceil(round(fitness * FITNESS_BANDS, 9)) - 1
        bands[min(FITNESS_BANDS - 1, max(0, band))] += 1
    return bands


class GradeColumn(NamedTuple):
    """One number a graded row answers with: whether it is two-valued, and the unit it is in."""

    # DECLARED, never read off the values: a formula grading every cell 0 or 1 may grade the next 0.5.
    binary: bool
    unit: MeasurandUnit
    read: Callable[[GradedCell], float]


class UngradedCellError(ValueError):
    """A column read off a cell that carries no grade: a zero in its place is a miss nobody measured."""


@shapes_optimizer_prompt
def _stamped(cell: GradedCell, grade: CellGrade) -> float:
    value: float | None = getattr(cell.grade, grade)
    if not cell.scored or value is None:
        raise UngradedCellError(
            f"sample {cell.key} carries no {grade}: an ungraded cell answers no column, "
            "so ask `scored` before reading one"
        )
    return value


ROW_GRADES: Annotated[dict[str, GradeColumn], shapes_optimizer_prompt] = {
    "fitness": GradeColumn(False, MeasurandUnit.RATE, lambda cell: _stamped(cell, "fitness")),
    "objective": GradeColumn(False, MeasurandUnit.SCORE, lambda cell: _stamped(cell, "objective")),
    # The one column every scorer's rows carry two-valued, by construction of `is_hit`.
    "hit": GradeColumn(
        True, MeasurandUnit.RATE, lambda cell: float(is_hit(_stamped(cell, "fitness")))
    ),
}

assert set(ROW_GRADES) - {"hit"} == set(get_args(CellGrade))


class ScoringSpec(NamedTuple):
    """``per_cell`` is evaluated on ONE cell: a round mean hides a slow cell behind a fast one."""

    per_sample: str | None
    per_cell: str | None
    scorer_id: str


DialKind = Literal["anchored", "unit"]

CRITERION_BASE = "fitness"


class Dial(NamedTuple):
    """At ``anchor`` or below a cell keeps its score, at twice it loses half of ``weight``."""

    weight: float
    anchor: float | None = None
    rewards: bool = False


def _dial_factor(name: str, dial: Dial) -> str:
    keep = round(1.0 - dial.weight, 9)
    if dial.anchor is None:
        if dial.rewards:
            return f"({keep} + {dial.weight} * {name})"
        return f"(1.0 - {dial.weight} * {name})"
    if dial.anchor <= 0.0:
        raise ValueError(
            f"the dial on {name!r} has anchor {dial.anchor}: a cost nobody measured above zero "
            "cannot be weighed against."
        )
    return f"({keep} + {dial.weight} * {dial.anchor} / max({dial.anchor}, {name}))"


def anchored_criterion(dials: Mapping[str, Dial]) -> str:
    factors = [_dial_factor(name, dial) for name, dial in dials.items() if dial.weight]
    return " * ".join([CRITERION_BASE, *factors])


def _number(node: ast.expr) -> float | None:
    if not isinstance(node, ast.Constant) or isinstance(node.value, bool):
        return None
    return float(node.value) if isinstance(node.value, int | float) else None


def _product(node: ast.expr) -> list[ast.expr]:
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Mult):
        return [*_product(node.left), node.right]
    return [node]


def _weighted_term(node: ast.expr) -> tuple[str, float, float | None] | None:
    """``w * name`` as ``(name, w, None)``, or ``w * a / max(a, name)`` as ``(name, w, a)``."""
    if not isinstance(node, ast.BinOp):
        return None
    if isinstance(node.op, ast.Mult) and isinstance(node.right, ast.Name):
        weight = _number(node.left)
        return None if weight is None else (node.right.id, weight, None)
    if not isinstance(node.op, ast.Div):
        return None
    scaled, floor = node.left, node.right
    if not (isinstance(scaled, ast.BinOp) and isinstance(scaled.op, ast.Mult)):
        return None
    weight, anchor = _number(scaled.left), _number(scaled.right)
    if (
        weight is None
        or anchor is None
        or not isinstance(floor, ast.Call)
        or not isinstance(floor.func, ast.Name)
        or floor.func.id != "max"
        or floor.keywords
        or len(floor.args) != 2
        or _number(floor.args[0]) != anchor
        or not isinstance(floor.args[1], ast.Name)
    ):
        return None
    return (floor.args[1].id, weight, anchor)


def _dial_of(node: ast.expr) -> tuple[str, Dial] | None:
    if not isinstance(node, ast.BinOp):
        return None
    term = _weighted_term(node.right)
    keep = _number(node.left)
    if term is None or keep is None:
        return None
    name, weight, anchor = term
    if isinstance(node.op, ast.Sub) and keep == 1.0 and anchor is None:
        return (name, Dial(weight))
    # A factor that does not reach 1.0 at its best is not a discount, and no dial describes it.
    if isinstance(node.op, ast.Add) and abs(keep + weight - 1.0) < 1e-9:
        return (name, Dial(weight, anchor, rewards=anchor is None))
    return None


def anchored_criterion_dials(formula: str) -> dict[str, Dial] | None:
    """``None`` where *formula* is not an anchored criterion, or names a term twice."""
    try:
        tree = ast.parse(formula, "<scoring formula>", "eval")
    except SyntaxError:
        return None
    base, *factors = _product(tree.body)
    if not isinstance(base, ast.Name) or base.id != CRITERION_BASE:
        return None
    dials: dict[str, Dial] = {}
    for factor in factors:
        dial = _dial_of(factor)
        if dial is None or dial[0] in dials:
            return None
        dials[dial[0]] = dial[1]
    return dials


@shapes_optimizer_prompt
def is_verifier_graded(ground_truth: str | None) -> bool:
    """Ask this, never ``predicted == NO_RESULT``: ``node_role`` declarations decide that sentinel."""
    return not (ground_truth or "")


VERIFIER_GRADED_TEXT = "verifier-graded — no label"


def ground_truth_text(ground_truth: str | None) -> str:
    if ground_truth is None or is_verifier_graded(ground_truth):
        return VERIFIER_GRADED_TEXT
    return ground_truth


@shapes_optimizer_prompt
def all_verifier_graded(labels: Iterable[str | None]) -> bool:
    """Empty is False: "measured nothing yet" is not "this backend has no labels"."""
    seen = False
    for label in labels:
        if not is_verifier_graded(label):
            return False
        seen = True
    return seen


@shapes_optimizer_prompt
def enumerable_truth_labels(cells: Sequence[GradedCell]) -> Counter[str] | None:
    """``None`` where collapse is not a meaningful question: an open space, or one truth per row."""
    truth = Counter(c.facts.ground_truth for c in cells if c.facts.ground_truth)
    if not truth or len(truth) > ANSWER_SPACE_CAP or len(truth) == len(cells):
        return None
    return truth


@shapes_optimizer_prompt
def modal_answer_share(cells: Sequence[GradedCell]) -> float | None:
    """Over PREDICTIONS; reports and never gates — below 1.0 it measures hedging."""
    if enumerable_truth_labels(cells) is None:
        return None
    said = Counter(c.facts.predicted for c in cells if c.facts.predicted)
    total = sum(said.values())
    if total == 0:
        return None
    return said.most_common(1)[0][1] / total


def is_answer_collapsed(cells: Sequence[GradedCell]) -> bool:
    """The ABSENCE of a measurement, not a low score: θ fitted to a constant answer is an artifact."""
    answered = [c for c in cells if not c.facts.errored]
    said = {c.facts.predicted for c in answered}
    if len(said) != 1 or said & {"", NO_RESULT}:
        return False
    truth = enumerable_truth_labels(answered)
    if truth is not None:
        return len(truth) >= 2
    return all_verifier_graded(c.facts.ground_truth for c in answered) and not all(
        c.hit for c in answered
    )
