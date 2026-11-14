from __future__ import annotations

from collections.abc import Collection, Mapping, Sequence
from enum import StrEnum
from typing import NamedTuple, assert_never

from pydantic import Field

from promptpotter.domain.crowning_bar import CrowningBar
from promptpotter.domain.paired_reading import (
    ArmName,
    MeasurandUnit,
    PairedReading,
    ReadingState,
    ReadingStateKind,
)
from promptpotter.domain.strict_model import FrozenModel
from promptpotter.shared.worded import Worded


class StallEffect(StrEnum):
    STARTS = "starts"
    RESETS = "resets"
    COUNTS = "counts"
    SKIPS = "skips"


# Marked: `stall` is the ONE pacing rule L1 patience, `rounds_without_advance` and the life bank
# read; the words (`ROUND_ADVANCE_INFO`) reach a closure only where a prompt prints one.
class RoundAdvance(StrEnum):
    """Whether a closed round ADVANCED the best-so-far line."""

    ORIGIN = "origin"
    ADVANCED = "advanced"
    ADVANCED_UNPAIRED = "advanced_unpaired"
    NOT_SEPARATED = "not_separated"
    HELD = "held"
    UNREAD = "unread"

    @property
    def stall(self) -> StallEffect:
        match self:
            case RoundAdvance.ORIGIN:
                return StallEffect.STARTS
            case (
                RoundAdvance.ADVANCED | RoundAdvance.ADVANCED_UNPAIRED | RoundAdvance.NOT_SEPARATED
            ):
                return StallEffect.RESETS
            case RoundAdvance.HELD:
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
            "The pick's lift over the origin clears zero on the origin panel, and its rate there "
            "tops every earlier pick's.",
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
        RoundAdvance.HELD: RoundAdvanceInfo("held", "No arm was promoted."),
        RoundAdvance.UNREAD: RoundAdvanceInfo(
            "unread",
            "An arm was promoted, and the origin-panel reading owed on it was not taken: the round "
            "neither advances the line nor counts as a stall.",
        ),
    }
)
assert ROUND_ADVANCE_INFO.keys() == set(RoundAdvance)


def round_advance(
    round_num: int,
    improved: bool,
    lead: PairedReading,
    bar: CrowningBar | None,
) -> RoundAdvance:
    """*improved* is the bar's promotion; only *lead* (pick over C0, origin panel) moves the line,
    separated as the *bar*'s switch reads it."""
    if round_num == 0:
        return RoundAdvance.ORIGIN
    if not improved:
        return RoundAdvance.HELD
    match lead.state.kind:
        case ReadingStateKind.UNPAIRED:
            return RoundAdvance.ADVANCED_UNPAIRED
        case ReadingStateKind.WAITING | ReadingStateKind.ABSENT | ReadingStateKind.REFUSED:
            return RoundAdvance.UNREAD
        case ReadingStateKind.READ:
            lift = lead.headline
            assert lift is not None and bar is not None, "a read pair carries its lift and bar"
            # ONE interval read: the pick over C0 as the bar reads it, never a point compare beside it.
            separated = bar.separates(lift)
            return RoundAdvance.ADVANCED if separated else RoundAdvance.NOT_SEPARATED


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
        bar: CrowningBar | None,
    ) -> OverlapReading:
        return cls(
            positions=tuple(positions),
            lead=lead,
            earlier=tuple(earlier),
            triggered=tuple(triggered),
            vs_parent=vs_parent,
            advance=round_advance(round_num, improved, lead, bar),
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
            bar=None,
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
    head = anchor.headline
    line = [
        LineRate(arm=anchor.a.address.arm, rate=head.rate_a, unit=head.measurand.unit, n=anchor.a.n)
    ]
    for pick in (*overlap.earlier, overlap.lead):
        lift, b = pick.headline, pick.b
        if lift is not None and b is not None and (arm := b.address.arm) is not None:
            line.append(LineRate(arm=arm, rate=lift.rate_b, unit=lift.measurand.unit, n=b.n))
    return line


def triggered_rates(overlap: OverlapReading) -> list[LineRate]:
    return [
        LineRate(arm=arm, rate=headline.rate_b, unit=headline.measurand.unit, n=b.n)
        for t in overlap.triggered
        if (headline := t.reading.headline) is not None
        and (b := t.reading.b) is not None
        and (arm := b.address.arm) is not None
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
    scope_of: Mapping[int, str],
    size: int,
) -> list[int]:
    """*origin_cells* are the origin's own ROUND's in walk order, never every cell it was later read
    on: its first cells are the ones the next round's draw walks first, so the panel costs no top-up
    there, and a resumed round rebuilds it off the same banked rows."""
    by_scope: dict[str, list[int]] = {}
    pool = set(poolable)
    for sid in (sid for sid in dict.fromkeys(origin_cells) if sid in pool):
        by_scope.setdefault(scope_of[sid], []).append(sid)
    sat = set(by_scope)
    for sid in sorted(set(line_cells) & set(poolable)):
        if scope_of[sid] not in sat:
            by_scope.setdefault(scope_of[sid], []).append(sid)
    return sorted(sid for cells in by_scope.values() for sid in cells[:size])
