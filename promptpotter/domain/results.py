from __future__ import annotations

import math
from collections.abc import Callable, Collection, Iterable, Mapping, Sequence
from dataclasses import dataclass
from enum import StrEnum
from typing import (
    TYPE_CHECKING,
    Annotated,
    Any,
    Literal,
    NamedTuple,
    NewType,
    Self,
    TypedDict,
    get_args,
)

from pydantic import ConfigDict, Field, computed_field, model_validator

from promptpotter.domain.bench import BandedValue, BenchReading, BenchScore, OwnLevel
from promptpotter.domain.opt_search_point import OptSearchPoint
from promptpotter.domain.optimizer_state import OptimizerState
from promptpotter.domain.paired_reading import (
    READING_STATE_INFO,
    ArmKey,
    ArmName,
    MeasurandUnit,
    PairedReading,
    ReadingState,
    ReadingStateKind,
)
from promptpotter.domain.phases import ErrorRecord, StopReason
from promptpotter.domain.round_diagnostics import RoundDiagnostics
from promptpotter.domain.ruler import LevelSeries, RulerStamp, ThetaCaveat, theta_band
from promptpotter.domain.scoring import (
    NO_CELLS,
    CellSheet,
    GradedCell,
    WalkedCell,
    fitness_bands,
    is_answer_collapsed,
)
from promptpotter.domain.search_point import JobSearchPoint, TaskDecomposition
from promptpotter.domain.spend import PrefixReading, SpendRollup, TokenAccount
from promptpotter.domain.strict_model import FrozenModel, StrictModel
from promptpotter.domain.wounds import (
    RuntimeFailure,
    ValidationFailure,
    fatal_validation_failures,
)
from promptpotter.shared.errors import ConflictError
from promptpotter.shared.hashing import shapes_optimizer_prompt, stable_hash
from promptpotter.shared.measurement_context import SCOPE_ROLES, MeasurementRole, RoleScope

if TYPE_CHECKING:
    from promptpotter.domain.pipeline_schema import PipelineSchema
    from promptpotter.domain.sample import Sample


class DegradationContext(TypedDict, total=False):
    """The bench's reading of a ``BROKEN`` arm, empty on every other."""

    degraded_rate: float
    degraded_count: int
    total_scored: int
    dominant_warning: str
    fatal: bool
    warning_types: dict[str, int]
    source: str


def degradation_reading(
    *,
    source: str,
    degraded_count: int,
    total_scored: int,
    warning_types: Mapping[str, int],
    dominant_warning: str,
    fatal: bool = False,
) -> DegradationContext:
    return {
        "degraded_rate": degraded_count / total_scored,
        "degraded_count": degraded_count,
        "total_scored": total_scored,
        "dominant_warning": dominant_warning,
        "fatal": fatal,
        "warning_types": dict(warning_types),
        "source": source,
    }


@shapes_optimizer_prompt
def candidate_label(round_num: int, idx: int) -> str:
    if round_num == 0:
        return "C0"
    return f"C{round_num}.{idx + 1}"


def closed_after_origin(rounds: Sequence[int]) -> int:
    return sum(1 for number in rounds if number > 0)


class CellFold(FrozenModel):
    # The cells carrying a verdict, a deprecated one included as a miss.
    total: int
    # ``None`` where `total` is 0: no fitness is not a 0.0.
    accuracy: float | None
    composite_fitness: float | None
    deprecated: int
    degraded: int
    # :func:`recall_at`; empty where the formula reads no ranking.
    recall_at: dict[int, float]


class ScoreSummary(CellFold):
    """An arm's fold of its cells: what it walked, and its level on them."""

    # Every cell taken, errored and deprecated ones included; ``cached`` of them replayed.
    walked: int
    cached: int
    evaluators: dict[str, float]
    # Student-t CI on the mean per-cell FITNESS, whatever the formula; ``None`` under two cells.
    mean_fitness_ci_lo: float | None
    mean_fitness_ci_hi: float | None
    # BACKEND tokens only, replayed rows excluded: judge and optimizer spend carry no arm.
    tokens: TokenAccount | None


#: Why an arm stopped short of its panel AGAINST it: refused before a cell, by the operator, by the
#: bench's run-health rules, or by the eliminator (`Eliminator.stop_disqualifies` says if that rejects).
CutKind = Literal["invalid", "skipped", "broken", "eliminated"]
#: How an arm's walk ended short of its panel: cut against it, or locked in for it.
ArmEnd = Literal[CutKind, "locked_in"]
#: Where an arm's walk stands: each ``ArmState`` member's discriminator.
ArmStateKind = Literal["proposed", "measuring", "cut", "measured"]
#: What an arm IS in its round: the origin, a proposal, or a reference re-read on the round's cells.
ArmRole = Literal[MeasurementRole.ORIGIN, MeasurementRole.PANEL, MeasurementRole.PARENT]


class ArmSlot(FrozenModel):
    """Where an individual was first walked as an arm: the ``idx``-th of its round's ``total``."""

    round: int
    idx: int
    total: int

    @property
    def label(self) -> str:
        return candidate_label(self.round, self.idx)


def arm_key(
    individual: OptSearchPoint, failures: Iterable[ValidationFailure], round_num: int, idx: int
) -> ArmKey:
    """A refused proposal is keyed by its SLOT: its content is often its parent's or a sibling's."""
    if fatal_validation_failures(failures):
        return ArmKey(stable_hash([round_num, idx, individual.id]))
    return ArmKey(individual.id)


class Proposal(FrozenModel):
    """What a proposer hands the round: an individual, its sparse overlay, and why it is refused."""

    model_config = ConfigDict(arbitrary_types_allowed=True)

    individual: OptSearchPoint
    # The sparse fork transport; the individual's own params are the resolved whole.
    overlay: dict[str, dict[str, Any]] = Field(default_factory=dict)
    validation_failures: list[ValidationFailure] = Field(default_factory=list)

    @property
    def refused(self) -> bool:
        return bool(fatal_validation_failures(self.validation_failures))

    def key(self, round_num: int, idx: int) -> ArmKey:
        return arm_key(self.individual, self.validation_failures, round_num, idx)


class _Arm(FrozenModel):
    model_config = ConfigDict(arbitrary_types_allowed=True)

    role: ArmRole
    # Its FIRST walk as an arm; ``None``: a reference no round of this cycle walked as an arm.
    slot: ArmSlot | None
    individual: OptSearchPoint
    overlay: dict[str, dict[str, Any]] = Field(default_factory=dict)
    validation_failures: list[ValidationFailure] = Field(default_factory=list)

    @model_validator(mode="after")
    def _only_a_parent_is_unwalked(self) -> Self:
        if self.slot is None and self.role != MeasurementRole.PARENT:
            raise ValueError(f"a {self.role} arm is walked: it holds its slot")
        return self

    @property
    def label(self) -> str:
        return "parent" if self.slot is None else self.slot.label

    @property
    def refused(self) -> bool:
        return bool(fatal_validation_failures(self.validation_failures))

    @property
    def changes_description(self) -> str:
        return self.individual.lineage.changes_description or ""


class Proposed(_Arm):
    state: Literal["proposed"] = "proposed"


class _Walked(_Arm):
    # Stamped, never re-derived: it covers each node's rendered ``prompt``.
    sp_hash: str
    # Origin floor ⊕ this individual's delta, schema-resolved: what a steered fork seeds from.
    resolved_params: dict[str, Any]
    expected_samples: int


class Measuring(_Walked):
    """What is known as the walk opens; its cells and their fold land on their own records."""

    state: Literal["measuring"] = "measuring"
    sample_order: list[int] = Field(default_factory=list)
    n_priors: int = 0
    # The turn's place in a block race — ``n`` of ``of``, ``size`` cells, ``racing`` arms live.
    block: dict[str, int] | None = None


class Elimination(FrozenModel):
    """The eliminator's reading of a stop it made, for the arm or against it."""

    reason: str
    # Opaque to the bench (potter's: `pobb/checks.py::EliminationContext`).
    context: dict[str, Any]


class Eliminated(FrozenModel):
    kind: Literal["eliminated"] = "eliminated"
    elimination: Elimination


class Broken(FrozenModel):
    """The bench's run-health rules stopped it."""

    kind: Literal["broken"] = "broken"
    failure: RuntimeFailure
    degradation: DegradationContext


class Invalid(FrozenModel):
    """Refused before a cell: the reasons are the arm's own fatal ``validation_failures``."""

    kind: Literal["invalid"] = "invalid"


class Skipped(FrozenModel):
    """The operator stopped it."""

    kind: Literal["skipped"] = "skipped"


CutReason = Annotated[Eliminated | Broken | Invalid | Skipped, Field(discriminator="kind")]


class Cut(_Walked):
    """Stopped short of its panel against it."""

    state: Literal["cut"] = "cut"
    reason: CutReason

    @property
    def kind(self) -> CutKind:
        return self.reason.kind


class Measured(_Walked):
    state: Literal["measured"] = "measured"
    # Stopped FOR it: the eliminator settled it before its panel ran out.
    locked_in: Elimination | None


Concluded = Cut | Measured
ArmState = Annotated[Proposed | Measuring | Cut | Measured, Field(discriminator="state")]

assert {r.model_fields["kind"].default for r in get_args(get_args(CutReason)[0])} == set(
    get_args(CutKind)
), "CutKind is the cut reasons' discriminators"
assert {a.model_fields["state"].default for a in get_args(get_args(ArmState)[0])} == set(
    get_args(ArmStateKind)
), "ArmStateKind is the arm states' discriminators"


def arm_end(arm: ArmState) -> ArmEnd | None:
    match arm:
        case Cut(reason=reason):
            return reason.kind
        case Measured(locked_in=Elimination()):
            return "locked_in"
        case _:
            return None


def walked_short(arm: ArmState) -> bool:
    """Stopped before its panel ran out, for it or against it; an ``invalid`` arm never started one."""
    return arm_end(arm) not in (None, "invalid")


@shapes_optimizer_prompt
def arm_elimination(arm: ArmState) -> Elimination | None:
    """The eliminator's verdict on an arm it settled, cut or locked in."""
    match arm:
        case Cut(reason=Eliminated(elimination=elimination)) | Measured(locked_in=elimination):
            return elimination
        case _:
            return None


def is_leader_eligible(arm: ArmState) -> bool:
    """Measured or eliminated: the bench's own stops and a refusal leave no reading."""
    return isinstance(arm, Measured) or (isinstance(arm, Cut) and arm.kind == "eliminated")


class BankedSearchPointError(ConflictError):
    code = "banked_searchpoint_moved"


def is_electable(arm: ArmState, rows: Sequence[GradedCell]) -> bool:
    """NOT the whole election rule: the coverage floor is `scoring/selection.py::readable_lifts`."""
    return is_leader_eligible(arm) and bool(rows) and not is_answer_collapsed(rows)


def round_document_digest(rr: RoundResult) -> str:
    """The WHOLE outcome, never a chosen subset: under-firing goes stale in silence."""
    outcome = rr.model_dump(mode="json", include=set(RoundOutcome.model_fields))
    return stable_hash([outcome, rr.addresses().model_dump(mode="json")])


def unscoreable_cells(results: Iterable[GradedCell]) -> int:
    """Never ``walked - total``, which counts a deprecated row as a hole."""
    return sum(1 for r in results if r.facts.errored)


def recall_at(results: Iterable[GradedCell]) -> dict[int, float]:
    answered = [r.grade for r in results if not r.facts.errored]
    if all(grade.n_candidates is None for grade in answered):
        return {}
    ranks = [grade.ground_truth_rank for grade in answered]
    return {
        k: sum(1 for rank in ranks if rank is not None and rank <= k) / len(ranks) for k in (1, 5)
    }


@shapes_optimizer_prompt
def merge_known_outcomes(
    prior: Iterable[GradedCell], incoming: Iterable[GradedCell]
) -> list[GradedCell]:
    """Never score this pool: its rows were measured by DIFFERENT configurations."""
    by_key = {cell.key: cell for cell in prior}
    for cell in incoming:
        by_key[cell.key] = cell
    return list(by_key.values())


@shapes_optimizer_prompt
def order_floor(score: float | None) -> float:
    return score if score is not None else -math.inf


ScoreboardRankKey = tuple[bool, bool, float, float, float]


def scoreboard_rank_key(
    composite_fitness: float | None,
    accuracy: float | None,
    theta: float | None = None,
    *,
    is_leading: bool = False,
    is_partial: bool = False,
) -> ScoreboardRankKey:
    """A mask lens (``mask/divergence.py``) passes no `is_leading`: pinned to rank 1 it cannot disagree."""
    return (
        is_leading,
        # The round order is stratified, so a walk cut short kept a biased slice, not a smaller sample.
        not is_partial,
        order_floor(theta),
        order_floor(composite_fitness),
        order_floor(accuracy),
    )


class ScoreboardRow(FrozenModel):
    """One rank-ordered row of a round's scoreboard: an arm's reading at its rank."""

    rank: int
    reading: ArmReading


class StallEffect(StrEnum):
    STARTS = "starts"
    RESETS = "resets"
    COUNTS = "counts"
    SKIPS = "skips"


class RoundAdvance(StrEnum):
    """Whether a closed round ADVANCED the best-so-far line."""

    ORIGIN = "origin"
    ADVANCED = "advanced"
    ADVANCED_UNPAIRED = "advanced_unpaired"
    NOT_SEPARATED = "not_separated"
    HELD = "held"
    UNREAD = "unread"


class RoundAdvanceInfo(NamedTuple):
    label: str
    sentence: str
    stall: StallEffect
    promoted: bool


ROUND_ADVANCE_INFO: Annotated[dict[RoundAdvance, RoundAdvanceInfo], shapes_optimizer_prompt] = {
    RoundAdvance.ORIGIN: RoundAdvanceInfo(
        "origin",
        "Round 0 measured the origin every later pick is read against.",
        StallEffect.STARTS,
        False,
    ),
    RoundAdvance.ADVANCED: RoundAdvanceInfo(
        "advanced",
        "The pick's lift over the origin clears zero on the origin panel, and its rate there "
        "tops every earlier pick's.",
        StallEffect.RESETS,
        True,
    ),
    RoundAdvance.ADVANCED_UNPAIRED: RoundAdvanceInfo(
        "advanced, unpaired",
        "An arm was promoted, and the origin panel held nothing to pair it with the origin on.",
        StallEffect.RESETS,
        True,
    ),
    RoundAdvance.NOT_SEPARATED: RoundAdvanceInfo(
        "not separated",
        "An arm was promoted, and on the origin panel it has not separated from the origin and "
        "every earlier pick.",
        StallEffect.COUNTS,
        True,
    ),
    RoundAdvance.HELD: RoundAdvanceInfo("held", "No arm was promoted.", StallEffect.COUNTS, False),
    RoundAdvance.UNREAD: RoundAdvanceInfo(
        "unread",
        "An arm was promoted, and the origin-panel reading owed on it was not taken: the round "
        "neither advances the line nor counts as a stall.",
        StallEffect.SKIPS,
        True,
    ),
}
assert ROUND_ADVANCE_INFO.keys() == set(RoundAdvance)


@shapes_optimizer_prompt
def round_advance(
    round_num: int, improved: bool, lead: PairedReading, earlier: Sequence[PairedReading]
) -> RoundAdvance:
    """*improved* is a point-estimate promotion; only *lead* (pick over C0, origin panel) moves the line."""
    if round_num == 0:
        return RoundAdvance.ORIGIN
    if not improved:
        return RoundAdvance.HELD
    match READING_STATE_INFO[lead.state].kind:
        case ReadingStateKind.UNPAIRED:
            return RoundAdvance.ADVANCED_UNPAIRED
        case ReadingStateKind.WAITING | ReadingStateKind.ABSENT | ReadingStateKind.REFUSED:
            return RoundAdvance.UNREAD
        case ReadingStateKind.READ:
            lift = lead.headline
            assert lift is not None, "a read pair carries its lift"
            # A pair that lost a cell is a rate over a different exam: compare whole-panel rates only.
            whole = lead.on_whole_set
            tops = all(
                whole is not None
                and (rival := pick.on_whole_set) is not None
                and whole.rate_b > rival.rate_b
                for pick in earlier
                if pick.headline is not None
            )
            separated = lift.estimate.side == "above" and tops
            return RoundAdvance.ADVANCED if separated else RoundAdvance.NOT_SEPARATED


@shapes_optimizer_prompt
def rounds_without_advance(rounds: Sequence[RoundOutcome]) -> int:
    depth = 0
    for rr in reversed(rounds):
        match ROUND_ADVANCE_INFO[rr.overlap.advance].stall:
            case StallEffect.STARTS | StallEffect.RESETS:
                break
            case StallEffect.COUNTS:
                depth += 1
            case StallEffect.SKIPS:
                pass
    return depth


def stalls_left(rounds: Sequence[RoundOutcome], lives: tuple[int, int]) -> int:
    """A round no arm reached the election of banks nothing: evidence about the proposer, not the search."""
    start, cap = lives
    bank = max(0, min(cap, start))
    for rr in rounds:
        if rr.round == 0 or rr.election is None or rr.election.electable == 0:
            continue
        promoted = ROUND_ADVANCE_INFO[rr.overlap.advance].promoted
        bank = max(0, min(cap, bank + (1 if promoted else -1)))
    return bank


def lives_bar(left: int, cap: int | None) -> str:
    if left <= 0:
        return "💀"
    if cap is None or cap < left:
        return "♥" * left
    return "♥" * left + "♡" * (cap - left)


def lives_label(left: int, cap: int | None) -> str:
    if left <= 0:
        return "No lives left — the run stops after this round"
    noun = "life" if left == 1 else "lives"
    if cap is None or cap < left:
        return f"{left} {noun} left"
    return f"{left} of {cap} {noun} left"


class LivesReading(FrozenModel):
    """A run's life bank as every surface shows it. Built by :meth:`of` alone."""

    bar: str = Field(description="One ♥ per banked life, one ♡ per spent one; 💀 on an empty bank.")
    label: str
    spent: bool = Field(description="The bank is empty: the run stops after this round.")

    @classmethod
    def of(cls, left: int, cap: int | None) -> LivesReading:
        return cls(bar=lives_bar(left, cap), label=lives_label(left, cap), spent=left <= 0)


class PanelTrigger(StrEnum):
    """Why an arm off the line is read on the origin panel: a monitoring call, never a verdict."""

    N_MIN = "n_min"


class TriggeredReading(FrozenModel):
    """Fired once, re-read every round the panel reads: a later cut or lost election takes none back."""

    trigger: PanelTrigger
    reading: PairedReading = Field(description="The arm (`b`) over C0 (`a`) on the origin panel.")


class OverlapReading(FrozenModel):
    """The best-so-far line on the ORIGIN PANEL: each pick paired with C0 on one set of cells."""

    # Elections pool these cells and pick on them; the unbiased headline is the bench.
    # :func:`origin_panel`, by sample slot, ascending; empty where no pass was sent.
    positions: tuple[int, ...]
    lead: PairedReading = Field(
        description="The standing pick (`b`) over C0 (`a`) on the origin panel, in report scope. "
        "`bought` on each member is what this round paid to put it back on the whole panel."
    )
    earlier: tuple[PairedReading, ...] = Field(
        description="Each earlier pick on the line over C0 on the same panel, oldest first."
    )
    triggered: tuple[TriggeredReading, ...] = Field(
        description="Every arm off the line a `PanelTrigger` fired for this cycle, by first walk. "
        "It moves no `advance`."
    )
    vs_parent: PairedReading | None = Field(
        description="The standing pick (`b`) over the pick before it on the line (`a`), on the "
        "same panel: the cells it gained and lost against its own parent. Null where that parent "
        "is C0, which `lead` reads, or where the line went unread. It moves no `advance`."
    )
    advance: RoundAdvance

    @classmethod
    def of(
        cls,
        round_num: int,
        improved: bool,
        *,
        positions: Sequence[int],
        lead: PairedReading,
        earlier: Sequence[PairedReading],
        triggered: Sequence[TriggeredReading],
        vs_parent: PairedReading | None,
    ) -> OverlapReading:
        return cls(
            positions=tuple(positions),
            lead=lead,
            earlier=tuple(earlier),
            triggered=tuple(triggered),
            vs_parent=vs_parent,
            advance=round_advance(round_num, improved, lead, earlier),
        )

    @classmethod
    def unpaired(cls, state: ReadingState, round_num: int, improved: bool) -> OverlapReading:
        return cls.of(
            round_num,
            improved,
            positions=(),
            lead=PairedReading.unread(state),
            earlier=(),
            triggered=(),
            vs_parent=None,
        )


class LineRate(FrozenModel):
    """One member of the best-so-far line as an overlap reading holds it."""

    arm: ArmName
    rate: float = Field(description="Its rate over the panel cells it and C0 both scored.")
    n: int = Field(description="How many of the panel's cells it holds scoreable.")


@shapes_optimizer_prompt
def overlap_line(overlap: OverlapReading) -> list[LineRate]:
    """C0 is on the lead's reading of it, before any pick on the first triggered one's: rates set
    side by side must be one pair's."""
    anchor = next(
        (
            reading
            for reading in (overlap.lead, *(t.reading for t in overlap.triggered))
            if reading.headline is not None and reading.a is not None
        ),
        None,
    )
    if (
        anchor is None
        or anchor.headline is None
        or anchor.a is None
        or anchor.a.address.arm is None
    ):
        return []
    line = [LineRate(arm=anchor.a.address.arm, rate=anchor.headline.rate_a, n=anchor.a.n)]
    for pick in (*overlap.earlier, overlap.lead):
        if pick.headline is not None and pick.b is not None and pick.b.address.arm is not None:
            line.append(LineRate(arm=pick.b.address.arm, rate=pick.headline.rate_b, n=pick.b.n))
    return line


@shapes_optimizer_prompt
def triggered_rates(overlap: OverlapReading) -> list[LineRate]:
    return [
        LineRate(arm=arm, rate=headline.rate_b, n=b.n)
        for t in overlap.triggered
        if (headline := t.reading.headline) is not None
        and (b := t.reading.b) is not None
        and (arm := b.address.arm) is not None
    ]


class LineStep(NamedTuple):
    """``opt_sp`` re-measures the member: another arm's prompt would read under this one's label."""

    arm: ArmName
    opt_sp: OptSearchPoint


@shapes_optimizer_prompt
def measured_cells(rows: Iterable[GradedCell]) -> set[str]:
    return {cell.key for cell in rows if cell.scored}


def election_rows(drawn: CellSheet, on_panel: CellSheet, crowned: Collection[str]) -> CellSheet:
    """What a round elects on: an individual's draw, then the origin-panel cells it lacks, less
    every cell that crowned the parent."""
    skip = {cell.key for cell in drawn} | set(crowned)
    return drawn.merged(on_panel.where(lambda cell: cell.key not in skip))


def is_floor_pinned(rows: Iterable[GradedCell]) -> bool:
    """``fitness``, never ``objective``: a ``per_cell`` composite charges a miss, so all-miss is not 0."""
    graded = [
        fitness for cell in rows if cell.scored and (fitness := cell.grade.fitness) is not None
    ]
    return bool(graded) and all(fitness <= 0.0 for fitness in graded)


def best_line(rounds: Sequence[RoundResult]) -> list[LineStep]:
    """The declared picks, never a high-water of each round's composite: those sat different rows."""
    individuals = {
        head.individual.id: head.individual for rr in rounds if (head := rr.head) is not None
    }
    return [LineStep(arm=arm, opt_sp=individuals[arm.key]) for arm in pick_line(rounds)]


def pick_line(rounds: Sequence[RoundOutcome]) -> list[ArmName]:
    picks: dict[ArmKey, ArmName] = {}
    for rr in rounds:
        if rr.election is not None and (key := rr.election.selected) is not None:
            picks.setdefault(key, arm_name(key, rr.arms[key], rr.round))
    return list(picks.values())


def origin_panel(
    origin_cells: Collection[int],
    *,
    line_cells: Collection[int],
    poolable: Collection[int],
    scope_of: Mapping[int, str],
    size: int,
) -> list[int]:
    """*origin_cells* are the origin's own ROUND's, never every cell it was later read on. A scope
    the line drew and the origin never sat takes the line's cells there, and the top-up seats C0 on
    them as on any member's gap; each scope is cut to *size* alone, so none crowds another out."""
    by_scope: dict[str, list[int]] = {}
    for sid in sorted(set(origin_cells) & set(poolable)):
        by_scope.setdefault(scope_of[sid], []).append(sid)
    sat = set(by_scope)
    for sid in sorted(set(line_cells) & set(poolable)):
        if scope_of[sid] not in sat:
            by_scope.setdefault(scope_of[sid], []).append(sid)
    return sorted(sid for cells in by_scope.values() for sid in cells[:size])


CEILING_FRACTION = 0.95


class RoundClocks(NamedTuple):
    """``rounds_to_improved`` adopts on a bare point estimate; quote ``rounds_to_separable``."""

    rounds_to_separable: int | None
    rounds_to_improved: int | None
    rounds_to_ceiling: int | None
    accuracy_ceiling: float | None


def round_clocks(rounds: Sequence[RoundResult], *, accuracy_ceiling: float | None) -> RoundClocks:

    def first(holds: Callable[[RoundResult], bool]) -> int | None:
        return next((r.round for r in rounds if holds(r)), None)

    to_ceiling: int | None = None
    if accuracy_ceiling is not None:
        target = CEILING_FRACTION * accuracy_ceiling
        to_ceiling = first(
            lambda r: (
                (fold := r.fold) is not None
                and fold.accuracy is not None
                and fold.accuracy >= target
            )
        )
    return RoundClocks(
        rounds_to_separable=first(lambda r: r.overlap.advance is RoundAdvance.ADVANCED),
        rounds_to_improved=first(lambda r: r.improved),
        rounds_to_ceiling=to_ceiling,
        accuracy_ceiling=accuracy_ceiling,
    )


HealthGrade = Literal["healthy", "degraded", "critical"]

HealthCause = Literal[
    "origin_unmeasured",
    # The origin measured SOME cells: `origin_unmeasured` is none, `holed` is any round's rate.
    "origin_incomplete",
    "structural",
    "unscoreable",
    "holed",
    "evidence_starved",
    "structural_untested",
    "persistent",
    "degraded",
]


class DegradationHealth(FrozenModel):
    """A round's degradation verdict from the backend's warning stamps; it never stops the run."""

    grade: HealthGrade
    # `None` is the `healthy` grade.
    cause: HealthCause | None = None
    samples: int
    structural_count: int
    transient_count: int
    # Attempted cells that reported nothing (re-run).
    hole_count: int = 0
    # Panel cells never SENT: no row, and outside ``samples``.
    not_attempted: int = 0
    # Sent and measured, no verdict (the formula named a term the row lacks): in ``samples``, no rate.
    unscored: int = 0
    # Answers cut at the output-token ceiling, scored as written. REPORTED, never graded.
    output_ceiling_count: int = 0
    # ``None`` where the answer space makes collapse meaningless. REPORTED, never graded.
    answer_modal_share: float | None = None
    degraded_rate: float
    consecutive_degraded_rounds: int
    prior_clean_rounds: int
    dominant_node: str | None = None
    node_failure_rates: dict[str, float] = Field(default_factory=dict)
    node_warnings: dict[str, list[str]] = Field(default_factory=dict)
    suggested_action: str | None = None
    last_error: str | None = None


class OptimizerFact(FrozenModel):
    """One labelled reading an optimizer reports about a round, worded by its own runtime."""

    key: str
    label: str
    text: str
    value: float | None
    # A `stat` reads on one line; a `note` is prose the optimizer carries into its next round.
    kind: Literal["stat", "note"]


class RoundCells(FrozenModel):
    """A :class:`RoundResult`'s rows as archive addresses, in walk order."""

    arms: dict[ArmKey, list[WalkedCell]] = Field(default_factory=dict)
    overlap: dict[str, list[WalkedCell]] = Field(default_factory=dict)

    def walks(self) -> list[IndividualWalk[WalkedCell]]:
        return _round_walks(arms=self.arms, overlap=self.overlap)

    def sample_keys(self) -> set[str]:
        walked = [*self.arms.values(), *self.overlap.values()]
        return {key for cells in walked for key, _, _ in cells}


class IndividualWalk[C: tuple[Any, ...]](NamedTuple):
    """A cell is any tuple LEADING with its sample's content key."""

    individual_id: str
    roles: frozenset[MeasurementRole]
    cells: Sequence[C]


# The WALK's role, never the answer's `role` stamp: that names who FILED it, and a replay reads it.
_ROW_SET_ROLES: dict[str, frozenset[MeasurementRole]] = {
    "arms": SCOPE_ROLES[RoleScope.DECISION],
    "overlap": frozenset({MeasurementRole.OVERLAP}),
}
assert _ROW_SET_ROLES.keys() == RoundCells.model_fields.keys()


def _round_walks[C: tuple[Any, ...]](
    **by_individual: Mapping[ArmKey, Sequence[C]] | Mapping[str, Sequence[C]],
) -> list[IndividualWalk[C]]:
    """An arm's cells are its individual's: a refused arm walked none, so its slot key holds none."""
    return [
        IndividualWalk(individual_id, _ROW_SET_ROLES[row_set], cells)
        for row_set in ("arms", "overlap")
        for individual_id, cells in by_individual[row_set].items()
    ]


def individual_cells[C: tuple[Any, ...]](
    walks: Iterable[IndividualWalk[C]], individual_id: str, scope: RoleScope
) -> list[C]:
    """Nothing is folded: the reader grades these and takes ``CellSheet.standing``."""
    visible = SCOPE_ROLES[scope]
    return [
        cell
        for walk in walks
        if walk.individual_id == individual_id and walk.roles <= visible
        for cell in walk.cells
    ]


DisplayMetric = Literal["accuracy", "composite", "ability"]
# A displayed level's unit: a measurand's, or θ — a fit over cells, never read off one.
LevelUnit = Literal["rate", "score", "theta"]
if not set(MeasurandUnit) < set(get_args(LevelUnit)):
    raise RuntimeError("LevelUnit must hold every MeasurandUnit (domain/results.py)")


class DisplayMetricInfo(NamedTuple):
    label: str
    glyph: str
    title: str
    unit: LevelUnit


#: IN PICK ORDER — the tiebreak where the elected column is not among those shown.
DISPLAY_METRIC_INFO: dict[DisplayMetric, DisplayMetricInfo] = {
    "accuracy": DisplayMetricInfo(
        "accuracy",
        "%",
        "Raw accuracy — correctness rate over the candidate's measured subset (subset-relative).",
        "rate",
    ),
    "ability": DisplayMetricInfo(
        "ability θ",
        "θ",
        "Difficulty-adjusted ability θ — what a selector declaring ability (potter's) elects on. "
        "A logit (not a %): comparable within a round; cross-round comparison waits on the stable "
        "δ bank.",
        "theta",
    ),
    "composite": DisplayMetricInfo(
        "composite",
        "∑",
        "Composite fitness under the active scoring formula (equals accuracy when no formula is "
        "set).",
        "score",
    ),
}
if set(DISPLAY_METRIC_INFO) != set(get_args(DisplayMetric)):
    raise RuntimeError("DISPLAY_METRIC_INFO is out of step with DisplayMetric (domain/results.py)")


class ArmFit(FrozenModel):
    """What the election read of one arm: θ lives here and nowhere else."""

    # Rasch ability on the round's joint-fit scale; `None` outside the election fit.
    theta: float | None = None
    theta_se: float | None = None
    # This ARM's own reason θ is not ability (`bench/difficulty.py::AbilityScale.caveat`).
    theta_caveat: ThetaCaveat | None = None
    # This arm over the individual it was judged against; `None` on an arm with no reference.
    vs_reference: PairedReading | None = None


class Election(FrozenModel):
    """What a round's selector decided over its arms, by key; ``selected`` null = the round HELD."""

    # THE pick: round 0 selects the origin it adopted.
    selected: ArmKey | None
    leading: ArmKey | None = Field(
        description="The ONE arm this round is read off, as its selector named it and never "
        "re-ranked: the selection where there is one, on a held round the challenger that came "
        "closest on the selector's own objective, at round 0 the origin. Rank 1 of `scoreboard`. "
        "`null` where the selector could read no arm. NOT whose numbers the headline carries: "
        "that is `ended_on`."
    )
    ended_on: ArmKey = Field(
        description="WHOSE numbers the round's headline carries: the selection where the round "
        "selected; on a held round the retained parent, re-read on this round's cells as its "
        "reference arm — never the leading challenger."
    )
    # Arms that entered the election. Zero says the proposer made no testable variant, not a stall.
    electable: int
    fit: dict[ArmKey, ArmFit] = Field(default_factory=dict)
    # `Selector.elects_on`: the column of `scoreboard` the round was WON on.
    elects_on: DisplayMetric
    # The selector's own reason; ``None`` on the origin round, which chose between nobody.
    verdict_reason: str | None = None
    # The δ scale the election read, whether or not a θ was fit on it.
    ruler: RulerStamp


class RoundOutcome(FrozenModel):
    """``RoundClosedRecord`` carries exactly this: a field declared here reaches the ledger."""

    model_config = ConfigDict(arbitrary_types_allowed=True)

    round: int
    # Its panel arms in slot order, then each reference the round re-read: one key, one state.
    arms: dict[ArmKey, ArmState] = Field(default_factory=dict)
    # Each arm's fold of the cells the round files for it, taken once as the round is measured
    # (`MeasuredRound.folds`); none for an arm refused before a cell.
    folds: dict[ArmKey, ScoreSummary]
    # ``None``: a diag round, its variants generated and never scored.
    election: Election | None
    # Not `improved`, the selector's point estimate. `pending` until the overlap pass lands.
    overlap: OverlapReading
    diagnostics: RoundDiagnostics | None = None
    # Graded against the rounds before it, so it is stamped, never re-derived from this one.
    health: DegradationHealth | None = None
    # Built once, at the round's close (`Cycle.close_state`); `None` from its election until then.
    optimizer_state: OptimizerState | None = None
    optimizer_facts: list[OptimizerFact] = Field(default_factory=list)

    @property
    def closed_state(self) -> OptimizerState:
        if self.optimizer_state is None:
            raise ValueError(
                f"round {self.round} holds no optimizer state: it has not closed, or it is a "
                "member's view, whose parts open through `NodeContext.recall`"
            )
        return self.optimizer_state

    @property
    def generation_only(self) -> bool:
        return self.election is None

    @property
    def head(self) -> Concluded | None:
        """The arm the round ENDED on: the headline is its numbers."""
        if self.election is None:
            return None
        arm = self.arms[self.election.ended_on]
        assert isinstance(arm, Concluded), "a round ends on an arm it concluded"
        return arm

    @property
    def fold(self) -> ScoreSummary | None:
        """The headline: the fold of the arm the round ENDED on, never a field of its own."""
        return None if self.election is None else self.folds.get(self.election.ended_on)

    @property
    def improved(self) -> bool:
        """A promotion, on a bare point estimate; round 0 adopts its origin and promotes nothing."""
        return self.round > 0 and self.election is not None and self.election.selected is not None

    @property
    def ruler(self) -> RulerStamp | None:
        return None if self.election is None else self.election.ruler

    @property
    def panel(self) -> dict[ArmKey, ArmState]:
        """The arms the round PROPOSED, in slot order: no reference it re-read."""
        return {key: arm for key, arm in self.arms.items() if arm.role != MeasurementRole.PARENT}

    @property
    def candidates_scored(self) -> int:
        return sum(1 for arm in self.panel.values() if is_leader_eligible(arm))

    @property
    def origin(self) -> Concluded:
        head = self.head
        assert self.round == 0 and head is not None, "round 0 ends on the origin"
        return head

    def fit(self, key: ArmKey) -> ArmFit | None:
        return None if self.election is None else self.election.fit.get(key)

    def arm_election(self, key: ArmKey, *, held: bool = True) -> ArmElection:
        election = self.election
        return ArmElection.of(
            key,
            held=held and election is not None,
            selected=None if election is None else election.selected,
            leading=None if election is None else election.leading,
            electable=None if election is None else election.electable,
        )

    def arm_readings(self) -> list[ArmReading]:
        counts = {key: panel_counts(arm, self.folds.get(key)) for key, arm in self.arms.items()}
        panel = list(self.panel)
        cuts = dict(zip(panel, panel_cuts([counts[key] for key in panel]), strict=True))
        # A reference reads the cells its readers asked, so no panel arm's fuller walk cuts it.
        cuts |= {key: panel_cuts([counts[key]])[0] for key in self.arms if key not in cuts}
        line = line_by_individual(self.overlap)
        return [
            ArmReading.of(
                arm_name(key, arm, self.round),
                arm,
                fold=self.folds.get(key),
                cut=cuts[key],
                election=self.arm_election(key),
                fit=self.fit(key),
            ).on_line(line)
            for key, arm in self.arms.items()
        ]

    def ranked(self) -> list[ScoreboardRow]:
        """THE rank: the leading arm first, then θ where the round was won on it, then composite."""
        leading = None if self.election is None else self.election.leading
        on_theta = self.election is not None and self.election.elects_on == "ability"

        def rank(reading: ArmReading) -> ScoreboardRankKey:
            own = reading.own
            ability = reading.ability
            return scoreboard_rank_key(
                None if own is None or own.composite is None else own.composite.value,
                None if own is None or own.accuracy is None else own.accuracy.value,
                ability.theta if on_theta and ability is not None else None,
                is_leading=reading.arm.key == leading,
                is_partial=reading.end == "skipped",
            )

        ranked = sorted(self.arm_readings(), key=rank, reverse=True)
        return [ScoreboardRow(rank=i, reading=r) for i, r in enumerate(ranked, start=1)]


def arm_name(key: ArmKey, arm: ArmState, round_num: int) -> ArmName:
    """An arm by its FIRST walk; a parent no round of the cycle walked is the one *round_num* read."""
    if arm.slot is None:
        return ArmName(key=key, round=round_num, label=arm.label)
    return ArmName(key=key, round=arm.slot.round, label=arm.slot.label)


def panel_counts(arm: ArmState, fold: ScoreSummary | None) -> tuple[int | None, int | None]:
    match arm:
        case Proposed():
            return None, None
        case Measuring() | Cut() | Measured():
            return (None if fold is None else fold.walked), arm.expected_samples


class RoundResult(RoundOutcome):
    """A round with its rows, which is the round file itself."""

    # `extra="ignore"`: the computed `round_id`/`scoreboard` are dumped and must read back.
    model_config = ConfigDict(extra="ignore")

    # Offset of the statement it stands on. ``None`` where no ledger was bound: 0 is a real offset.
    at_offset: int | None = None
    # By arm: a reference re-read on the round's cells is an arm, so a held round's head is here.
    cells: dict[ArmKey, CellSheet] = Field(default_factory=dict)
    # Keyed by the MEASURED individual, outside the arms: the election pools them (`election_rows`).
    overlap_results: dict[str, CellSheet] = Field(default_factory=dict)

    @computed_field  # type: ignore[prop-decorator]
    @property
    def round_id(self) -> str:
        return f"round_{self.round}"

    @property
    def results(self) -> CellSheet:
        """The rows of the arm the round ENDED on: the headline's own."""
        return NO_CELLS if self.election is None else self.rows(self.election.ended_on)

    def rows(self, key: ArmKey) -> CellSheet:
        return self.cells.get(key, NO_CELLS)

    def reference_rows(self, key: ArmKey) -> CellSheet:
        """The rows of the arm *key* was judged against, re-read on this round's cells."""
        fit = self.fit(key)
        against = None if fit is None or fit.vs_reference is None else fit.vs_reference.a
        return NO_CELLS if against is None else self.rows(ArmKey(against.address.individual_id))

    def addresses(self) -> RoundCells:
        return RoundCells(
            arms={k: v.addresses() for k, v in self.cells.items()},
            overlap={k: v.addresses() for k, v in self.overlap_results.items()},
        )

    @computed_field  # type: ignore[prop-decorator]
    @property
    def scoreboard(self) -> list[ScoreboardRow]:
        """`ranked`, served: the ledger's `RoundOutcome` cannot carry a computed field."""
        return self.ranked()

    @computed_field(  # type: ignore[prop-decorator]
        description="The arm the round ENDED on (`election.ended_on`), read: the headline's "
        "numbers are its own. Null on a diag round, which elects nothing."
    )
    @property
    def headline(self) -> ArmReading | None:
        ended_on = None if self.election is None else self.election.ended_on
        return next((r for r in self.arm_readings() if r.arm.key == ended_on), None)

    @computed_field(  # type: ignore[prop-decorator]
        description="How many scored rows of the `headline` arm's cells graded into each tenth of "
        "[0, 1], lowest first: a band is closed above, so a 0.5 counts in the fifth and a 1.0 in "
        "the tenth. A row carrying no verdict is in none — an uncharged error's 0.0 is a floor, "
        "not a low score."
    )
    @property
    def fitness_bands(self) -> list[int]:
        return fitness_bands(self.results)

    def sheet_of(self, individual_id: str) -> CellSheet:
        return InRunCells([self]).sheet(individual_id, RoleScope.DECISION)


class InRunCells:
    def __init__(self, rounds: Sequence[RoundResult]) -> None:
        self._walks = [
            walk
            for rr in rounds
            for walk in _round_walks(
                arms={k: _by_sample(v) for k, v in rr.cells.items()},
                overlap={k: _by_sample(v) for k, v in rr.overlap_results.items()},
            )
        ]

    def sheet(self, individual_id: str, scope: RoleScope) -> CellSheet:
        folded = individual_cells(self._walks, individual_id, scope)
        if not folded:
            return NO_CELLS
        return CellSheet(folded[0][2], tuple(cell for _, cell, _ in folded)).standing()


def _by_sample(sheet: CellSheet) -> list[tuple[str, GradedCell, str]]:
    return [(cell.key, cell, sheet.scorer_id) for cell in sheet]


def declared_selection[R: RoundOutcome](rounds: Sequence[R]) -> R:
    return next(
        rr
        for rr in reversed(rounds)
        if rr.election is not None and rr.election.selected is not None
    )


def _selection_line(selection: ArmName | None, pair: PairedReading) -> str:
    if selection is None:
        return "nothing selected"
    name = f"{selection.label} (round {selection.round})"
    if pair.headline is None or pair.coverage is None:
        return f"{name} — {READING_STATE_INFO[pair.state].sentence}"
    return (
        f"{name} — origin {pair.headline.rate_a:.1%} → {pair.headline.rate_b:.1%} "
        f"on {pair.coverage.scored} origin-panel cells"
    )


class RunStanding(FrozenModel):
    """Where the run stands after a round: its selection, over whom, and how it reads against C0."""

    rounds_without_advance: int
    # ``None`` where the campaign banks no lives (`optimization.lives`).
    stalls_left: int | None
    stalls_left_cap: int | None
    lives: LivesReading | None = Field(
        description="`stalls_left` of `stalls_left_cap`, read: the pips and their words, as the "
        "readout prints them. Null where the campaign banks no lives."
    )
    selection: ArmName | None = Field(
        description="The optimizer's declared pick (`declared_selection`), as the arm it was "
        "first measured as: the origin until a round selects. Null before round 0 closes."
    )
    parent: ArmName | None = Field(
        description="The pick `selection` was promoted over. Null while it is the origin."
    )
    vs_origin: PairedReading = Field(
        description="`selection` (`b`) over C0 (`a`) on the origin panel — the newest overlap "
        "reading of that pair. The state says why where there is none: `same_individual` while "
        "the selection is the origin."
    )
    rounds_closed: int = Field(
        description="Rounds closed AFTER the origin — the unit a rounds cap counts."
    )
    improved: int = Field(
        description="Of those, how many PROMOTED an arm. A bare point estimate (lift > 0, no "
        "interval), so it is the promotion clock and never a result; read `advanced`."
    )
    advanced: int = Field(
        description="Of those, how many closed `advanced`: their pick separated from C0 on the "
        "origin panel — the verdict clock. Never summed with `improved`."
    )
    selection_line: str = Field(
        description="The ONE text reading of the selection: who, and both rates on the origin "
        "panel — or the reason there is no pair. Never the selection's rate alone. The readout, "
        "`log.md`, the activity feed and every screen print this. Stamped by the two "
        "constructors alone."
    )

    @model_validator(mode="after")
    def _line_words_the_selection(self) -> Self:
        if self.selection_line != _selection_line(self.selection, self.vs_origin):
            raise ValueError("a standing's selection_line is the wording of its own selection")
        return self

    @classmethod
    def opening(cls, lives: tuple[int, int] | None) -> RunStanding:
        return cls(
            rounds_without_advance=0,
            stalls_left=None if lives is None else stalls_left([], lives),
            stalls_left_cap=None if lives is None else lives[1],
            lives=None if lives is None else LivesReading.of(stalls_left([], lives), lives[1]),
            selection=None,
            parent=None,
            vs_origin=(unread := PairedReading.unread(ReadingState.NO_SELECTION)),
            rounds_closed=0,
            improved=0,
            advanced=0,
            selection_line=_selection_line(None, unread),
        )

    @classmethod
    def after(
        cls,
        rounds: Sequence[RoundOutcome],
        *,
        lives: tuple[int, int] | None,
    ) -> RunStanding:
        declared = declared_selection(rounds)
        picked = [key for rr in rounds if rr.election and (key := rr.election.selected) is not None]
        chosen = picked[-1]
        arms: dict[str, ArmName] = {arm.key: arm for arm in pick_line(rounds)}
        before = [individual for individual in picked if individual != chosen]
        vs_origin = next(
            (
                reading
                for rr in reversed(rounds)
                if rr.round >= declared.round
                for reading in (rr.overlap.lead, *rr.overlap.earlier)
                if reading.headline is not None
                and reading.b is not None
                and reading.b.address.individual_id == chosen
            ),
            declared.overlap.lead,
        )
        after_origin = [rr for rr in rounds if rr.round > 0]
        return cls(
            rounds_without_advance=rounds_without_advance(rounds),
            stalls_left=None if lives is None else stalls_left(rounds, lives),
            stalls_left_cap=None if lives is None else lives[1],
            lives=None if lives is None else LivesReading.of(stalls_left(rounds, lives), lives[1]),
            selection=arms[chosen],
            parent=arms[before[-1]] if before else None,
            vs_origin=vs_origin,
            rounds_closed=closed_after_origin([rr.round for rr in rounds]),
            improved=sum(1 for rr in after_origin if rr.improved),
            advanced=sum(1 for rr in after_origin if rr.overlap.advance is RoundAdvance.ADVANCED),
            selection_line=_selection_line(arms[chosen], vs_origin),
        )


@dataclass(frozen=True)
class PointContext:
    schema: PipelineSchema
    framing: TaskDecomposition
    demo: Sequence[Sample]

    def job(self, individual: OptSearchPoint) -> JobSearchPoint:
        return individual.to_job_search_point(
            schema=self.schema, framing=self.framing, demo=self.demo
        )

    def measured(self, arm: Concluded) -> JobSearchPoint:
        sp = self.job(arm.individual)
        if sp.sp_hash(self.schema) != arm.sp_hash:
            raise BankedSearchPointError(
                f"{arm.label} rebuilds as searchpoint {sp.sp_hash(self.schema)[:12]}, not the "
                f"{arm.sp_hash[:12]} its rows were measured under: the pipeline schema, framing "
                "or demo pool has moved under it."
            )
        return sp


def measured_arm(rounds: Sequence[RoundOutcome], individual_id: str) -> Concluded:
    for rr in reversed(rounds):
        arm = rr.arms.get(ArmKey(individual_id))
        if isinstance(arm, Concluded):
            return arm
    raise KeyError(f"no closed round measured individual {individual_id}")


class CycleResult(StrictModel):
    rounds: list[RoundResult]
    # Origin-EXCLUSIVE, unlike `CycleIndex.rounds`, which holds round 0.
    n_rounds_after_origin: int
    result_accuracy: float | None
    result_round: int
    # On its OWN round-0 rows. `None`, not a 0.0 level, on a cycle that never started.
    origin: OwnLevel | None
    # The individual each round ended on, never the proposals; `None` on a cycle that never started.
    level_series: LevelSeries | None
    # The held-out bench: the origin's headline on its rows, then per L1 round the STANDING
    # selection's on the same rows, held forward across rounds that held. ``None`` in the series
    # is a round whose selection pass read nothing or was never sent; ``None`` origin, no bench
    # pass read.
    bench_origin_level: float | None = None
    bench_round_levels: list[float | None] = Field(default_factory=list)
    # From config, never the rounds that ran: `lives` stops a stalling cycle short. ``None`` = no cap.
    round_budget: int | None = None
    result_prompt_fields: dict[str, dict[str, str]]
    result_pipeline_params: dict[str, Any] | None = None
    stop_reason: StopReason
    started_at: str
    finished_at: str
    langfuse_trace_id: str | None = None
    cycle_id: str | None = None
    resumed_from_round: int = 1
    # ``None`` only on an init-crash before any observer wired up.
    spend: SpendRollup | None = None
    # Set where the stop's ``STOP_REASON_INFO`` row is FAILED (``runner/termination.py::end_run_on``).
    error: ErrorRecord | None = None
    bench: BenchScore


VerifyStrategy = Literal["random", "hard"]

VERIFY_STRATEGY_LABELS: dict[VerifyStrategy, str] = {
    "random": "picked at random",
    "hard": "hardest first",
}
if set(VERIFY_STRATEGY_LABELS) != set(get_args(VerifyStrategy)):
    raise RuntimeError("VERIFY_STRATEGY_LABELS is out of step with VerifyStrategy")


class VerifyPass(FrozenModel):
    """The facts a verify pass banked, never a grade of them."""

    arm: ArmName
    sp_hash: str
    cells: list[WalkedCell]
    positions: list[int]
    strategy: VerifyStrategy
    seed: int | None
    # The grader that stamped its live reading; a reader re-grades under its own.
    scorer_id: str

    def walk(self) -> IndividualWalk[WalkedCell]:
        return IndividualWalk(self.arm.key, frozenset({MeasurementRole.VERIFY}), self.cells)


VerifyHeldAbsent = Literal["hard_picks", "under_two_fresh"]


class VerifyReading(FrozenModel):
    """A verify pass under a named scorer: the candidate on fresh cells, beside its decided ones."""

    label: str
    scorer_id: str = Field(
        description="The grader every number here was read under. A stored copy is a cache of "
        "that reading: a reader under another grader reads the pass again, never this."
    )
    strategy: VerifyStrategy = Field(
        description="How the fresh cells were picked: `random` from the unmeasured search split, "
        "or `hard` — its highest-δ cells first, which read BELOW the level by construction."
    )
    fresh: OwnLevel = Field(description="The level on the fresh cells alone, with its band.")
    recorded: OwnLevel = Field(
        description="The level on every cell a round's decision read this candidate on — its own "
        "walk and each re-score as a parent — re-read under this scorer: the number the fresh "
        "cells are a check on."
    )
    accuracy_increment: float | None = Field(
        description="`fresh` minus `recorded`, hit rate. Unpaired — the two are different cells."
    )
    composite_increment: float | None
    vs_origin: PairedReading = Field(
        description="This candidate over the campaign origin, on every cell the cycle measured "
        "both on outside the bench — the fresh ones and the overlap pass's included. "
        "`same_individual` on the origin itself."
    )
    held: bool | None = Field(
        description="Whether the fresh cells leave the recorded hit rate standing: its level sits "
        "at or below the fresh band's upper bound. `None` exactly where `held_absent` says why."
    )
    held_absent: VerifyHeldAbsent | None = Field(
        description="Why the fresh cells make no held / dropped call: `hard_picks` sit below the "
        "level whatever the candidate is worth, and `under_two_fresh` cells have no band. Read "
        "`vs_origin` there."
    )

    @model_validator(mode="after")
    def _held_or_why_not(self) -> Self:
        if (self.held is None) is (self.held_absent is None):
            raise ValueError("a verify reading makes its held call, or says why it makes none")
        return self


def panel_cuts(panels: Sequence[tuple[int | None, int | None]]) -> list[bool]:
    fullest = max((scored for scored, _ in panels if scored is not None), default=0)
    return [
        scored is not None and ((expected is not None and scored < expected) or scored < fullest)
        for scored, expected in panels
    ]


Crown = Literal["elected", "uncontested"]


class ArmPanel(FrozenModel):
    """The cells one arm's walk holds of the panel its round asked of it."""

    scored: int | None = Field(
        description="Cells the walk holds. Null on an arm with no score report — never measured, "
        "which is not a walk of zero cells."
    )
    expected: int | None = Field(
        description="The walk's length; null until its first cell announces it."
    )
    cached: int | None = Field(
        description="Of `scored`, how many were replayed from the archive rather than measured. "
        "Null on an arm never measured."
    )
    cached_share: float | None = Field(
        description="`cached` over `scored`. Null where either is, and on a walk holding no cell."
    )
    cut: bool = Field(
        description="Stopped short of its round's panel (`panel_cuts`): under its own `expected`, "
        "or, for a panel arm, under the fullest panel a panel arm of its round reached."
    )

    @classmethod
    def of(
        cls, scored: int | None, expected: int | None, cached: int | None, *, cut: bool
    ) -> ArmPanel:
        share = cached / scored if scored and cached is not None else None
        return cls(scored=scored, expected=expected, cached=cached, cached_share=share, cut=cut)


class ArmAbility(FrozenModel):
    """One arm's difficulty-adjusted Rasch ability on its round's joint-fit scale."""

    theta: float | None
    se: float | None
    ci_lo: float | None = Field(
        description="Lower bound of the 95% interval on `theta` (`ruler.py::theta_band`); null "
        "where `theta` or `se` is."
    )
    ci_hi: float | None = Field(description="Upper bound of the same interval.")
    caveat: ThetaCaveat | None = Field(
        description="Why `theta` is not this arm's ability, of the ARM's own scope."
    )

    @classmethod
    def of(
        cls, theta: float | None, se: float | None, caveat: ThetaCaveat | None
    ) -> ArmAbility | None:
        if (theta, se, caveat) == (None, None, None):
            return None
        lo, hi = theta_band(theta, se) or (None, None)
        return cls(theta=theta, se=se, ci_lo=lo, ci_hi=hi, caveat=caveat)


class ArmElection(FrozenModel):
    """What one arm's round decided about it. Built by :meth:`of` alone."""

    held: bool = Field(
        description="The arm's round has held its election. `selected: false` reads the same on a "
        "round still scoring and on one that crowned nobody; only this tells them apart."
    )
    selected: bool
    leading: bool = Field(
        description="The ONE arm its round is read off, as the selector named it "
        "(`Election.leading`); false until the election."
    )
    crown: Crown | None = Field(
        description="How a selected arm advanced, read off the election's electable count; null "
        "on every arm the election did not select."
    )

    @classmethod
    def of(
        cls,
        key: ArmKey,
        *,
        held: bool,
        selected: ArmKey | None,
        leading: ArmKey | None,
        electable: int | None,
    ) -> ArmElection:
        won = key == selected
        crown: Crown | None = None
        if won and electable is not None:
            crown = "elected" if electable > 1 else "uncontested"
        return cls(held=held, selected=won, leading=key == leading, crown=crown)


ArmVerdict = Literal[
    "retired",
    "invalid",
    "origin",
    "elected",
    "uncontested",
    "selected",
    "not_elected",
    "unmeasured",
    "awaiting",
]

ARM_VERDICT_LABELS: dict[ArmVerdict, str] = {
    "retired": "retired",
    "invalid": "invalid — never measured",
    "origin": "origin",
    "elected": "won its round",
    "uncontested": "advanced uncontested",
    "selected": "selected",
    "not_elected": "not elected",
    "unmeasured": "not measured yet",
    "awaiting": "awaiting election",
}
assert ARM_VERDICT_LABELS.keys() == set(get_args(ArmVerdict))


def arm_verdict(reading: ArmReading, *, retired: bool) -> ArmVerdict:
    if retired:
        return "retired"
    if reading.end == "invalid":
        return "invalid"
    if reading.role == MeasurementRole.ORIGIN:
        return "origin"
    election = reading.election
    if election.crown is not None:
        return election.crown
    if election.selected:
        return "selected"
    if election.held:
        return "not_elected"
    return "unmeasured" if reading.state in ("proposed", "measuring") else "awaiting"


def _own_level(
    accuracy: float | None,
    composite: float | None,
    band: tuple[float | None, float | None],
    *,
    n: int,
) -> OwnLevel | None:
    if accuracy is None and composite is None:
        return None
    lo, hi = band if None not in band else (None, None)
    return OwnLevel(
        accuracy=None if accuracy is None else BandedValue(value=accuracy, ci_lo=lo, ci_hi=hi),
        composite=None
        if composite is None
        else BandedValue(value=composite, ci_lo=None, ci_hi=None),
        n=n,
    )


class ArmReading(FrozenModel):
    """One arm of one round, read: the one carrier every surface that lists or ranks arms embeds.

    Each part lands when its fact exists, so an arm in flight is this type with fewer parts set.
    """

    arm: ArmName = Field(description="THE key, and where the arm was first walked.")
    role: ArmRole = Field(
        description="What the arm IS in this round: its origin, one of its proposals, or a "
        "`parent` re-read on its cells as a reference — never one of the round's proposals, "
        "whatever round `arm` names."
    )
    sp_hash: str = Field(
        description="The archive address of this arm's measurements (`prompt_fields_id`). Not a "
        "key: a re-proposed configuration shares it across rounds. Empty before it is measured."
    )
    changes_description: str
    state: ArmStateKind = Field(
        description="Where the arm's walk stands: proposed, being measured, or ended — cut "
        "short against it, or measured."
    )
    end: ArmEnd | None = Field(
        description="How its walk ended short of its panel: cut against it (`invalid` refused it "
        "before a cell), or `locked_in` — settled FOR it by the eliminator. Null on a walk still "
        "running and one that ran its panel."
    )
    walked_short: bool = Field(
        description="`end` names a stop of its walk, for it or against it: false on an `invalid` "
        "arm, which never started one."
    )
    own: OwnLevel | None = Field(
        description="The arm's own level on its round's cells: accuracy with the band the "
        "gateway folds per cell, the composite beside it, over `n` rows carrying a verdict. Null "
        "before the first row is graded, and on an `invalid` arm, which holds no cell."
    )
    panel: ArmPanel
    tokens: TokenAccount | None = Field(
        description="What measuring it consumed, the BACKEND bucket alone, replayed rows "
        "excluded; null until its score report folds an account."
    )
    prefix: PrefixReading | None = Field(
        description="The provider's prefix-cache reading of `tokens.input`; null beside a null "
        "`tokens`."
    )
    ability: ArmAbility | None = Field(description="Null outside the round's election fit.")
    vs_reference: PairedReading | None = Field(
        description="This arm (`b`) over the individual it was judged against (`a`), on the "
        "cells both scored. Null on the origin and until the election stamps it."
    )
    election: ArmElection
    bench: BenchReading | None = Field(
        description="This arm's individual on the held-out bench set; null unless a pass graded it."
    )
    verify: VerifyReading | None = Field(
        description="Its last `verify` pass, on search cells its rounds never bought."
    )
    on_origin_panel: LineRate | None = Field(
        description="Its individual's rate on the origin panel, as the newest overlap reading "
        "holds it: the one level two arms of different rounds may be differenced on. Null for an "
        "arm neither on the best-so-far line nor triggered, and where it does not hold the whole "
        "panel."
    )

    @classmethod
    def of(
        cls,
        name: ArmName,
        arm: ArmState,
        *,
        fold: ScoreSummary | None,
        cut: bool,
        election: ArmElection,
        fit: ArmFit | None,
    ) -> ArmReading:
        """*fold* is its round's, or `AnnouncedArms.fold` in flight; *fit* the election's, ``None`` until held."""
        match arm:
            case Proposed():
                sp_hash, expected = "", None
            case Measuring() | Cut() | Measured():
                sp_hash, expected = arm.sp_hash, arm.expected_samples
        tokens = None if fold is None else fold.tokens
        panel = ArmPanel.of(
            None if fold is None else fold.walked,
            expected,
            None if fold is None else fold.cached,
            cut=cut,
        )
        return cls(
            arm=name,
            role=arm.role,
            sp_hash=sp_hash,
            changes_description=arm.changes_description,
            state=arm.state,
            end=arm_end(arm),
            walked_short=walked_short(arm),
            own=None
            if fold is None
            else _own_level(
                fold.accuracy,
                fold.composite_fitness,
                (fold.mean_fitness_ci_lo, fold.mean_fitness_ci_hi),
                n=fold.total,
            ),
            panel=panel,
            tokens=tokens,
            prefix=None if tokens is None else tokens.prefix(replayed=False),
            ability=None
            if fit is None
            else ArmAbility.of(fit.theta, fit.theta_se, fit.theta_caveat),
            vs_reference=None if fit is None else fit.vs_reference,
            election=election,
            bench=None,
            verify=None,
            on_origin_panel=None,
        )

    def on_line(self, line: Mapping[str, LineRate]) -> ArmReading:
        return self.model_copy(update={"on_origin_panel": line.get(self.arm.key)})


def line_by_individual(overlap: OverlapReading | None) -> dict[str, LineRate]:
    """Only members holding the WHOLE panel: a rate over a shorter denominator sat a different exam."""
    if overlap is None:
        return {}
    whole = len(overlap.positions)
    rates = (*triggered_rates(overlap), *overlap_line(overlap))
    return {r.arm.key: r for r in rates if r.n == whole}


class DiagnosticRunRecord(FrozenModel):
    """One ``noise-floor`` run's sidecar: the run-to-run spread of fresh re-scores of ONE config."""

    ts: str
    dataset: str
    source_campaign: str
    source_cycle: str
    source_candidate_id: str
    config_hash: str
    workspace_n: int
    workspace_accuracy: float
    source_campaign_accuracy: float | None
    source_campaign_composite: float
    source_campaign_n: int
    noise_floor_k: int
    noise_floor_mean: float
    noise_floor_ci_lo: float
    noise_floor_ci_hi: float
    noise_floor_raw: list[float]


RescoreCount = NewType("RescoreCount", int)


def rescore_count(k: int) -> RescoreCount:
    if k < 2:
        raise ValueError(f"k={k}: one rescore has no spread to report — ask for two or more.")
    return RescoreCount(k)


# Ranks what the operator READS only: the engine's scoring order is `build_round_order`.
HardSampleOrder = Literal["info_gain", "difficulty"]
