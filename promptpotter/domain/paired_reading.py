"""Imports no sibling that reads a round; no ``@computed_field``, since a reading is banked on ``extra="forbid"``."""

from __future__ import annotations

import math
from collections.abc import Collection
from enum import StrEnum
from typing import Annotated, Literal, NamedTuple, Self

from pydantic import ConfigDict, Field, model_validator

from promptpotter.domain.cycle_paths import CycleHop
from promptpotter.domain.l4.inner_origin import inner_origin_of
from promptpotter.domain.strict_model import StrictModel
from promptpotter.shared.hashing import shapes_optimizer_prompt, stable_hash
from promptpotter.shared.measurement_context import MeasurementRole, RoleScope

__all__ = [
    "READING_STATE_INFO",
    "ROUND_LIFT_SPEC",
    "ArmPointer",
    "CellSet",
    "CellSetBasis",
    "CellSetName",
    "Coverage",
    "CoverageState",
    "EstimatorSpec",
    "FlipCounts",
    "IntervalMethod",
    "LiftEstimate",
    "LiftReference",
    "LiftSide",
    "Measurand",
    "MeasurandKind",
    "MeasurandUnit",
    "MeasuredLift",
    "MemberAddress",
    "PairMember",
    "PairedReading",
    "ReadingState",
    "ReadingStateInfo",
    "ReadingStateKind",
    "TestFamily",
    "instrument_of",
    "interval_side",
]


class ReadingStateKind(StrEnum):
    READ = "read"
    # Nothing is wrong: the read has not been asked for, or has not landed.
    WAITING = "waiting"
    # No pair exists here to read, so none was owed: a re-run meets the same answer.
    UNPAIRED = "unpaired"
    # A pair exists and its reading was owed: a member's cells never arrived, or do not suffice.
    ABSENT = "absent"
    # Both members hold cells, and differencing them would mean nothing.
    REFUSED = "refused"


class ReadingState(StrEnum):
    """Whether a pair was read, and the ONE reason it was not."""

    READ = "read"
    NOT_ASKED = "not_asked"
    PENDING = "pending"
    NOT_HELD = "not_held"
    HELD_ELSEWHERE = "held_elsewhere"
    PASS_STOPPED = "pass_stopped"
    PAST_TOLERANCE = "past_tolerance"
    MEMBER_UNSCOREABLE = "member_unscoreable"
    UNDER_TWO_CELLS = "under_two_cells"
    NO_SELECTION = "no_selection"
    # One individual on one pass. Never a 0.0 lift: nothing was compared.
    SAME_INDIVIDUAL = "same_individual"
    RUN_FAILED = "run_failed"
    SCOPE_DIFFERS = "scope_differs"
    MEASURAND_DIFFERS = "measurand_differs"
    DATASET_DIFFERS = "dataset_differs"
    CELL_SET_DIFFERS = "cell_set_differs"
    INSTRUMENT_DIFFERS = "instrument_differs"


class ReadingStateInfo(NamedTuple):
    """``label`` and ``sentence`` are SERVED: no surface words a state of its own."""

    kind: ReadingStateKind
    label: str
    sentence: str


# Marked: a state's ``kind`` decides the round's advance, and so the stall a prompt reports.
READING_STATE_INFO: Annotated[dict[ReadingState, ReadingStateInfo], shapes_optimizer_prompt] = {
    ReadingState.READ: ReadingStateInfo(
        ReadingStateKind.READ, "read", "Read on the cells both members scored."
    ),
    ReadingState.NOT_ASKED: ReadingStateInfo(
        ReadingStateKind.WAITING, "not asked", "Nobody has asked for this reading yet."
    ),
    ReadingState.PENDING: ReadingStateInfo(
        ReadingStateKind.WAITING, "pending", "The pass this reading waits on has not finished."
    ),
    ReadingState.NOT_HELD: ReadingStateInfo(
        ReadingStateKind.UNPAIRED,
        "no cells held",
        "The cell set this reading is taken on holds no cell.",
    ),
    ReadingState.HELD_ELSEWHERE: ReadingStateInfo(
        ReadingStateKind.UNPAIRED,
        "held elsewhere",
        "The cycle holding this line takes the reading, not this one.",
    ),
    ReadingState.PASS_STOPPED: ReadingStateInfo(
        ReadingStateKind.ABSENT,
        "pass stopped",
        "The pass that measures a member stopped before it finished.",
    ),
    ReadingState.PAST_TOLERANCE: ReadingStateInfo(
        ReadingStateKind.ABSENT,
        "past tolerance",
        "A member's pass lost more cells than its tolerance allows.",
    ),
    ReadingState.MEMBER_UNSCOREABLE: ReadingStateInfo(
        ReadingStateKind.ABSENT, "unscoreable", "A member holds no scoreable cell on this set."
    ),
    ReadingState.UNDER_TWO_CELLS: ReadingStateInfo(
        ReadingStateKind.ABSENT,
        "under two cells",
        "Fewer than two cells were scored by both members, so nothing was tested.",
    ),
    ReadingState.NO_SELECTION: ReadingStateInfo(
        ReadingStateKind.UNPAIRED,
        "no selection",
        "No individual was selected, so there is nothing to pair.",
    ),
    ReadingState.SAME_INDIVIDUAL: ReadingStateInfo(
        ReadingStateKind.UNPAIRED,
        "same individual",
        "Both members are one individual on one pass, so there is no difference to read.",
    ),
    ReadingState.RUN_FAILED: ReadingStateInfo(
        ReadingStateKind.ABSENT, "run failed", "The run failed before this reading was taken."
    ),
    ReadingState.SCOPE_DIFFERS: ReadingStateInfo(
        ReadingStateKind.REFUSED, "scopes differ", "The members were read in different scopes."
    ),
    ReadingState.MEASURAND_DIFFERS: ReadingStateInfo(
        ReadingStateKind.REFUSED,
        "scorers differ",
        "The members were graded by different scorers.",
    ),
    ReadingState.DATASET_DIFFERS: ReadingStateInfo(
        ReadingStateKind.REFUSED,
        "datasets differ",
        "The members were measured on different datasets.",
    ),
    ReadingState.CELL_SET_DIFFERS: ReadingStateInfo(
        ReadingStateKind.REFUSED,
        "cell sets differ",
        "The members were sent on different declared cell sets.",
    ),
    ReadingState.INSTRUMENT_DIFFERS: ReadingStateInfo(
        ReadingStateKind.REFUSED,
        "instruments differ",
        "The members were measured by different instruments.",
    ),
}
assert READING_STATE_INFO.keys() == set(ReadingState)


LiftSide = Literal["above", "below", "spans"]


#: `best_so_far`: the standing best re-scored on the round's panel; `parents`: the better parent on the arm's cells.
LiftReference = Literal["best_so_far", "parents"]


def instrument_of(dataset_name: str, pipeline_params: object) -> str:
    """*pipeline_params* is any searchpoint of the cycle: the stamp is the cycle's."""
    inner = inner_origin_of(pipeline_params)
    return dataset_name if inner is None else f"{dataset_name}@{inner}"


def interval_side(
    lo: float, hi: float, null_value: float, *, p_floor: float, alpha: float
) -> LiftSide:
    """`spans` wherever *p_floor* is above *alpha*: too few cells differ for any test to reach it."""
    if p_floor > alpha:
        return "spans"
    return "above" if lo > null_value else "below" if hi < null_value else "spans"


class ArmPointer(StrictModel):
    """One arm of the run, named the three ways a surface joins on it."""

    model_config = ConfigDict(frozen=True)

    round: int = Field(description="The round the arm was measured in; 0 is the origin.")
    label: str = Field(
        description="`C0` or `C{round}.{n}` — the ARM's key; an id names its individual."
    )
    candidate_id: str = Field(description="The individual's lineage id.")


class MemberAddress(StrictModel):
    """Where one member of a pair was read: the cycle, the individual, and the pass."""

    model_config = ConfigDict(frozen=True)

    path: tuple[CycleHop, ...] = Field(
        min_length=1, description="The cycle the member was read in, root to leaf."
    )
    individual_id: str = Field(description="The individual's content id.")
    arm: ArmPointer | None = Field(
        description="The arm the individual was measured as; null for a pass that is no arm."
    )
    pass_role: MeasurementRole | None = Field(
        description="Which pass, where the same individual is read by more than one."
    )


class PairMember(StrictModel):
    """One side of a pair, and what it holds on the pair's cell set."""

    model_config = ConfigDict(frozen=True)

    address: MemberAddress
    n: int = Field(description="Cells of the set this member holds a scoreable row on.")
    bought: int = Field(
        description="Cells the round that took this reading measured fresh on this member; a "
        "replayed cell is not one."
    )


class CellSetName(StrEnum):
    ORIGIN_PANEL = "origin_panel"
    REFERENCE_CELLS = "reference_cells"
    BENCH_SPLIT = "bench_split"
    MEASURED_BY_BOTH = "measured_by_both"


class CellSetBasis(StrEnum):
    # Named before either member was measured on it.
    DECLARED = "declared"
    # Whatever both members happen to hold.
    INCIDENTAL = "incidental"
    MASKED = "masked"


class CellSet(StrictModel):
    """The cells a pair is read on, as an identity two readings can be compared by."""

    model_config = ConfigDict(frozen=True)

    name: CellSetName
    basis: CellSetBasis
    id: str = Field(description="Digest of the sorted sample keys the set names.")
    dataset_hash: str | None
    size: int = Field(description="How many cells the set names — the N of `n of N`.")

    @model_validator(mode="after")
    def _basis_fits_name(self) -> Self:
        incidental_name = self.name is CellSetName.MEASURED_BY_BOTH
        if (self.basis is CellSetBasis.DECLARED and incidental_name) or (
            self.basis is CellSetBasis.INCIDENTAL and not incidental_name
        ):
            raise ValueError(f"a {self.basis.value} cell set cannot be {self.name.value}")
        return self

    @classmethod
    def of(
        cls,
        name: CellSetName,
        basis: CellSetBasis,
        keys: Collection[str],
        *,
        dataset_hash: str | None,
    ) -> CellSet:
        """*keys* are ``Sample.key`` content addresses, so a set re-cut or re-numbered elsewhere keeps its id."""
        members = sorted(set(keys))
        return cls(
            name=name,
            basis=basis,
            id=stable_hash(members),
            dataset_hash=dataset_hash,
            size=len(members),
        )


class MeasurandKind(StrEnum):
    GRADE = "grade"
    CHANNEL = "channel"
    EXPR = "expr"


class MeasurandUnit(StrEnum):
    RATE = "rate"
    SCORE = "score"


class Measurand(StrictModel):
    """What is read off each cell, and under which scorer."""

    model_config = ConfigDict(frozen=True)

    kind: MeasurandKind
    key: str = Field(description="The grade, the channel name or the expression text.")
    scorer_id: str
    binary: bool = Field(
        description="DECLARED, never inferred from the values: every cell is 0 or 1, so a "
        "cell can flip."
    )
    unit: MeasurandUnit


class IntervalMethod(StrEnum):
    STUDENT_T = "student_t"


class EstimatorSpec(StrictModel):
    """How a lift's two-sided interval and test were taken; the estimate is the mean paired difference."""

    model_config = ConfigDict(frozen=True)

    interval_method: IntervalMethod
    alpha: float
    null_value: float = Field(description="The difference the test and `side` are read against.")


#: One spec for every lift a round banks, so each arm's and the pick's are read on one bar.
ROUND_LIFT_SPEC = EstimatorSpec(
    interval_method=IntervalMethod.STUDENT_T, alpha=0.05, null_value=0.0
)


class LiftEstimate(StrictModel):
    """A mean paired difference with its interval and test."""

    model_config = ConfigDict(frozen=True)

    value: float
    ci_lo: float
    ci_hi: float
    side: LiftSide
    p_value: float = Field(
        description="The Student-t p, never below `p_floor`: where the two are equal the floor "
        "binds, and the cells that differ are what limits the claim."
    )
    p_floor: float = Field(
        description="The smallest p an exact sign test can reach on the cells that differ "
        "from the null. Above the spec's `alpha`, `side` is `spans` whatever the interval says."
    )


class TestFamily(StrictModel):
    """The set of tests one multiple-comparison correction was taken across."""

    model_config = ConfigDict(frozen=True)

    id: str
    n_tests: int
    method: Literal["holm"]
    p_adjusted: float


class FlipCounts(StrictModel):
    """What moving from member `a` to member `b` did cell by cell, on a binary measurand."""

    model_config = ConfigDict(frozen=True)

    gained: int = Field(description="Cells `a` missed and `b` hit.")
    lost: int = Field(description="Cells `a` hit and `b` missed.")
    unchanged: int


class MeasuredLift(StrictModel):
    """One measurand's lift of member `b` over member `a`, on the cells both scored."""

    model_config = ConfigDict(frozen=True)

    measurand: Measurand
    rate_a: float = Field(description="`a`'s mean over the cells BOTH scored, never its own level.")
    rate_b: float
    estimate: LiftEstimate
    flips: FlipCounts | None
    family: TestFamily | None

    @model_validator(mode="after")
    def _one_population(self) -> Self:
        if not math.isclose(self.rate_b - self.rate_a, self.estimate.value, abs_tol=1e-9):
            raise ValueError("a lift is rate_b - rate_a over the cells both members scored")
        if (self.flips is not None) != self.measurand.binary:
            raise ValueError("flips exist exactly where the measurand is declared binary")
        return self


class CoverageState(StrEnum):
    COMPLETE = "complete"
    PARTIAL = "partial"
    # Cut by an eliminator or an operator: the subset is OUTCOME-selected, and pairing does not rescue it.
    SELECTED_BY_STOP = "selected_by_stop"
    INCIDENTAL = "incidental"


class Coverage(StrictModel):
    """How much of the cell set a lift stands on."""

    model_config = ConfigDict(frozen=True)

    state: CoverageState
    shared: int = Field(description="Cells of the set both members hold a row on.")
    scored: int = Field(description="Shared cells both scored — the n of every lift here.")
    excluded_unscored: int = Field(
        description="Shared cells dropped because the formula could not grade a member's row."
    )
    excluded_faulted: int = Field(
        description="Shared cells dropped because a member's row errored."
    )

    @model_validator(mode="after")
    def _partitions_shared(self) -> Self:
        if self.scored + self.excluded_unscored + self.excluded_faulted != self.shared:
            raise ValueError("scored and the two exclusions partition the shared cells")
        return self


class PairedReading(StrictModel):
    """One pair of addressed individuals read on one cell set."""

    model_config = ConfigDict(frozen=True)

    state: ReadingState
    a: PairMember | None
    b: PairMember | None
    cell_set: CellSet | None
    instrument_id: str | None = Field(
        description="What measured both members' cells: their dataset, and on the recursion "
        "the inner origin. Null where the reading names no pair."
    )
    scope: RoleScope
    spec: EstimatorSpec
    coverage: Coverage | None
    headline: MeasuredLift | None
    beside: tuple[MeasuredLift, ...] = Field(
        description="Other measurands over the SAME scored cells as `headline`."
    )

    @classmethod
    def unread(cls, state: ReadingState) -> PairedReading:
        return cls(
            state=state,
            a=None,
            b=None,
            cell_set=None,
            instrument_id=None,
            scope=RoleScope.REPORT,
            spec=ROUND_LIFT_SPEC,
            coverage=None,
            headline=None,
            beside=(),
        )

    @property
    def on_whole_set(self) -> MeasuredLift | None:
        """Only here is ``rate_a`` `a`'s level on the set: elsewhere it is over the cells `b` reached."""
        whole = self.coverage is not None and self.coverage.state is CoverageState.COMPLETE
        return self.headline if whole else None

    def lift(self, key: str) -> MeasuredLift | None:
        if self.headline is None:
            return None
        return next(
            (lift for lift in (self.headline, *self.beside) if lift.measurand.key == key), None
        )

    def reference_level(self, key: str) -> float | None:
        lift = self.lift(key) if self.on_whole_set is not None else None
        return None if lift is None else lift.rate_a

    @model_validator(mode="after")
    def _state_fits_content(self) -> Self:
        if self.headline is None:
            if self.state is ReadingState.READ or self.beside or self.coverage is not None:
                raise ValueError("a lift and its coverage exist exactly where the state is read")
            return self
        if self.state is not ReadingState.READ:
            raise ValueError("a lift exists exactly where the state is read")
        if self.a is None or self.b is None or self.cell_set is None or self.coverage is None:
            raise ValueError("a read pair names both members, its cell set and its coverage")
        if not 2 <= self.coverage.scored <= self.coverage.shared <= self.cell_set.size:
            raise ValueError("a read pair scored two or more of the cells its set names")
        for lift in (self.headline, *self.beside):
            estimate = lift.estimate
            if estimate.p_value < estimate.p_floor or estimate.side != interval_side(
                estimate.ci_lo,
                estimate.ci_hi,
                self.spec.null_value,
                p_floor=estimate.p_floor,
                alpha=self.spec.alpha,
            ):
                raise ValueError(
                    "a lift's side is read off its own bounds, floor and the spec's null, and "
                    "its p is never below that floor"
                )
            if lift.flips is not None and (
                lift.flips.gained + lift.flips.lost + lift.flips.unchanged != self.coverage.scored
            ):
                raise ValueError("flips partition the scored cells")
        return self
