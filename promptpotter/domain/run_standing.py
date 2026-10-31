from __future__ import annotations

from collections.abc import Callable, Sequence
from typing import Literal, NamedTuple

from pydantic import Field

from promptpotter.domain.arms import ArmSlot, Concluded, arm_name
from promptpotter.domain.opt_search_point import OptSearchPoint
from promptpotter.domain.origin_panel import ROUND_ADVANCE_INFO, RoundAdvance, StallEffect
from promptpotter.domain.paired_reading import (
    READING_STATE_INFO,
    ArmKey,
    ArmName,
    PairedReading,
    ReadingState,
    RoundArm,
)
from promptpotter.domain.results import RoundOutcome, RoundResult
from promptpotter.domain.strict_model import FrozenModel
from promptpotter.shared.hashing import shapes_optimizer_prompt
from promptpotter.shared.measurement_context import MeasurementRole


def closed_after_origin(rounds: Sequence[int]) -> int:
    return sum(1 for number in rounds if number > 0)


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
        match ROUND_ADVANCE_INFO[rr.overlap.advance].stall:
            case StallEffect.RESETS:
                bank = min(cap, bank + 1)
            case StallEffect.COUNTS:
                bank = max(0, bank - 1)
            case StallEffect.STARTS | StallEffect.SKIPS:
                pass
    return bank


# A life bank's words: one pip per banked life, one per spent one, a skull on an empty bank.
type LivesWord = Literal["banked", "spent_pip", "empty", "empty_sentence", "life", "lives"]
LIVES_WORDS: dict[LivesWord, str] = {
    "banked": "♥",
    "spent_pip": "♡",
    "empty": "💀",
    "empty_sentence": "No lives left — the run stops after this round",
    "life": "life",
    "lives": "lives",
}


class LivesReading(FrozenModel):
    """A run's life bank: what is left of it, and its cap. Its pips and words are `bar` / `label`,
    read off `LIVES_WORDS` the same way on every surface (`lib/derivations/lives.ts`)."""

    left: int
    cap: int | None = Field(description="Null where the bank has no cap.")

    @property
    def spent(self) -> bool:
        """The bank is empty: the run stops after this round."""
        return self.left <= 0

    @property
    def bar(self) -> str:
        if self.spent:
            return LIVES_WORDS["empty"]
        spent = 0 if self.cap is None or self.cap < self.left else self.cap - self.left
        return LIVES_WORDS["banked"] * self.left + LIVES_WORDS["spent_pip"] * spent

    @property
    def label(self) -> str:
        if self.spent:
            return LIVES_WORDS["empty_sentence"]
        noun = LIVES_WORDS["life" if self.left == 1 else "lives"]
        if self.cap is None or self.cap < self.left:
            return f"{self.left} {noun} left"
        return f"{self.left} of {self.cap} {noun} left"


class LineStep(NamedTuple):
    """``opt_sp`` re-measures the member: another arm's prompt would read under this one's label."""

    arm: ArmName
    opt_sp: OptSearchPoint


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


def line_reference(rounds: Sequence[RoundOutcome]) -> RoundArm:
    """The candidate a line of *rounds* stands on, by the round that crowned it: what the next
    round re-reads."""
    return next(
        RoundArm(round=rr.round, arm=picked)
        for rr in reversed(rounds)
        if rr.election is not None and (picked := rr.election.selected) is not None
    )


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
    lives: LivesReading | None = Field(
        description="The life bank after this round. Null where the campaign banks no lives "
        "(`optimization.lives`)."
    )
    selection: ArmName | None = Field(
        description="The optimizer's declared pick (`declared_selection`), as the arm it was "
        "first measured as: the origin until a round selects. Null before round 0 closes."
    )
    parent: ArmName | None = Field(
        description="The pick `selection` was promoted over. Null while it is the origin."
    )
    changes: str = Field(
        description="What `selection` changed, in its proposer's words (its lineage's "
        "`changes_description`). Empty while it is the origin, which changed nothing."
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

    @property
    def selection_line(self) -> str:
        """The ONE text reading of the selection: who, and both rates on the origin panel — or
        the reason there is no pair. Never the selection's rate alone; derived, never served."""
        return _selection_line(self.selection, self.vs_origin)

    @classmethod
    def opening(cls, lives: tuple[int, int] | None) -> RunStanding:
        return cls(
            rounds_without_advance=0,
            lives=None
            if lives is None
            else LivesReading(left=stalls_left([], lives), cap=lives[1]),
            selection=None,
            parent=None,
            changes="",
            vs_origin=PairedReading.unread(ReadingState.NO_SELECTION),
            rounds_closed=0,
            improved=0,
            advanced=0,
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
            lives=None
            if lives is None
            else LivesReading(left=stalls_left(rounds, lives), cap=lives[1]),
            selection=arms[chosen],
            parent=arms[before[-1]] if before else None,
            changes=""
            if vs_origin.state is ReadingState.SAME_INDIVIDUAL
            else declared.arms[chosen].changes_description,
            vs_origin=vs_origin,
            rounds_closed=closed_after_origin([rr.round for rr in rounds]),
            improved=sum(1 for rr in after_origin if rr.improved),
            advanced=sum(1 for rr in after_origin if rr.overlap.advance is RoundAdvance.ADVANCED),
        )


def measured_arm(rounds: Sequence[RoundOutcome], individual_id: str) -> Concluded:
    for rr in reversed(rounds):
        arm = rr.arms.get(ArmKey(individual_id))
        if isinstance(arm, Concluded):
            return arm
    raise KeyError(f"no closed round measured individual {individual_id}")


def walked_slot(rounds: Sequence[RoundOutcome], key: ArmKey) -> ArmSlot | None:
    """The candidate a re-read of *key* reads: its latest walk as a proposal, as `measured_arm`
    reads it; ``None`` for one no round walked."""
    return next(
        (
            arm.slot
            for rr in reversed(rounds)
            if (arm := rr.arms.get(key)) is not None
            and arm.slot is not None
            and arm.role != MeasurementRole.PARENT
        ),
        None,
    )
