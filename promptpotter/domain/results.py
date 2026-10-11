from __future__ import annotations

import math
from collections.abc import Callable, Collection, Iterable, Mapping, Sequence
from contextlib import suppress
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
    ArmPointer,
    LiftReference,
    PairedReading,
    ReadingState,
    ReadingStateKind,
)
from promptpotter.domain.phases import ErrorRecord, StopReason
from promptpotter.domain.round_diagnostics import RoundDiagnostics
from promptpotter.domain.ruler import AbilityReading, ThetaCaveat, theta_band
from promptpotter.domain.scoring import (
    NO_CELLS,
    CellSheet,
    GradedCell,
    WalkedCell,
    is_answer_collapsed,
)
from promptpotter.domain.search_point import JobSearchPoint, TaskDecomposition
from promptpotter.domain.spend import CloseSpend, PrefixReading, SpendRollup, TokenAccount
from promptpotter.domain.strict_model import StrictModel
from promptpotter.domain.wounds import RuntimeFailure, ValidationFailure
from promptpotter.shared.errors import ConflictError
from promptpotter.shared.hashing import shapes_optimizer_prompt, stable_hash
from promptpotter.shared.measurement_context import SCOPE_ROLES, MeasurementRole, RoleScope

if TYPE_CHECKING:
    from promptpotter.domain.pipeline_schema import PipelineSchema
    from promptpotter.domain.sample import Sample

__all__ = [
    "ARM_VERDICT_LABELS",
    "CEILING_FRACTION",
    "DISPLAY_METRIC_INFO",
    "ROUND_ADVANCE_INFO",
    "VERIFY_STRATEGY_LABELS",
    "ArmAbility",
    "ArmElection",
    "ArmOutcome",
    "ArmPanel",
    "ArmReading",
    "ArmSpend",
    "ArmVerdict",
    "BankedSearchPointError",
    "CandidateProposal",
    "CellFold",
    "Crown",
    "CycleResult",
    "DegradationContext",
    "DegradationHealth",
    "DiagnosticRunRecord",
    "DisplayMetric",
    "HardSampleOrder",
    "InRunCells",
    "IndividualWalk",
    "LineRate",
    "LineStep",
    "LivesReading",
    "OptimizerFact",
    "OverlapReading",
    "ReferenceReading",
    "RescoreCount",
    "RoundAdvance",
    "RoundAdvanceInfo",
    "RoundClocks",
    "RoundResult",
    "RunStanding",
    "ScoreSummary",
    "ScoreboardRankKey",
    "ScoreboardRow",
    "ScoredCandidate",
    "StallEffect",
    "VerifyHeldAbsent",
    "VerifyPass",
    "VerifyReading",
    "VerifyStrategy",
    "arm_verdict",
    "best_line",
    "candidate_label",
    "closed_after_origin",
    "declared_selection",
    "degradation_reading",
    "diagnostic_held",
    "individual_cells",
    "is_electable",
    "is_floor_pinned",
    "is_leader_eligible",
    "line_by_individual",
    "measured_cells",
    "measured_searchpoint",
    "merge_known_outcomes",
    "order_floor",
    "origin_panel",
    "overlap_line",
    "panel_cuts",
    "parse_candidate_label",
    "pick_line",
    "recall_at",
    "rescore_count",
    "round_advance",
    "round_clocks",
    "rounds_without_advance",
    "scoreboard_rank_key",
    "unscoreable_cells",
]


class ArmOutcome(StrEnum):
    """How an arm's measurement ended, and whose decision ended it."""

    MEASURED = "measured"
    INVALID = "invalid"  # rejected before it cost a cell; the scores beside it are synthetic
    SKIPPED = "skipped"
    BROKEN = "broken"
    # `Eliminator.stop_disqualifies` says whether the stop is a rejection.
    ELIMINATED = "eliminated"
    LOCKED_IN = "locked_in"

    @property
    def cut_short(self) -> bool:
        """Stopped before its panel against the arm — by the operator, the bench or a cut."""
        return self in (ArmOutcome.SKIPPED, ArmOutcome.BROKEN, ArmOutcome.ELIMINATED)

    @property
    def ended_early(self) -> bool:
        """Stopped before its panel for the arm or against it — what a stopped-walk badge marks."""
        return self.cut_short or self is ArmOutcome.LOCKED_IN


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


def parse_candidate_label(label: str) -> tuple[int, int]:
    """Labels are 1-indexed, the on-disk candidate list 0-indexed."""
    if label == "C0":
        return 0, 0
    round_part, _, idx_part = label[1:].partition(".")
    if not label.startswith("C") or not idx_part:
        raise ValueError(f"bad candidate label {label!r}; expected C0 or C{{round}}.{{n}}.")
    try:
        round_num, idx_one_based = int(round_part), int(idx_part)
    except ValueError:
        raise ValueError(
            f"bad candidate label {label!r}; expected C0 or C{{round}}.{{n}}."
        ) from None
    if idx_one_based < 1:
        raise ValueError(f"candidate index in {label!r} must be >= 1.")
    return round_num, idx_one_based - 1


def closed_after_origin(rounds: Sequence[int]) -> int:
    return sum(1 for number in rounds if number > 0)


class CellFold(StrictModel):
    model_config = ConfigDict(frozen=True)

    # The cells carrying a verdict, a deprecated one included as a miss.
    total: int
    # ``None`` where `total` is 0: no fitness is not a 0.0.
    accuracy: float | None
    composite_fitness: float | None
    deprecated: int


class ScoreSummary(CellFold):
    evaluators: dict[str, float]
    # Student-t CI on the mean per-cell FITNESS, whatever the formula; ``None`` under two cells.
    mean_fitness_ci_lo: float | None
    mean_fitness_ci_hi: float | None


class ScoredCandidate(ScoreSummary):
    """One candidate's score report: its cells folded, beside what the round knows of the arm.

    ``accuracy`` IS mean fitness, so there is no ``hits``.
    """

    model_config = ConfigDict(arbitrary_types_allowed=True)

    candidate_id: str
    label: str
    changes_description: str = ""
    pipeline_overlay: dict[str, Any] | None = None
    # Origin floor ⊕ this candidate's delta; ``pipeline_overlay`` above is the sparse fork transport.
    resolved_pipeline_params: dict[str, Any] | None = None
    # Stamped, never re-derived: it covers each node's rendered ``prompt``, stripped from the above.
    sp_hash: str = ""
    prompt_fields: dict[str, Any] = Field(default_factory=dict)
    outcome: ArmOutcome
    scored_samples: int = 0
    expected_samples: int = 0
    cached_samples: int = 0
    # BACKEND tokens only: judge and optimizer spend carry no candidate.
    input_tokens: int | None = None
    output_tokens: int | None = None
    cache_read_tokens: int | None = None
    validation_failures: list[ValidationFailure] = Field(default_factory=list)
    runtime_failures: list[RuntimeFailure] = Field(default_factory=list)
    # The eliminator's own keys, opaque to the bench (potter's: `pobb/checks.py::EliminationContext`).
    elimination_context: dict[str, Any] = Field(default_factory=dict)
    elimination_reason: str | None = None
    degradation_context: DegradationContext = Field(default_factory=DegradationContext)
    # ``None`` on the origin, on an arm with no parent, and until the round has read every arm.
    vs_reference: PairedReading | None = None
    # Rasch ability on the round's joint-fit scale; `None` outside the election fit.
    theta: float | None = None
    theta_se: float | None = None
    # This ARM's own caveat; the round-scale ones ride ``RoundResult.ability``.
    theta_caveat: ThetaCaveat | None = None

    def searchpoint(
        self, *, schema: PipelineSchema, framing: TaskDecomposition, demo: Sequence[Sample]
    ) -> JobSearchPoint:
        if self.resolved_pipeline_params is None:
            raise BankedSearchPointError(f"{self.label}'s round file carries no resolved config.")
        sp = OptSearchPoint.from_prompt_fields(
            self.prompt_fields, pipeline_params=self.resolved_pipeline_params
        ).to_job_search_point(schema=schema, framing=framing, demo=demo)
        if self.sp_hash and sp.sp_hash(schema) != self.sp_hash:
            raise BankedSearchPointError(
                f"{self.label} rebuilds from its round file as searchpoint "
                f"{sp.sp_hash(schema)[:12]}, not the {self.sp_hash[:12]} its rows were measured "
                "under: the pipeline schema, framing or demo pool has moved under it."
            )
        return sp


class BankedSearchPointError(ConflictError):
    code = "banked_searchpoint_moved"


def is_leader_eligible(cs: ScoredCandidate) -> bool:
    return cs.outcome not in (ArmOutcome.BROKEN, ArmOutcome.SKIPPED)


def is_electable(cs: ScoredCandidate, rows: Sequence[GradedCell]) -> bool:
    """NOT the whole election rule: the coverage floor is `scoring/selection.py::distinct_valid_cells`."""
    return is_leader_eligible(cs) and bool(rows) and not is_answer_collapsed(rows)


def round_document_digest(rr: RoundResult) -> str:
    """The WHOLE outcome, never a chosen subset: under-firing goes stale in silence."""
    outcome = rr.model_dump(mode="json", include=set(RoundOutcome.model_fields))
    return stable_hash([outcome, rr.cells().model_dump(mode="json")])


def unscoreable_cells(results: Iterable[GradedCell]) -> int:
    """Never ``scored_samples - total``, which counts a PoBB stop and a deprecated row as holes."""
    return sum(1 for r in results if r.facts.errored)


def recall_at(results: Iterable[GradedCell]) -> dict[int, float]:
    ranks = [r.facts.ground_truth_rank for r in results if not r.facts.errored]
    if not any(rank is not None for rank in ranks):
        return {}
    return {
        k: sum(1 for rank in ranks if rank is not None and rank <= k) / len(ranks) for k in (1, 5)
    }


@shapes_optimizer_prompt
def merge_known_outcomes(
    prior: Iterable[GradedCell], incoming: Iterable[GradedCell]
) -> list[GradedCell]:
    """Never score this pool: its rows were measured by DIFFERENT configurations."""
    by_sid = {cell.sample_id: cell for cell in prior}
    for cell in incoming:
        by_sid[cell.sample_id] = cell
    return list(by_sid.values())


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
    """A mask lens (``mask/verdicts.py``) passes no `is_leading`: pinned to rank 1 it cannot disagree."""
    return (
        is_leading,
        # The round order is stratified, so a walk cut short kept a biased slice, not a smaller sample.
        not is_partial,
        order_floor(theta),
        order_floor(composite_fitness),
        order_floor(accuracy),
    )


class ScoreboardRow(StrictModel):
    """One rank-ordered row of a round's scoreboard: an arm's reading at its rank."""

    model_config = ConfigDict(frozen=True)

    rank: int
    reading: ArmReading


class CandidateProposal(StrictModel):
    model_config = ConfigDict(arbitrary_types_allowed=True)

    opt_sp: OptSearchPoint
    pipeline_overlay: dict[str, dict[str, Any]] = Field(default_factory=dict)
    validation_failures: list[ValidationFailure] = Field(default_factory=list)
    runtime_failures: list[RuntimeFailure] = Field(default_factory=list)


class ReferenceReading(StrictModel):
    """A round's reference individual re-read on the round's panel; potter's is its parent."""

    model_config = ConfigDict(arbitrary_types_allowed=True, frozen=True)

    opt_sp: OptSearchPoint
    report: ScoredCandidate
    results: CellSheet = NO_CELLS


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
        if rr.round == 0 or rr.generation_only or rr.electable_count == 0:
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


class LivesReading(StrictModel):
    """A run's life bank as every surface shows it. Built by :meth:`of` alone."""

    model_config = ConfigDict(frozen=True)

    bar: str = Field(description="One ♥ per banked life, one ♡ per spent one; 💀 on an empty bank.")
    label: str
    spent: bool = Field(description="The bank is empty: the run stops after this round.")

    @classmethod
    def of(cls, left: int, cap: int | None) -> LivesReading:
        return cls(bar=lives_bar(left, cap), label=lives_label(left, cap), spent=left <= 0)


class OverlapReading(StrictModel):
    """The best-so-far line on the ORIGIN PANEL: each pick paired with C0 on one set of cells."""

    # Its rows reach no election, floor, lift, ruler or acquisition: OUTSIDE them is the point.
    model_config = ConfigDict(frozen=True)

    # :func:`origin_panel`, by sample slot, ascending; empty where no pass was sent.
    sample_ids: tuple[int, ...]
    lead: PairedReading = Field(
        description="The standing pick (`b`) over C0 (`a`) on the origin panel, in report scope. "
        "`bought` on each member is what this round paid to put it back on the whole panel."
    )
    earlier: tuple[PairedReading, ...] = Field(
        description="Each earlier pick on the line over C0 on the same panel, oldest first."
    )
    advance: RoundAdvance

    @classmethod
    def of(
        cls,
        round_num: int,
        improved: bool,
        *,
        sample_ids: Sequence[int],
        lead: PairedReading,
        earlier: Sequence[PairedReading],
    ) -> OverlapReading:
        return cls(
            sample_ids=tuple(sample_ids),
            lead=lead,
            earlier=tuple(earlier),
            advance=round_advance(round_num, improved, lead, earlier),
        )

    @classmethod
    def unpaired(cls, state: ReadingState, round_num: int, improved: bool) -> OverlapReading:
        return cls.of(
            round_num, improved, sample_ids=(), lead=PairedReading.unread(state), earlier=()
        )


class LineRate(StrictModel):
    """One member of the best-so-far line as an overlap reading holds it."""

    model_config = ConfigDict(frozen=True)

    arm: ArmPointer
    rate: float = Field(description="Its rate over the panel cells it and C0 both scored.")
    n: int = Field(description="How many of the panel's cells it holds scoreable.")


def overlap_line(overlap: OverlapReading) -> list[LineRate]:
    """C0 is on the lead's reading of it: rates set side by side must be one pair's."""
    lead = overlap.lead
    if lead.headline is None or lead.a is None or lead.a.address.arm is None:
        return []
    line = [LineRate(arm=lead.a.address.arm, rate=lead.headline.rate_a, n=lead.a.n)]
    for pick in (*overlap.earlier, lead):
        if pick.headline is not None and pick.b is not None and pick.b.address.arm is not None:
            line.append(LineRate(arm=pick.b.address.arm, rate=pick.headline.rate_b, n=pick.b.n))
    return line


class LineStep(NamedTuple):
    """``opt_sp`` re-measures the member: another arm's prompt would read under this one's label."""

    round: int
    candidate_id: str
    label: str
    opt_sp: OptSearchPoint


@shapes_optimizer_prompt
def measured_cells(rows: Iterable[GradedCell]) -> set[int]:
    return {cell.sample_id for cell in rows if cell.scored}


def is_floor_pinned(rows: Iterable[GradedCell]) -> bool:
    """``fitness``, never ``objective``: a ``per_cell`` composite charges a miss, so all-miss is not 0."""
    graded = [
        fitness for cell in rows if cell.scored and (fitness := cell.grade.fitness) is not None
    ]
    return bool(graded) and all(fitness <= 0.0 for fitness in graded)


def best_line(rounds: Sequence[RoundResult]) -> list[LineStep]:
    """The declared picks, never a high-water of each round's composite: those sat different rows."""
    individuals = {rr.opt_sp.id: rr.opt_sp for rr in rounds if rr.opt_sp is not None}
    return [
        LineStep(
            round=arm.round,
            candidate_id=arm.candidate_id,
            label=arm.label,
            opt_sp=individuals[arm.candidate_id],
        )
        for arm in pick_line(rounds)
    ]


def pick_line(rounds: Sequence[RoundOutcome]) -> list[ArmPointer]:
    labels: dict[str, str] = {}
    became: dict[str, int] = {}
    for rr in rounds:
        for cs in rr.candidate_scores:
            labels.setdefault(cs.candidate_id, cs.label)
        if rr.opt_sp is not None and rr.selected_labels:
            became.setdefault(rr.opt_sp.id, rr.round)
    # `R{n}` only where an individual was never a scored candidate: an anomaly.
    return [
        ArmPointer(round=rnd, label=labels.get(cid) or f"R{rnd}", candidate_id=cid)
        for cid, rnd in became.items()
    ]


def origin_panel(
    origin_cells: Collection[int], *, poolable: Collection[int], size: int
) -> list[int]:
    """*origin_cells* are the origin's own ROUND's, never every cell it was later read on."""
    return sorted(set(origin_cells) & set(poolable))[:size]


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
        to_ceiling = first(lambda r: r.accuracy is not None and r.accuracy >= target)
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


class DegradationHealth(StrictModel):
    """A round's degradation verdict from the backend's warning stamps; it never stops the run."""

    model_config = ConfigDict(frozen=True)

    grade: HealthGrade
    # `None` is the `healthy` grade.
    cause: HealthCause | None = None
    samples: int
    structural_count: int
    transient_count: int
    # The pipeline succeeded and emitted no extractable prediction; the backend stamps no warning.
    no_result_count: int = 0
    # Attempted cells that reported nothing (re-run); a ``no_result_count`` row ran (fix the format).
    hole_count: int = 0
    # Panel cells never SENT: no row, and outside ``samples``.
    not_attempted: int = 0
    # Sent and measured, no verdict (the formula named a term the row lacks): in ``samples``, no rate.
    unscored: int = 0
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


class OptimizerFact(StrictModel):
    """One labelled reading an optimizer reports about a round, worded by its own runtime."""

    model_config = ConfigDict(frozen=True)

    key: str
    label: str
    text: str
    value: float | None
    # A `stat` reads on one line; a `note` is prose the optimizer carries into its next round.
    kind: Literal["stat", "note"]


class RoundCells(StrictModel):
    """A :class:`RoundResult`'s four row fields as archive addresses, in walk order."""

    model_config = ConfigDict(frozen=True)

    head: list[WalkedCell] = Field(default_factory=list)
    arms: dict[str, list[WalkedCell]] = Field(default_factory=dict)
    references: dict[str, list[WalkedCell]] = Field(default_factory=dict)
    overlap: dict[str, list[WalkedCell]] = Field(default_factory=dict)

    def walks(self, ended_on: OptSearchPoint | None) -> list[IndividualWalk[WalkedCell]]:
        return _round_walks(
            ended_on,
            head=self.head,
            arms=self.arms,
            references=self.references,
            overlap=self.overlap,
        )


class IndividualWalk[C: tuple[Any, ...]](NamedTuple):
    """A cell is any tuple LEADING with its sample's content key."""

    individual_id: str
    roles: frozenset[MeasurementRole]
    cells: Sequence[C]


# The WALK's role, never the answer's `role` stamp: that names who FILED it, and a replay reads it.
_ROW_SET_ROLES: dict[str, frozenset[MeasurementRole]] = {
    "head": SCOPE_ROLES[RoleScope.DECISION],
    "arms": SCOPE_ROLES[RoleScope.DECISION],
    "references": SCOPE_ROLES[RoleScope.DECISION],
    "overlap": frozenset({MeasurementRole.OVERLAP}),
}
assert _ROW_SET_ROLES.keys() == RoundCells.model_fields.keys()


def _round_walks[C: tuple[Any, ...]](
    ended_on: OptSearchPoint | None,
    *,
    head: Sequence[C],
    **by_individual: Mapping[str, Sequence[C]],
) -> list[IndividualWalk[C]]:
    keyed = {**by_individual, "head": {} if ended_on is None else {ended_on.id: head}}
    return [
        IndividualWalk(individual_id, _ROW_SET_ROLES[row_set], cells)
        for row_set in ("arms", "references", "head", "overlap")
        for individual_id, cells in keyed[row_set].items()
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


class DisplayMetricInfo(NamedTuple):
    label: str
    glyph: str
    title: str


#: IN PICK ORDER — the tiebreak where the elected column is not among those shown.
DISPLAY_METRIC_INFO: dict[DisplayMetric, DisplayMetricInfo] = {
    "accuracy": DisplayMetricInfo(
        "accuracy",
        "%",
        "Raw accuracy — correctness rate over the candidate's measured subset (subset-relative).",
    ),
    "ability": DisplayMetricInfo(
        "ability θ",
        "θ",
        "Difficulty-adjusted ability θ — what a selector declaring ability (potter's) elects on. "
        "A logit (not a %): comparable within a round; cross-round comparison waits on the stable "
        "δ bank.",
    ),
    "composite": DisplayMetricInfo(
        "composite",
        "∑",
        "Composite fitness under the active scoring formula (equals accuracy when no formula is "
        "set).",
    ),
}
if set(DISPLAY_METRIC_INFO) != set(get_args(DisplayMetric)):
    raise RuntimeError("DISPLAY_METRIC_INFO is out of step with DisplayMetric (domain/results.py)")


class RoundOutcome(StrictModel):
    """``RoundClosedRecord`` carries exactly this: a field declared here reaches the ledger."""

    model_config = ConfigDict(arbitrary_types_allowed=True)

    round: int
    label: str = Field(
        description="WHOSE numbers this round's headline (`accuracy`, `composite_fitness`, "
        "`total`, `evaluators`, `results`) carries: the candidate label (`C0`, `C3.1`) of the "
        "individual the round ENDED on. Equal to `leading_label` where the round selected; on a "
        "held round it is the retained PARENT, re-scored on this round's cells, and names an arm "
        "of an earlier round — so the headline is never the leading challenger's."
    )
    # Not defaulted: a MISSING key must fail rather than quietly become a rate.
    accuracy: float | None
    composite_fitness: float | None
    total: int
    improved: bool
    # `None` only before the election runs.
    verdict_reason: str | None = None
    # `Selector.elects_on`: the column of `scoreboard` this round was WON on.
    elects_on: DisplayMetric
    degraded_samples: int = 0
    not_attempted: int = 0
    # Cells of the winner's panel measured and ungraded: re-graded off the banked row, never re-run.
    unscored: int = 0
    # Fatal-warning samples discarded from total/accuracy on the winner's run.
    deprecated: int = 0
    # :func:`recall_at`; empty where the backend ranks nothing.
    recall_at: dict[int, float] = Field(default_factory=dict)
    # `None` = never fit. Its SE is a precision, never a penalty: no rank key, no `mean - λ·se`.
    ability: AbilityReading | None = None
    prompt_fields: dict[str, Any]
    pipeline_params: dict[str, Any] | None = None
    candidates_scored: int
    # Arms that entered the election. Zero says the proposer made no testable variant, not a stall.
    electable_count: int = 0
    candidate_scores: list[ScoredCandidate] = Field(default_factory=list)
    # By LABEL; empty when the round HELD, and round 0 selects the ``C0`` it adopted.
    selected_labels: list[str]
    leading_label: str | None = Field(
        description="The ONE arm of `candidate_scores` this round is read off, by LABEL, as its "
        "selector named it (`Selection.leading_id`) and never re-ranked: the selection where "
        "there is one, on a held round the challenger that came closest on the selector's own "
        "objective, at round 0 the origin. Rank 1 of `scoreboard`. `null` where the selector "
        "could read no arm. NOT whose numbers the headline carries: that is `label`."
    )
    evaluators: dict[str, float] = Field(default_factory=dict)
    reference_rule: LiftReference | None = Field(
        default=None,
        description="Which individual this round's arms were read against, as the `a` member of "
        "each arm's `vs_reference`: the round's standing best (`best_so_far`), or the better of "
        "the arm's own parents on the cells the arm measured (`parents`). `null` on round 0, "
        "which reads no arm against anything.",
    )
    # Not `improved` above, the selector's point estimate. `pending` until the overlap pass lands.
    overlap: OverlapReading
    diagnostics: RoundDiagnostics | None = None
    health: DegradationHealth | None = None
    # Resume rebuilds `Cycle.opt_sp` from it. None only on a round that never closed.
    opt_sp: OptSearchPoint | None = None
    optimizer_state: OptimizerState
    optimizer_facts: list[OptimizerFact] = Field(default_factory=list)
    # A diag round: variants generated, never scored.
    generation_only: bool = False

    @property
    def origin(self) -> ScoredCandidate:
        """Round 0's arm, addressed by the individual the round ended on, never by position."""
        assert self.round == 0 and self.opt_sp is not None, "round 0 ends on the origin"
        return {c.candidate_id: c for c in self.candidate_scores}[self.opt_sp.id]

    def _arm(self, scored: ScoredCandidate) -> ArmPointer:
        return ArmPointer(round=self.round, label=scored.label, candidate_id=scored.candidate_id)

    @property
    def ended_on(self) -> ArmPointer | None:
        """`label` as an arm, in the round that MINTED it: an earlier one where this round held."""
        if self.opt_sp is None:
            return None
        minted = self.round
        if all(c.label != self.label for c in self.candidate_scores):
            with suppress(ValueError):
                minted = parse_candidate_label(self.label)[0]
        return ArmPointer(round=minted, label=self.label, candidate_id=self.opt_sp.id)

    @property
    def leading_arm(self) -> ArmPointer | None:
        """`leading_label` as an arm; ``None`` where the selector could read none."""
        return next(
            (self._arm(c) for c in self.candidate_scores if c.label == self.leading_label), None
        )

    @property
    def selected_arms(self) -> list[ArmPointer]:
        """`selected_labels` as arms, in that order; empty on a round that held."""
        return [self._arm(c) for c in self.selected_scores]

    def arm_reading(self, scored: ScoredCandidate, *, cut: bool) -> ArmReading:
        return ArmReading.of(
            self._arm(scored),
            scored,
            cut=cut,
            changes_description=scored.changes_description,
            ability=ArmAbility.of(scored.theta, scored.theta_se, scored.theta_caveat),
            vs_reference=scored.vs_reference,
            election=ArmElection.of(
                scored.label,
                held=True,
                selected=self.selected_labels,
                leading=self.leading_label,
                electable=self.electable_count,
            ),
        ).on_line(line_by_individual(self.overlap))

    def arm_readings(self) -> list[ArmReading]:
        scored = self.candidate_scores
        cuts = panel_cuts([(c.scored_samples, c.expected_samples) for c in scored])
        return [self.arm_reading(c, cut=cut) for c, cut in zip(scored, cuts, strict=True)]

    @property
    def selected_scores(self) -> list[ScoredCandidate]:
        """The selected arms' rows, in ``selected_labels`` order."""
        by_label = {c.label: c for c in self.candidate_scores}
        return [by_label[label] for label in self.selected_labels if label in by_label]


class RoundResult(RoundOutcome):
    """A round with its rows, which is the round file itself."""

    # `extra="ignore"`: the computed `round_id`/`scoreboard` are dumped and must read back.
    model_config = ConfigDict(arbitrary_types_allowed=True, extra="ignore", frozen=True)

    # Offset of its own ``RoundClosedRecord``. ``None`` where no ledger was bound: 0 is a real offset.
    at_offset: int | None = None
    results: CellSheet = NO_CELLS
    all_candidate_results: dict[str, CellSheet] = Field(default_factory=dict)
    # Its own field: `all_candidate_results.values()` is walked as arms. Keyed by `vs_reference.a`.
    reference_results: dict[str, CellSheet] = Field(default_factory=dict)
    # Keyed by the MEASURED individual, never flat; outside the two arm maps — see `OverlapReading`.
    overlap_results: dict[str, CellSheet] = Field(default_factory=dict)

    @computed_field  # type: ignore[prop-decorator]
    @property
    def round_id(self) -> str:
        return f"round_{self.round}"

    def cells(self) -> RoundCells:
        return RoundCells(
            head=self.results.addresses(),
            arms={k: v.addresses() for k, v in self.all_candidate_results.items()},
            references={k: v.addresses() for k, v in self.reference_results.items()},
            overlap={k: v.addresses() for k, v in self.overlap_results.items()},
        )

    @computed_field  # type: ignore[prop-decorator]
    @property
    def scoreboard(self) -> list[ScoreboardRow]:
        """The rank-ordered display table: rank 1 is `leading_label`, then θ, then composite."""
        cuts = dict(
            zip(
                (c.label for c in self.candidate_scores),
                panel_cuts([(c.scored_samples, c.expected_samples) for c in self.candidate_scores]),
                strict=True,
            )
        )
        ranked = sorted(
            self.candidate_scores,
            key=lambda c: scoreboard_rank_key(
                c.composite_fitness,
                c.accuracy,
                # θ orders the table only where the round was won on it.
                c.theta if self.elects_on == "ability" else None,
                is_leading=c.label == self.leading_label,
                is_partial=c.outcome is ArmOutcome.SKIPPED,
            ),
            reverse=True,
        )
        return [
            ScoreboardRow(rank=i, reading=self.arm_reading(c, cut=cuts[c.label]))
            for i, c in enumerate(ranked, start=1)
        ]

    def sheet_of(self, individual_id: str) -> CellSheet:
        return InRunCells([self]).sheet(individual_id, RoleScope.DECISION)


class InRunCells:
    def __init__(self, rounds: Sequence[RoundResult]) -> None:
        self._walks = [
            walk
            for rr in rounds
            for walk in _round_walks(
                rr.opt_sp,
                head=_by_sample(rr.results),
                arms={k: _by_sample(v) for k, v in rr.all_candidate_results.items()},
                references={k: _by_sample(v) for k, v in rr.reference_results.items()},
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
    return next(rr for rr in reversed(rounds) if rr.selected_labels)


def _selection_line(selection: ArmPointer | None, pair: PairedReading) -> str:
    if selection is None:
        return "nothing selected"
    name = f"{selection.label} (round {selection.round})"
    if pair.headline is None or pair.coverage is None:
        return f"{name} — {READING_STATE_INFO[pair.state].sentence}"
    return (
        f"{name} — origin {pair.headline.rate_a:.1%} → {pair.headline.rate_b:.1%} "
        f"on {pair.coverage.scored} origin-panel cells"
    )


class RunStanding(StrictModel):
    """Where the run stands after a round: its selection, over whom, and how it reads against C0."""

    model_config = ConfigDict(frozen=True)

    rounds_without_advance: int
    # ``None`` where the campaign banks no lives (`optimization.lives`).
    stalls_left: int | None
    stalls_left_cap: int | None
    lives: LivesReading | None = Field(
        description="`stalls_left` of `stalls_left_cap`, read: the pips and their words, as the "
        "readout prints them. Null where the campaign banks no lives."
    )
    selection: ArmPointer | None = Field(
        description="The optimizer's declared pick (`declared_selection`), as the arm it was "
        "first measured as: the origin until a round selects. Null before round 0 closes."
    )
    parent: ArmPointer | None = Field(
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
    spent: CloseSpend | None = Field(
        description="What the cycle had cost at this close. Null before round 0 closes, and "
        "on a run with no ledger to fold."
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
            spent=None,
            selection_line=_selection_line(None, unread),
        )

    @classmethod
    def after(
        cls,
        rounds: Sequence[RoundOutcome],
        *,
        lives: tuple[int, int] | None,
        spent: CloseSpend | None,
    ) -> RunStanding:
        declared = declared_selection(rounds)
        picked = [rr.opt_sp.id for rr in rounds if rr.selected_labels and rr.opt_sp is not None]
        chosen = picked[-1]
        arms = {arm.candidate_id: arm for arm in pick_line(rounds)}
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
            spent=spent,
            selection_line=_selection_line(arms[chosen], vs_origin),
        )


def measured_searchpoint(
    rounds: Sequence[RoundOutcome],
    individual_id: str,
    *,
    schema: PipelineSchema,
    framing: TaskDecomposition,
    demo: Sequence[Sample],
) -> JobSearchPoint:
    for rr in reversed(rounds):
        for cs in rr.candidate_scores:
            if cs.candidate_id == individual_id and cs.resolved_pipeline_params is not None:
                return cs.searchpoint(schema=schema, framing=framing, demo=demo)
    raise KeyError(f"no closed round measured individual {individual_id}")


class CycleResult(StrictModel):
    rounds: list[RoundResult]
    # Origin-EXCLUSIVE, unlike `CycleIndex.rounds`, which holds round 0.
    n_rounds_after_origin: int
    result_accuracy: float | None
    result_round: int
    # On its OWN round-0 rows. `None`, not a 0.0 level, on a cycle that never started.
    origin: OwnLevel | None
    # θ levels of the PARENT each round ended on, never the proposals, and NOT floored at origin.
    origin_level: float | None = None
    round_levels: list[float] = Field(default_factory=list)
    # Precision only. `origin_level_se` has no reader BY DESIGN: `test_ruler.py`'s negative control.
    origin_level_se: float | None = None
    round_level_ses: list[float] = Field(default_factory=list)
    # From config, never ``len(round_levels)``: `lives` stops a stalling cycle short. ``None`` = no cap.
    round_budget: int | None = None
    result_prompt_fields: dict[str, Any]
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


class VerifyPass(StrictModel):
    """The facts a verify pass banked, never a grade of them."""

    model_config = ConfigDict(frozen=True)

    label: str
    candidate_id: str
    round: int
    sp_hash: str
    cells: list[WalkedCell]
    sample_ids: list[int]
    strategy: VerifyStrategy
    seed: int | None
    # The grader that stamped its live reading; a reader re-grades under its own.
    scorer_id: str

    def walk(self) -> IndividualWalk[WalkedCell]:
        return IndividualWalk(self.candidate_id, frozenset({MeasurementRole.VERIFY}), self.cells)


VerifyHeldAbsent = Literal["hard_picks", "under_two_fresh"]


class VerifyReading(StrictModel):
    """A verify pass under a named scorer: the candidate on fresh cells, beside its decided ones."""

    model_config = ConfigDict(frozen=True)

    label: str
    scorer_id: str = Field(
        description="The grader every number here was read under. A stored copy is a cache of "
        "that reading: a reader under another grader reads the pass again, never this."
    )
    strategy: VerifyStrategy = Field(
        description="How the fresh cells were picked: `random` from the unmeasured search pool, "
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


class ArmPanel(StrictModel):
    """The cells one arm's walk holds of the panel its round asked of it."""

    model_config = ConfigDict(frozen=True)

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
        "or under the fullest panel an arm of its round reached."
    )

    @classmethod
    def of(
        cls, scored: int | None, expected: int | None, cached: int | None, *, cut: bool
    ) -> ArmPanel:
        share = cached / scored if scored and cached is not None else None
        return cls(scored=scored, expected=expected, cached=cached, cached_share=share, cut=cut)


class ArmSpend(StrictModel):
    """What measuring one arm consumed: the BACKEND bucket alone, replayed rows excluded."""

    model_config = ConfigDict(frozen=True)

    input_tokens: int
    output_tokens: int
    prefix: PrefixReading = Field(
        description="The provider's prefix-cache reading of `input_tokens`, read rather than left "
        "as a third count for a surface to divide."
    )

    @classmethod
    def of(
        cls, input_tokens: int | None, output_tokens: int | None, cache_read_tokens: int | None
    ) -> ArmSpend | None:
        if input_tokens is None or output_tokens is None:
            return None
        account = TokenAccount(input=input_tokens, cache_read=cache_read_tokens)
        return cls(
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            prefix=account.prefix(replayed=False),
        )


class ArmAbility(StrictModel):
    """One arm's difficulty-adjusted Rasch ability on its round's joint-fit scale."""

    model_config = ConfigDict(frozen=True)

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


class ArmElection(StrictModel):
    """What one arm's round decided about it. Built by :meth:`of` alone."""

    model_config = ConfigDict(frozen=True)

    held: bool = Field(
        description="The arm's round has held its election. `selected: false` reads the same on a "
        "round still scoring and on one that crowned nobody; only this tells them apart."
    )
    selected: bool
    leading: bool = Field(
        description="The ONE arm its round is read off, as the selector named it at the close "
        "(`RoundOutcome.leading_label`); false until then."
    )
    crown: Crown | None = Field(
        description="How a selected arm advanced. Read off the electable count the round's CLOSE "
        "banks, so null on a selected arm whose round has not closed, and on every other arm."
    )

    @classmethod
    def of(
        cls,
        label: str,
        *,
        held: bool,
        selected: Collection[str],
        leading: str | None,
        electable: int | None,
    ) -> ArmElection:
        won = label in selected
        crown: Crown | None = None
        if won and electable is not None:
            crown = "elected" if electable > 1 else "uncontested"
        return cls(held=held, selected=won, leading=label == leading, crown=crown)


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
    """An arm with no ``outcome`` and no individual is a branch's stand-in row, owed no report."""
    if retired:
        return "retired"
    if reading.outcome == ArmOutcome.INVALID:
        return "invalid"
    if reading.arm.round == 0:
        return "origin"
    election = reading.election
    if election.crown is not None:
        return election.crown
    if election.selected:
        return "selected"
    if election.held:
        return "not_elected"
    unmeasured = reading.outcome is None and bool(reading.arm.candidate_id)
    return "unmeasured" if unmeasured else "awaiting"


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


class ArmReading(StrictModel):
    """One arm of one round, read: the one carrier every surface that lists or ranks arms embeds.

    Each part lands when its fact exists, so an arm in flight is this type with fewer parts set.
    """

    model_config = ConfigDict(frozen=True)

    arm: ArmPointer = Field(
        description="THE key: `(round, label)` names the arm, `candidate_id` its individual — "
        "empty only on an arm its round carried, until its score report lands."
    )
    sp_hash: str = Field(
        description="The archive address of this arm's measurements (`prompt_fields_id`). Not a "
        "key: a re-proposed configuration shares it across rounds. Empty before it is measured."
    )
    changes_description: str
    outcome: ArmOutcome | None = Field(
        description="How the arm's walk ended; null until that is decided."
    )
    own: OwnLevel | None = Field(
        description="The arm's own level on its round's cells: accuracy with the band the "
        "gateway folds per cell, the composite beside it, over `n` rows carrying a verdict. Null "
        "before the first row is graded, and on an `invalid` arm, whose scores are synthetic."
    )
    panel: ArmPanel
    spend: ArmSpend | None = Field(
        description="What measuring it consumed; null until its score report folds an account."
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
        "holds it: the one level two arms of different rounds may be differenced on. Null off "
        "the best-so-far line, and where it does not hold the whole panel."
    )

    @classmethod
    def walking(
        cls,
        arm: ArmPointer,
        *,
        fold: ScoreSummary | None,
        scored: int | None,
        expected: int | None,
        cached: int | None,
        cut: bool,
        election: ArmElection,
        changes_description: str = "",
    ) -> ArmReading:
        return cls(
            arm=arm,
            sp_hash="",
            changes_description=changes_description,
            outcome=None,
            own=None
            if fold is None
            else _own_level(
                fold.accuracy,
                fold.composite_fitness,
                (fold.mean_fitness_ci_lo, fold.mean_fitness_ci_hi),
                n=fold.total or scored or 0,
            ),
            panel=ArmPanel.of(scored, expected, cached, cut=cut),
            spend=None,
            ability=None,
            vs_reference=None,
            election=election,
            bench=None,
            verify=None,
            on_origin_panel=None,
        )

    @classmethod
    def of(
        cls,
        arm: ArmPointer,
        report: ScoredCandidate,
        *,
        cut: bool,
        election: ArmElection,
        changes_description: str,
        ability: ArmAbility | None,
        vs_reference: PairedReading | None,
    ) -> ArmReading:
        """The keyword facts are those a surface may hold a NEWER copy of than *report*."""
        return cls(
            arm=arm,
            sp_hash=report.sp_hash,
            changes_description=changes_description,
            outcome=report.outcome,
            own=None
            if report.outcome == ArmOutcome.INVALID
            else _own_level(
                report.accuracy,
                report.composite_fitness,
                (report.mean_fitness_ci_lo, report.mean_fitness_ci_hi),
                n=report.total or report.scored_samples,
            ),
            panel=ArmPanel.of(
                report.scored_samples, report.expected_samples, report.cached_samples, cut=cut
            ),
            spend=ArmSpend.of(report.input_tokens, report.output_tokens, report.cache_read_tokens),
            ability=ability,
            vs_reference=vs_reference,
            election=election,
            bench=None,
            verify=None,
            on_origin_panel=None,
        )

    def on_line(self, line: Mapping[str, LineRate]) -> ArmReading:
        return self.model_copy(update={"on_origin_panel": line.get(self.arm.candidate_id)})


def line_by_individual(overlap: OverlapReading | None) -> dict[str, LineRate]:
    """Only members holding the WHOLE panel: a rate over a shorter denominator sat a different exam."""
    if overlap is None:
        return {}
    whole = len(overlap.sample_ids)
    return {r.arm.candidate_id: r for r in overlap_line(overlap) if r.n == whole}


class DiagnosticRunRecord(StrictModel):
    """One ``noise-floor`` run's sidecar: the run-to-run spread of fresh re-scores of ONE config."""

    model_config = ConfigDict(frozen=True)

    ts: str
    dataset: str
    source_campaign: str
    source_cycle: str
    source_label: str
    source_candidate_id: str
    config_hash: str
    samples_requested: int
    samples_added: int
    workspace_n: int
    workspace_accuracy: float
    workspace_composite: float
    source_campaign_accuracy: float | None
    source_campaign_composite: float
    source_campaign_n: int
    held: bool | None = Field(
        description="Did the verdict HOLD on the wider set — `workspace_accuracy` at or above "
        "`source_campaign_accuracy`, under this layer's float tolerance. `None` where the source "
        "carries no rate to compare against. Stored rather than left to each reader: the "
        "tolerance is a decision about when two measured rates count as equal, and a surface "
        "picking its own epsilon is a surface that can disagree with this one about whether a "
        "candidate survived."
    )
    noise_floor_k: int | None = None
    noise_floor_mean: float | None = None
    noise_floor_ci_lo: float | None = None
    noise_floor_ci_hi: float | None = None
    noise_floor_raw: list[float] | None = None


def diagnostic_held(
    workspace_accuracy: float, source_campaign_accuracy: float | None
) -> bool | None:
    """The tolerance absorbs the float error of two means taken over different row counts."""
    if source_campaign_accuracy is None:
        return None
    return workspace_accuracy + 1e-9 >= source_campaign_accuracy


RescoreCount = NewType("RescoreCount", int)


def rescore_count(k: int) -> RescoreCount:
    if k < 2:
        raise ValueError(f"k={k}: one rescore has no spread to report — ask for two or more.")
    return RescoreCount(k)


# Ranks what the operator READS only: the engine's scoring order is `build_round_order`.
HardSampleOrder = Literal["info_gain", "difficulty"]
