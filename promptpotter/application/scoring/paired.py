"""A state only a pass or a run knows (stopped, pending) is the caller's, through ``absent_pair``."""

from __future__ import annotations

from collections.abc import Collection, Mapping, Sequence
from dataclasses import dataclass
from typing import NamedTuple

from promptpotter.application.scoring.formula.compiler import (
    CompiledExpression,
    ScoringFormulaError,
    cell_channels_of,
    compile_expression,
)
from promptpotter.domain.paired_reading import (
    CellSet,
    CellSetBasis,
    CellSetName,
    Coverage,
    CoverageState,
    EstimatorSpec,
    FlipCounts,
    LiftEstimate,
    Measurand,
    MeasurandKind,
    MeasuredLift,
    MemberAddress,
    PairedReading,
    PairMember,
    ReadingState,
    TestFamily,
    interval_side,
)
from promptpotter.domain.scoring import ROW_GRADES, CellSheet, GradeColumn, GradedCell
from promptpotter.shared.hashing import shapes_optimizer_prompt
from promptpotter.shared.measurement_context import RoleScope
from promptpotter.shared.statistics import (
    discordant_counts,
    holm_adjusted,
    p_floor,
    paired_mean_t,
)

__all__ = [
    "FlippedCells",
    "MemberRows",
    "absent_pair",
    "as_family",
    "flipped_keys",
    "fresh_cells",
    "grade_measurands",
    "read_pair",
]


@dataclass(frozen=True, kw_only=True)
class MemberRows:
    """A pair asked under anything but what its rows were READ under is refused, never coerced."""

    address: MemberAddress
    sheet: CellSheet
    bought: int
    cut: bool
    scope: RoleScope
    instrument_id: str
    dataset_hash: str | None
    # ``None`` where it holds whatever it measured.
    cell_set_id: str | None

    def by_key(self) -> dict[str, GradedCell]:
        return self.sheet.by_key()


def fresh_cells(sheet: CellSheet) -> int:
    return sum(1 for cell in sheet if not cell.facts.cached)


def grade_measurands(scorer_id: str) -> list[Measurand]:
    """Accuracy first: a pair's headline is its first measurand."""
    return [
        Measurand(
            kind=MeasurandKind.GRADE,
            key=key,
            scorer_id=scorer_id,
            binary=column.binary,
            unit=column.unit,
        )
        for key, column in ROW_GRADES.items()
    ]


def absent_pair(
    *,
    state: ReadingState,
    a: PairMember | None,
    b: PairMember | None,
    cell_set: CellSet | None,
    scope: RoleScope,
    spec: EstimatorSpec,
    instrument_id: str | None,
) -> PairedReading:
    return PairedReading(
        state=state,
        a=a,
        b=b,
        cell_set=cell_set,
        instrument_id=instrument_id,
        scope=scope,
        spec=spec,
        coverage=None,
        headline=None,
        beside=(),
    )


def read_pair(
    *,
    a: MemberRows,
    b: MemberRows,
    cell_set: CellSetName,
    cells: Collection[str] | None,
    masked: bool,
    dataset_hash: str | None,
    measurands: Sequence[Measurand],
    spec: EstimatorSpec,
    scope: RoleScope,
    instrument_id: str,
) -> PairedReading:
    if not measurands:
        raise ValueError("a pair is read on one measurand or more")
    readers = [_Reader(m) for m in measurands]
    held_a, held_b = a.by_key(), b.by_key()
    basis = (
        CellSetBasis.MASKED
        if masked
        else CellSetBasis.INCIDENTAL
        if cells is None
        else CellSetBasis.DECLARED
    )
    keys = held_a.keys() & held_b.keys() if cells is None else set(cells)
    named = CellSet.of(cell_set, basis, keys, dataset_hash=dataset_hash)

    read_a = {k: readers[0](held_a[k]) for k in keys & held_a.keys()}
    read_b = {k: readers[0](held_b[k]) for k in keys & held_b.keys()}
    shared = sorted(read_a.keys() & read_b.keys())
    scored = [k for k in shared if read_a[k] is not None and read_b[k] is not None]
    member_a = _member(a, read_a)
    member_b = _member(b, read_b)

    def absent(state: ReadingState) -> PairedReading:
        return absent_pair(
            state=state,
            a=member_a,
            b=member_b,
            cell_set=named,
            scope=scope,
            spec=spec,
            instrument_id=instrument_id,
        )

    members = (a, b)
    if any(m.scope is not scope for m in members):
        return absent(ReadingState.SCOPE_DIFFERS)
    if any(m.instrument_id != instrument_id for m in members):
        return absent(ReadingState.INSTRUMENT_DIFFERS)
    if dataset_hash is not None and any(
        m.dataset_hash is not None and m.dataset_hash != dataset_hash for m in members
    ):
        return absent(ReadingState.DATASET_DIFFERS)
    # A member holding no cell was graded under nothing, and differs from no measurand.
    if any(
        m.sheet and m.sheet.scorer_id != asked.scorer_id for m in members for asked in measurands
    ):
        return absent(ReadingState.MEASURAND_DIFFERS)
    if basis is CellSetBasis.DECLARED and any(
        m.cell_set_id is not None and m.cell_set_id != named.id for m in members
    ):
        return absent(ReadingState.CELL_SET_DIFFERS)
    if a.address == b.address:
        return absent(ReadingState.SAME_INDIVIDUAL)
    if not named.size:
        # Two members sharing nothing hold no set to be empty: that is a width, not a split.
        return absent(
            ReadingState.UNDER_TWO_CELLS
            if basis is CellSetBasis.INCIDENTAL
            else ReadingState.NOT_HELD
        )
    if not member_a.n or not member_b.n:
        return absent(ReadingState.MEMBER_UNSCOREABLE)
    if len(scored) < 2:
        return absent(ReadingState.UNDER_TWO_CELLS)

    unscored = sum(
        1
        for k in shared
        if any(
            value is None and (cell.grade.unscored is not None or not cell.facts.errored)
            for value, cell in ((read_a[k], held_a[k]), (read_b[k], held_b[k]))
        )
    )
    lifts = [
        _lift(
            measurand,
            [_owed(read(held_a[k]), measurand) for k in scored],
            [_owed(read(held_b[k]), measurand) for k in scored],
            spec,
        )
        for measurand, read in zip(measurands, readers, strict=True)
    ]
    return PairedReading(
        state=ReadingState.READ,
        a=member_a,
        b=member_b,
        cell_set=named,
        instrument_id=instrument_id,
        scope=scope,
        spec=spec,
        coverage=Coverage(
            state=(
                CoverageState.INCIDENTAL
                if basis is CellSetBasis.INCIDENTAL
                else CoverageState.COMPLETE
                if len(scored) == named.size
                else CoverageState.SELECTED_BY_STOP
                if a.cut or b.cut
                else CoverageState.PARTIAL
            ),
            shared=len(shared),
            scored=len(scored),
            excluded_unscored=unscored,
            excluded_faulted=len(shared) - len(scored) - unscored,
        ),
        headline=lifts[0],
        beside=tuple(lifts[1:]),
    )


def as_family(readings: Sequence[PairedReading], family_id: str) -> list[PairedReading]:
    """A reading that was not read was not tested: it carries no family and counts for none."""
    tested = [r.headline for r in readings if r.headline is not None]
    adjusted = iter(holm_adjusted([lift.estimate.p_value for lift in tested]))
    return [
        r
        if r.headline is None
        else r.model_copy(
            update={
                "headline": r.headline.model_copy(
                    update={
                        "family": TestFamily(
                            id=family_id,
                            n_tests=len(tested),
                            method="holm",
                            p_adjusted=next(adjusted),
                        )
                    }
                )
            }
        )
        for r in readings
    ]


class FlippedCells[K](NamedTuple):
    """``kept`` are `a`'s hits `b` hit too; a cell both missed is none of the three."""

    gained: list[K]
    lost: list[K]
    kept: list[K]


@shapes_optimizer_prompt
def flipped_keys(a: Mapping[str, GradedCell], b: Mapping[str, GradedCell]) -> FlippedCells[str]:
    hit = ROW_GRADES["hit"]
    flipped = FlippedCells[str]([], [], [])
    for key, cell in b.items():
        was = _graded(hit, a[key]) if key in a else None
        now = _graded(hit, cell)
        if was is None or now is None:
            continue
        if now > was:
            flipped.gained.append(key)
        elif was:
            (flipped.kept if now else flipped.lost).append(key)
    return flipped


@shapes_optimizer_prompt
def _graded(grade: GradeColumn, cell: GradedCell) -> float | None:
    return grade.read(cell) if cell.scored else None


def _member(member: MemberRows, read: Mapping[str, float | None]) -> PairMember:
    return PairMember(
        address=member.address,
        n=sum(1 for value in read.values() if value is not None),
        bought=member.bought,
    )


class _Reader:
    def __init__(self, measurand: Measurand) -> None:
        self._expression: CompiledExpression | None = None
        self._grade: GradeColumn | None = None
        if measurand.kind is MeasurandKind.GRADE:
            grade = ROW_GRADES.get(measurand.key)
            if grade is None or grade.binary is not measurand.binary:
                raise ValueError(f"{measurand.key!r} is not a grade a row carries as declared")
            self._grade = grade
        else:
            self._expression = compile_expression(measurand.key, source="paired measurand")

    def __call__(self, cell: GradedCell) -> float | None:
        if self._grade is not None:
            return _graded(self._grade, cell)
        assert self._expression is not None
        try:
            channels = cell_channels_of(cell.facts, cell.grade.fitness)
            return self._expression.evaluate(channels, "this cell")
        except ScoringFormulaError:
            return None


def _owed(value: float | None, measurand: Measurand) -> float:
    if value is None:
        raise ValueError(
            f"measurand {measurand.key!r} cannot be read on every cell the headline scored: "
            "it is its own reading"
        )
    return value


def _lift(
    measurand: Measurand, a_values: list[float], b_values: list[float], spec: EstimatorSpec
) -> MeasuredLift:
    n = len(a_values)
    flips: FlipCounts | None = None
    if measurand.binary:
        if not {*a_values, *b_values} <= {0.0, 1.0}:
            raise ValueError(f"measurand {measurand.key!r} is declared binary and is not")
        gained, lost = discordant_counts(b_values, a_values)
        flips = FlipCounts(gained=gained, lost=lost, unchanged=n - gained - lost)
    # Tested against the null by shifting it out, so the estimate stays `rate_b - rate_a`.
    against_null = [value - spec.null_value for value in b_values]
    mean, lo, hi, p, _ = paired_mean_t(against_null, a_values, alpha=spec.alpha)
    assert lo is not None and hi is not None and p is not None
    ci_lo, ci_hi = lo + spec.null_value, hi + spec.null_value
    floor = p_floor(against_null, a_values)
    return MeasuredLift(
        measurand=measurand,
        rate_a=sum(a_values) / n,
        rate_b=sum(b_values) / n,
        estimate=LiftEstimate(
            value=mean + spec.null_value,
            ci_lo=ci_lo,
            ci_hi=ci_hi,
            side=interval_side(ci_lo, ci_hi, spec.null_value, p_floor=floor, alpha=spec.alpha),
            p_value=max(p, floor),
            p_floor=floor,
        ),
        flips=flips,
        family=None,
    )
