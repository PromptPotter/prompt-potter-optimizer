from __future__ import annotations

from collections.abc import Collection, Mapping, Sequence
from enum import StrEnum
from typing import Annotated, NamedTuple

from pydantic import Field

from promptpotter.domain.paired_reading import (
    READING_STATE_INFO,
    ArmName,
    PairedReading,
    ReadingState,
    ReadingStateKind,
)
from promptpotter.domain.scoring import CellSheet
from promptpotter.domain.strict_model import FrozenModel
from promptpotter.shared.hashing import shapes_optimizer_prompt


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


# `stall` is the ONE pacing column: L1 patience, `rounds_without_advance` and the life bank read it.
ROUND_ADVANCE_INFO: Annotated[dict[RoundAdvance, RoundAdvanceInfo], shapes_optimizer_prompt] = {
    RoundAdvance.ORIGIN: RoundAdvanceInfo(
        "origin",
        "Round 0 measured the origin every later pick is read against.",
        StallEffect.STARTS,
    ),
    RoundAdvance.ADVANCED: RoundAdvanceInfo(
        "advanced",
        "The pick's lift over the origin clears zero on the origin panel, and its rate there "
        "tops every earlier pick's.",
        StallEffect.RESETS,
    ),
    RoundAdvance.ADVANCED_UNPAIRED: RoundAdvanceInfo(
        "advanced, unpaired",
        "An arm was promoted, and the origin panel held nothing to pair it with the origin on.",
        StallEffect.RESETS,
    ),
    RoundAdvance.NOT_SEPARATED: RoundAdvanceInfo(
        "not separated",
        "An arm was promoted, and on the origin panel it has not separated from the origin and "
        "every earlier pick.",
        StallEffect.RESETS,
    ),
    RoundAdvance.HELD: RoundAdvanceInfo("held", "No arm was promoted.", StallEffect.COUNTS),
    RoundAdvance.UNREAD: RoundAdvanceInfo(
        "unread",
        "An arm was promoted, and the origin-panel reading owed on it was not taken: the round "
        "neither advances the line nor counts as a stall.",
        StallEffect.SKIPS,
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


def line_by_individual(overlap: OverlapReading | None) -> dict[str, LineRate]:
    """Only members holding the WHOLE panel: a rate over a shorter denominator sat a different exam."""
    if overlap is None:
        return {}
    whole = len(overlap.positions)
    rates = (*triggered_rates(overlap), *overlap_line(overlap))
    return {r.arm.key: r for r in rates if r.n == whole}


def election_rows(drawn: CellSheet, on_panel: CellSheet, crowned: Collection[str]) -> CellSheet:
    """What a round elects on: an individual's draw, then the origin-panel cells it lacks, less
    every cell that crowned the parent."""
    skip = {cell.key for cell in drawn} | set(crowned)
    return drawn.merged(on_panel.where(lambda cell: cell.key not in skip))


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
