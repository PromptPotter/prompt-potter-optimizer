from __future__ import annotations

from collections.abc import Callable, Sequence
from typing import Literal, NamedTuple

from pydantic import Field

from promptpotter.domain.arm_reading import UNIT_FORMATS, level_text, lift_text
from promptpotter.domain.arms import ArmSlot, Concluded, arm_name
from promptpotter.domain.bench import COST_ABSENCE_WORDS, LiftCost
from promptpotter.domain.opt_search_point import OptSearchPoint
from promptpotter.domain.origin_panel import RoundAdvance, StallEffect
from promptpotter.domain.paired_reading import (
    READING_STATE_INFO,
    ArmKey,
    ArmName,
    PairedReading,
    ReadingState,
    RoundArm,
    lift_versus,
)
from promptpotter.domain.results import RoundOutcome, RoundResult
from promptpotter.domain.strict_model import FrozenModel
from promptpotter.shared.clock import duration_words
from promptpotter.shared.measurement_context import MeasurementRole
from promptpotter.shared.worded import Worded


def closed_after_origin(rounds: Sequence[int]) -> int:
    return sum(1 for number in rounds if number > 0)


def rounds_without_advance(rounds: Sequence[RoundOutcome]) -> int:
    depth = 0
    for rr in reversed(rounds):
        match rr.overlap.advance.stall:
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
        match rr.overlap.advance.stall:
            case StallEffect.RESETS:
                bank = min(cap, bank + 1)
            case StallEffect.COUNTS:
                bank = max(0, bank - 1)
            case StallEffect.STARTS | StallEffect.SKIPS:
                pass
    return bank


# A life bank's words: one pip per banked life, one per spent one, a skull on an empty bank.
type LivesWord = Literal["banked", "spent_pip", "empty", "empty_sentence", "life", "lives"]
LIVES_WORDS: Worded[LivesWord, str] = Worded(
    {
        "banked": "♥",
        "spent_pip": "♡",
        "empty": "💀",
        "empty_sentence": "No lives left — the run stops after this round",
        "life": "life",
        "lives": "lives",
    }
)


class LivesReading(FrozenModel):
    """A run's life bank: what is left of it, and its cap."""

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
    fitness_ceiling: float | None


def round_clocks(rounds: Sequence[RoundResult], *, fitness_ceiling: float | None) -> RoundClocks:

    def first(holds: Callable[[RoundResult], bool]) -> int | None:
        return next((r.round for r in rounds if holds(r)), None)

    to_ceiling: int | None = None
    if fitness_ceiling is not None:
        target = CEILING_FRACTION * fitness_ceiling
        to_ceiling = first(
            lambda r: (
                (fold := r.fold) is not None and fold.fitness is not None and fold.fitness >= target
            )
        )
    return RoundClocks(
        rounds_to_separable=first(lambda r: r.overlap.advance is RoundAdvance.ADVANCED),
        rounds_to_improved=first(lambda r: r.improved),
        rounds_to_ceiling=to_ceiling,
        fitness_ceiling=fitness_ceiling,
    )


def line_reference(rounds: Sequence[RoundOutcome], *, origin: ArmKey) -> RoundArm:
    """The candidate a line of *rounds* stands on going into the next round: its *origin* until an election crowns another."""
    return next(
        (
            RoundArm(round=rr.round, arm=picked)
            for rr in reversed(rounds)
            if rr.election is not None and (picked := rr.election.selected) is not None
        ),
        RoundArm(round=0, arm=origin),
    )


def declared_selection[R: RoundOutcome](rounds: Sequence[R]) -> R:
    return next(
        rr
        for rr in reversed(rounds)
        if rr.election is not None and rr.election.selected is not None
    )


class LineOutlay(NamedTuple):
    """What the campaign's line had paid when a round closed: the denominators of its climb."""

    search_usd: float | None
    worked_s: float | None


type ClimbKey = Literal["lift_per_usd", "lift_per_candidate", "time_per_lift"]


class ClimbRate(NamedTuple):
    """``value`` is ``None`` where its denominator is absent or zero; ``sub`` says which."""

    key: ClimbKey
    label: str
    per: str
    value: str | None
    sub: str


class Climb(NamedTuple):
    """What the selection gained over C0, priced by what the line paid, and over its parent; each
    lift's name (`lift_versus`) says the sheet its own reading was read on."""

    candidate: str
    lift: str
    over_origin: str
    vs_parent: str | None
    over_parent: str | None
    measurand: str
    rates: list[ClimbRate]

    @property
    def line(self) -> str:
        """The lead line: who, both lifts as named, every rate (an absent one as `—` with its reason)."""
        parent = "" if self.vs_parent is None else f", {self.vs_parent} {self.over_parent}"
        return f"{self.candidate}: {self.lift} {self.over_origin}{parent} · " + " · ".join(
            f"— {rate.per} ({rate.sub})" if rate.value is None else f"{rate.value} {rate.per}"
            for rate in self.rates
        )


def _per_unit_time(seconds: float) -> str:
    return f"{seconds:.1f}s" if seconds < 10 else duration_words(seconds)


def _climb(standing: RunStanding) -> Climb | None:
    pair, candidates = standing.vs_origin, standing.candidates
    if (headline := pair.headline) is None:
        return None
    lift, unit = headline.estimate.value, headline.measurand.unit
    fmt = UNIT_FORMATS[unit]
    gained = fmt.scale * lift
    usd, worked = standing.search_usd, standing.worked_s
    cost = LiftCost.of(pair, usd)
    parent = standing.vs_parent
    over = None if parent is None else parent.headline
    return Climb(
        candidate=standing.selection_name,
        lift=lift_text(lift, unit),
        over_origin=lift_versus("origin", pair.cell_set),
        vs_parent=None if over is None else lift_text(over.estimate.value, over.measurand.unit),
        over_parent=(
            None if parent is None or over is None else lift_versus("parent", parent.cell_set)
        ),
        measurand=headline.measurand.key,
        rates=[
            ClimbRate(
                "lift_per_usd",
                "Lift per search $",
                "per $",
                None if cost.lift_per_usd is None else lift_text(cost.lift_per_usd, unit),
                f"${usd:.2f} of search" if cost.absent is None else COST_ABSENCE_WORDS[cost.absent],
            ),
            ClimbRate(
                "lift_per_candidate",
                "Lift per candidate",
                "per candidate",
                lift_text(lift / candidates, unit) if candidates else None,
                f"{candidates} tested",
            ),
            ClimbRate(
                "time_per_lift",
                f"Time per {fmt.lift_name}",
                f"per {fmt.lift_name}",
                _per_unit_time(worked / gained) if worked and gained > 0 else None,
                "no lift to time"
                if gained <= 0
                else "no launch's clock parsed"
                if worked is None
                else f"{duration_words(worked)} worked",
            ),
        ],
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
    vs_parent: PairedReading | None = Field(
        description="`selection` (`b`) over `parent` (`a`) on the origin panel, from the round "
        "reading `vs_origin` comes from (`OverlapReading.vs_parent`); `vs_origin` itself where "
        "the parent is C0. Null while the selection is the origin, or where that reading pairs "
        "no such two."
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
    candidates: int = Field(
        description="Arms those rounds scored (`RoundOutcome.candidates_scored`), a re-read "
        "leader once per read."
    )
    search_usd: float | None = Field(
        description="USD the SEARCH had incurred at this close, every cycle of the line read "
        "whole (`SpendRollup.search_incurred_usd`); null on unpriced search tokens."
    )
    worked_s: float | None = Field(
        description="Worked seconds of the line's launches to this close "
        "(`domain/campaign.py::worked_seconds`), the closing launch's to now; null where no "
        "launch's endpoints parsed."
    )

    @property
    def outlay(self) -> LineOutlay:
        return LineOutlay(search_usd=self.search_usd, worked_s=self.worked_s)

    @property
    def selection_name(self) -> str:
        """Who the selection is, without its rates: a figure beside the lift that carries them."""
        pick = self.selection
        return "nothing selected" if pick is None else f"{pick.label} (round {pick.round})"

    @property
    def score_line(self) -> str:
        """Both rates on the origin panel, or why there is no pair: the line beneath the climb."""
        pair = self.vs_origin
        if pair.headline is None or pair.coverage is None:
            return READING_STATE_INFO[pair.state].sentence
        unit = pair.headline.measurand.unit
        return (
            f"origin {level_text(pair.headline.rate_a, unit)} → "
            f"{level_text(pair.headline.rate_b, unit)} "
            f"on {pair.coverage.scored} origin-panel cells"
        )

    @property
    def lead_line(self) -> str:
        """The ONE text reading of the standing: the climb, else who; then `score_line`."""
        lead = self.selection_name if (climb := self.climb) is None else climb.line
        return lead if self.selection is None else f"{lead} — {self.score_line}"

    @property
    def climb(self) -> Climb | None:
        """The selection's lift over C0 per search dollar, per candidate tested and per unit of worked time; ``None`` with no pair."""
        return _climb(self)

    @classmethod
    def after(
        cls,
        rounds: Sequence[RoundOutcome],
        *,
        lives: tuple[int, int] | None,
        outlay: LineOutlay,
    ) -> RunStanding:
        declared = declared_selection(rounds)
        picked = [key for rr in rounds if rr.election and (key := rr.election.selected) is not None]
        chosen = picked[-1]
        arms: dict[str, ArmName] = {arm.key: arm for arm in pick_line(rounds)}
        before = [individual for individual in picked if individual != chosen]
        vs_origin, overlap = next(
            (
                (reading, rr.overlap)
                for rr in reversed(rounds)
                if rr.round >= declared.round
                for reading in (rr.overlap.lead, *rr.overlap.earlier)
                if reading.headline is not None
                and reading.b is not None
                and reading.b.address.individual_id == chosen
            ),
            (declared.overlap.lead, declared.overlap),
        )
        # Both legs read one sheet: where the parent is C0, the parent leg IS the origin leg.
        over_parent = (before[-1], chosen) if before else None
        vs_parent = next(
            (
                pair
                for pair in (vs_origin, overlap.vs_parent)
                if pair is not None
                and pair.a is not None
                and pair.b is not None
                and (pair.a.address.individual_id, pair.b.address.individual_id) == over_parent
            ),
            None,
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
            vs_parent=vs_parent,
            rounds_closed=closed_after_origin([rr.round for rr in rounds]),
            improved=sum(1 for rr in after_origin if rr.improved),
            advanced=sum(1 for rr in after_origin if rr.overlap.advance is RoundAdvance.ADVANCED),
            candidates=sum(rr.candidates_scored for rr in after_origin),
            search_usd=outlay.search_usd,
            worked_s=outlay.worked_s,
        )


def measured_arm(rounds: Sequence[RoundOutcome], individual_id: str) -> Concluded:
    for rr in reversed(rounds):
        arm = rr.arms.get(ArmKey(individual_id))
        if isinstance(arm, Concluded):
            return arm
    raise KeyError(f"no closed round measured individual {individual_id}")


def walked_slot(rounds: Sequence[RoundOutcome], key: ArmKey) -> ArmSlot | None:
    """The candidate a re-read of *key* reads: its latest walk as a proposal; ``None`` for one no round walked."""
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
