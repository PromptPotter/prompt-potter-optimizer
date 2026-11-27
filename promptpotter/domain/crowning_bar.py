"""The bar potter's election crowns on: a predicate whose statistic is a switch
(``theta_election.crown``). A baseline crowns by its own rule and is never read here."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Literal, NamedTuple, get_args

from promptpotter.domain.paired_reading import MeasuredLift
from promptpotter.domain.strict_model import FrozenModel
from promptpotter.shared.statistics import (
    discordance_e_value,
    holm_adjusted,
    sign_test_p,
)

#: The point reading crowns on the green bar's value, its interval information; the others on a
#: confidence the round's evidence must reach. Every one reads the paired fitness verdicts alone.
CrowningStatistic = Literal["paired_point", "sign_test", "e_process"]

#: What a round no arm is crowned in ends on, so what the next round mutates from: its parent
#: (``crowned``, plus selection), its leader where the leader did not lose to the parent
#: (``not_lost``, a neutral move), or its leader whatever it read (``leading``, comma selection).
#: The declared pick is always a crown.
NextParent = Literal["crowned", "not_lost", "leading"]


#: How each reading crowns, as the election's prompt line states it.
CROWNING_RULE: dict[CrowningStatistic, str] = {
    "paired_point": "by the arm whose verdicts on the cells it shares with the parent gain the "
    "most over the parent's, any net gain crowning",
    "sign_test": "by an arm winning more shared cells than it loses past an exact sign test on "
    "them, Holm-corrected across the round's arms",
    "e_process": "by an arm winning more shared cells than it loses past an anytime-valid "
    "e-process on them",
}
assert CROWNING_RULE.keys() == set(get_args(CrowningStatistic))

#: Which rows a subject pairs with its parent on: the round's draw, or the origin panel's alone.
Vote = Literal["draw", "overlap"]


class BarSubject(NamedTuple):
    """One arm's evidence against its bar: its fitness pairing over the parent on the vote's cells
    (the green bar's point and the cells it won and lost)."""

    paired: MeasuredLift

    @property
    def wins(self) -> int:
        return self.paired.flips.gained

    @property
    def losses(self) -> int:
        return self.paired.flips.lost


class CrowningBar(FrozenModel):
    """*admission* is ``1 − α``, which the point reading never reads. A subject its paired cells
    contradict (no more wins than losses) clears under no reading."""

    statistic: CrowningStatistic
    admission: float

    def rank(self, subject: BarSubject) -> float:
        """What the round's arms are ordered on, its crown among those clearing and its lead among
        all, zero where the arm stands level with its parent: the green bar's point."""
        return subject.paired.estimate.value

    def lost(self, subject: BarSubject) -> bool:
        """The parent beat *subject* on what the bar ranks: it stands below level, or was not read.
        On one fixed panel paired lifts add, so a line stepping only on arms that did not lose
        never falls below a crown it held."""
        return self.rank(subject) < 0.0

    def clears[K](self, subjects: Mapping[K, BarSubject]) -> dict[K, bool]:
        """Every subject of the round at once: the multiplicity is the round's arms."""
        alpha, m = 1.0 - self.admission, len(subjects)
        match self.statistic:
            case "paired_point":
                read = {k: s.paired.estimate.value > 0.0 for k, s in subjects.items()}
            case "sign_test":
                adjusted = holm_adjusted([sign_test_p(s.wins, s.losses) for s in subjects.values()])
                read = {k: p <= alpha for k, p in zip(subjects, adjusted, strict=True)}
            case "e_process":
                read = {
                    k: discordance_e_value(s.wins, s.losses) >= m / alpha
                    for k, s in subjects.items()
                }
        return {k: read[k] and s.wins > s.losses for k, s in subjects.items()}
