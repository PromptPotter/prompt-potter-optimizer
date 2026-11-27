from __future__ import annotations

from collections.abc import Collection, Mapping, Sequence
from enum import StrEnum
from typing import Literal, NamedTuple, assert_never

from pydantic import Field

from promptpotter.domain.arms import ArmState, Measured, walked_short
from promptpotter.domain.cell_fold import measured_cells
from promptpotter.domain.paired_reading import (
    ArmName,
    MeasurandUnit,
    PairedReading,
    ReadingState,
    ReadingStateKind,
    UnreadPair,
    UnreadState,
)
from promptpotter.domain.scoring import CellSheet
from promptpotter.domain.strict_model import FrozenModel
from promptpotter.shared.measurement_context import MeasurementRole
from promptpotter.shared.worded import Worded


class StallEffect(StrEnum):
    STARTS = "starts"
    RESETS = "resets"
    COUNTS = "counts"
    SKIPS = "skips"


class RoundEnding(StrEnum):
    """How a round's election ended: on the origin round 0 adopts, on the arm it crowned, on its
    uncrowned leader the line moved to, or held on its parent."""

    ADOPTED = "adopted"
    CROWNED = "crowned"
    MOVED = "moved"
    HELD = "held"

    @property
    def moves_line(self) -> bool:
        """Off the round before it: an adopted origin starts the line, a held round stands on it."""
        return self in (RoundEnding.CROWNED, RoundEnding.MOVED)


#: What a crown the origin panel does not separate from the origin does to the stall
#: (`OptimizationConfig.not_separated_stall`).
NotSeparatedStall = Literal["resets", "counts", "skips"]


# Marked: `OverlapReading.stall` is the ONE pacing rule L1 patience, `rounds_without_advance` and
# the life bank read; the words (`ROUND_ADVANCE_INFO`) reach a closure only where a prompt prints one.
class RoundAdvance(StrEnum):
    """Whether a closed round ADVANCED the best-so-far line."""

    ORIGIN = "origin"
    ADVANCED = "advanced"
    ADVANCED_UNPAIRED = "advanced_unpaired"
    NOT_SEPARATED = "not_separated"
    MOVED_UNCROWNED = "moved_uncrowned"
    HELD = "held"
    UNREAD = "unread"

    def stall(self, not_separated: NotSeparatedStall | None) -> StallEffect:
        """*not_separated* is read by ``NOT_SEPARATED`` alone, which only a read lead closes."""
        match self:
            case RoundAdvance.ORIGIN:
                return StallEffect.STARTS
            case RoundAdvance.ADVANCED | RoundAdvance.ADVANCED_UNPAIRED:
                return StallEffect.RESETS
            case RoundAdvance.NOT_SEPARATED:
                assert not_separated is not None, "a read lead closes on the switch's effect"
                return StallEffect(not_separated)
            case RoundAdvance.MOVED_UNCROWNED | RoundAdvance.HELD:
                return StallEffect.COUNTS
            case RoundAdvance.UNREAD:
                return StallEffect.SKIPS
            case _:
                assert_never(self)


class RoundAdvanceInfo(NamedTuple):
    label: str
    sentence: str


ROUND_ADVANCE_INFO: Worded[RoundAdvance, RoundAdvanceInfo] = Worded(
    {
        RoundAdvance.ORIGIN: RoundAdvanceInfo(
            "origin", "Round 0 measured the origin every later pick is read against."
        ),
        RoundAdvance.ADVANCED: RoundAdvanceInfo(
            "advanced",
            "The pick's lift over the origin clears zero on the origin panel.",
        ),
        RoundAdvance.ADVANCED_UNPAIRED: RoundAdvanceInfo(
            "advanced, unpaired",
            "An arm was promoted, and the origin panel held nothing to pair it with the origin on.",
        ),
        RoundAdvance.NOT_SEPARATED: RoundAdvanceInfo(
            "not separated",
            "An arm was promoted, and on the origin panel it has not separated from the origin and "
            "every earlier pick.",
        ),
        RoundAdvance.MOVED_UNCROWNED: RoundAdvanceInfo(
            "moved, uncrowned",
            "No arm was promoted; the line moved on to the round's leader, which the next round "
            "mutates from, while the pick stays the last one promoted.",
        ),
        RoundAdvance.HELD: RoundAdvanceInfo(
            "held", "No arm was promoted, and the line stands on its parent."
        ),
        RoundAdvance.UNREAD: RoundAdvanceInfo(
            "unread",
            "An arm was promoted, and the origin-panel reading owed on it was not taken: the round "
            "neither advances the line nor counts as a stall.",
        ),
    }
)
assert ROUND_ADVANCE_INFO.keys() == set(RoundAdvance)


def round_advance(ending: RoundEnding, lead: PairedReading) -> RoundAdvance:
    """Only *lead* (pick over C0, origin panel) advances the line, separated where its interval clears."""
    if ending is RoundEnding.ADOPTED:
        return RoundAdvance.ORIGIN
    if ending is not RoundEnding.CROWNED:
        return RoundAdvance.MOVED_UNCROWNED if ending.moves_line else RoundAdvance.HELD
    match lead.state.kind:
        case ReadingStateKind.UNPAIRED:
            return RoundAdvance.ADVANCED_UNPAIRED
        case ReadingStateKind.WAITING | ReadingStateKind.ABSENT | ReadingStateKind.REFUSED:
            return RoundAdvance.UNREAD
        case ReadingStateKind.READ:
            assert lead.state is ReadingState.READ
            # ONE interval read: the pick over C0, never a point compare beside it.
            separated = lead.headline.side == "above"
            return RoundAdvance.ADVANCED if separated else RoundAdvance.NOT_SEPARATED


class PanelTrigger(StrEnum):
    """Why an arm off the line is read on the origin panel: a monitoring call, except under the
    election's ``vote: overlap``, where it is the verdict: only an arm it seats there is electable."""

    N_MIN = "n_min"


def panel_trigger(arm: ArmState, rows: CellSheet, n_min: int) -> PanelTrigger | None:
    """Why the origin panel reads *arm*, a proposal walked to its draw's end, off its draw's *rows*."""
    if (
        not isinstance(arm, Measured)
        or walked_short(arm)
        or arm.role is MeasurementRole.PARENT
        or len(measured_cells(rows)) < n_min
    ):
        return None
    return PanelTrigger.N_MIN


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
    vs_previous_pick: PairedReading | None = Field(
        description="The standing pick (`b`) over the pick before it on the line (`a`), on the "
        "same panel: the cells it gained and lost against the last pick it displaced. Null where "
        "that pick is C0, which `lead` reads, or where the line went unread. It moves no "
        "`advance`."
    )
    advance: RoundAdvance
    stall: StallEffect = Field(
        description="What `advance` does to the stall clock L1 patience, `rounds_without_advance` "
        "and the life bank read, stamped at the close under `not_separated_stall`."
    )

    @classmethod
    def of(
        cls,
        ending: RoundEnding,
        *,
        positions: Sequence[int],
        lead: PairedReading,
        earlier: Sequence[PairedReading],
        triggered: Sequence[TriggeredReading],
        vs_previous_pick: PairedReading | None,
        not_separated: NotSeparatedStall | None,
    ) -> OverlapReading:
        advance = round_advance(ending, lead)
        return cls(
            positions=tuple(positions),
            lead=lead,
            earlier=tuple(earlier),
            triggered=tuple(triggered),
            vs_previous_pick=vs_previous_pick,
            advance=advance,
            stall=advance.stall(not_separated),
        )

    @classmethod
    def unpaired(cls, state: UnreadState, ending: RoundEnding) -> OverlapReading:
        """No lead is read, so no round closes ``NOT_SEPARATED`` and no switch is read."""
        return cls.of(
            ending,
            positions=(),
            lead=UnreadPair.of(state),
            earlier=(),
            triggered=(),
            vs_previous_pick=None,
            not_separated=None,
        )


class LineRate(FrozenModel):
    """One member of the best-so-far line as an overlap reading holds it."""

    arm: ArmName
    rate: float = Field(description="Its level over the panel cells it and C0 both scored.")
    unit: MeasurandUnit = Field(description="The unit `rate` reads in: its headline's.")
    n: int = Field(description="How many of the panel's cells it holds scoreable.")


def overlap_line(overlap: OverlapReading) -> list[LineRate]:
    """C0 is on the lead's reading of it, before any pick on the first triggered one's."""
    anchor = next(
        (
            reading
            for reading in (overlap.lead, *(t.reading for t in overlap.triggered))
            if reading.state is ReadingState.READ
        ),
        None,
    )
    if anchor is None or anchor.a.address.arm is None:
        return []
    head = anchor.headline
    line = [
        LineRate(arm=anchor.a.address.arm, rate=head.rate_a, unit=head.measurand.unit, n=anchor.a.n)
    ]
    for pick in (*overlap.earlier, overlap.lead):
        if pick.state is ReadingState.READ and (arm := pick.b.address.arm) is not None:
            lift = pick.headline
            line.append(LineRate(arm=arm, rate=lift.rate_b, unit=lift.measurand.unit, n=pick.b.n))
    return line


def triggered_rates(overlap: OverlapReading) -> list[LineRate]:
    return [
        LineRate(arm=arm, rate=read.headline.rate_b, unit=read.headline.measurand.unit, n=read.b.n)
        for t in overlap.triggered
        if (read := t.reading).state is ReadingState.READ
        and (arm := read.b.address.arm) is not None
    ]


def line_by_individual(overlap: OverlapReading | None) -> dict[str, LineRate]:
    """Only members holding the WHOLE panel: a rate over a shorter denominator sat a different exam."""
    if overlap is None:
        return {}
    whole = len(overlap.positions)
    rates = (*triggered_rates(overlap), *overlap_line(overlap))
    return {r.arm.key: r for r in rates if r.n == whole}


def origin_panel(
    origin_cells: Sequence[int],
    *,
    line_cells: Collection[int],
    poolable: Collection[int],
    benchmark_of: Mapping[int, str | None],
    size: int,
) -> list[int]:
    """*origin_cells* are the origin's own ROUND's in walk order, never every cell it was later read
    on: its first cells are the ones the next round's draw walks first, so the panel costs no top-up
    there, and a resumed round rebuilds it off the same banked rows."""
    by_benchmark: dict[str | None, list[int]] = {}
    pool = set(poolable)
    for sid in (sid for sid in dict.fromkeys(origin_cells) if sid in pool):
        by_benchmark.setdefault(benchmark_of[sid], []).append(sid)
    sat = set(by_benchmark)
    for sid in sorted(set(line_cells) & set(poolable)):
        if benchmark_of[sid] not in sat:
            by_benchmark.setdefault(benchmark_of[sid], []).append(sid)
    return sorted(sid for cells in by_benchmark.values() for sid in cells[:size])
