"""The bar potter's election crowns on: a predicate whose statistic is a switch
(``theta_election.crown``). A baseline crowns by its own rule and is never read here."""

from __future__ import annotations

import math
from collections.abc import Iterable, Mapping
from statistics import fmean
from typing import Literal, NamedTuple, get_args

from promptpotter.domain.paired_reading import Estimate, MeasuredLift
from promptpotter.domain.ruler import clears, p_better, theta_lift
from promptpotter.domain.strict_model import FrozenModel
from promptpotter.shared.statistics import (
    discordance_e_value,
    holm_adjusted,
    sign_posterior,
    sign_test_p,
)

#: The point reading crowns on the green bar's value, its interval information; the others on a
#: confidence the round's evidence must reach.
CrowningStatistic = Literal[
    "paired_point", "posterior", "sign_test", "e_process", "empirical_bayes"
]

#: What a round no arm is crowned in ends on, so what the next round mutates from: its parent
#: (``crowned``, plus selection), its leader where the leader did not lose to the parent
#: (``not_lost``, a neutral move), or its leader whatever it read (``leading``, comma selection).
#: The declared pick is always a crown.
NextParent = Literal["crowned", "not_lost", "leading"]


#: How each reading crowns, as the election's prompt line states it.
CROWNING_RULE: dict[CrowningStatistic, str] = {
    "paired_point": "by the arm whose verdicts on the cells it shares with the parent gain the "
    "most over the parent's, any net gain crowning",
    "posterior": "on θ lift over the parent by an arm winning more shared cells than it loses",
    "sign_test": "by an arm winning more shared cells than it loses past an exact sign test on "
    "them, Holm-corrected across the round's arms",
    "e_process": "by an arm winning more shared cells than it loses past an anytime-valid "
    "e-process on them",
    "empirical_bayes": "on θ lift over the parent shrunk to the banked lift prior, by an arm "
    "winning more shared cells than it loses",
}
assert CROWNING_RULE.keys() == set(get_args(CrowningStatistic))

#: The readings that crown on the θ lift, which the cell formula's every term reaches; the others
#: crown on the paired fitness verdicts alone (`BarSubject.paired`).
THETA_CROWNS: frozenset[CrowningStatistic] = frozenset({"posterior", "empirical_bayes"})

#: Which rows a subject pairs with its parent on: the round's draw, or the origin panel's alone.
Vote = Literal["draw", "overlap"]


class LiftPrior(FrozenModel):
    """The generator's lift prior N(μ0, τ²) over arm-vs-parent θ lifts: μ0 their mean (its own
    error *mu0_se*), τ² their observed variance less their mean noise variance, floored at 0."""

    mu0: float
    tau: float
    pairs: int
    mu0_se: float

    def shrunk(self, lift: Estimate) -> Estimate:
        """The arm's posterior, B = se²/(τ²+se²): mean μ0 + (1−B)(x−μ0), variance se²(1−B) +
        B²·mu0_se², so a τ floored at 0 leaves μ0's spread, never a point mass at sign(μ0)."""
        tau2, se2 = self.tau**2, (lift.se or 0.0) ** 2
        pool = se2 / (tau2 + se2) if tau2 + se2 > 0.0 else 1.0
        spread = math.sqrt(se2 * (1.0 - pool) + (pool * self.mu0_se) ** 2)
        return theta_lift(self.mu0 + (1.0 - pool) * (lift.value - self.mu0), spread, 0.0, 0.0)


def lift_prior(lifts: Iterable[Estimate]) -> LiftPrior | None:
    """``None`` below two pairs: one lift has no variance to read τ off."""
    held = list(lifts)
    if len(held) < 2:
        return None
    mu0 = fmean(lift.value for lift in held)
    observed = sum((lift.value - mu0) ** 2 for lift in held) / (len(held) - 1)
    noise = fmean((lift.se or 0.0) ** 2 for lift in held)
    tau2 = max(0.0, observed - noise)
    mu0_se = math.sqrt((tau2 + noise) / len(held))
    return LiftPrior(mu0=mu0, tau=math.sqrt(tau2), pairs=len(held), mu0_se=mu0_se)


class BarSubject(NamedTuple):
    """One arm's evidence against its bar: its fitness pairing over the parent on the vote's cells
    (the green bar's point and the cells it won and lost), and its θ lift (``None`` where none was
    fit)."""

    paired: MeasuredLift
    lift: Estimate | None

    @property
    def wins(self) -> int:
        return self.paired.flips.gained

    @property
    def losses(self) -> int:
        return self.paired.flips.lost


class CrowningBar(FrozenModel):
    """*admission* is ``1 − α``, which the point reading never reads; *prior* is the tenant's banked
    lift prior, which only ``empirical_bayes`` reads. A subject its paired cells contradict (no
    more wins than losses) clears under no reading."""

    statistic: CrowningStatistic
    admission: float
    prior: LiftPrior | None = None

    @property
    def reads_theta(self) -> bool:
        return self.statistic in THETA_CROWNS

    def rank(self, subject: BarSubject) -> float:
        """What the round's arms are ordered on, its crown among those clearing and its lead among
        all, zero where the arm stands level with its parent: the green bar's point under the
        verdict readings, else ``p_better`` less a half; ``-inf`` where no θ lift was fit."""
        if not self.reads_theta:
            return subject.paired.estimate.value
        return -math.inf if subject.lift is None else p_better(subject.lift) - 0.5

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
            case "posterior":
                read = {
                    k: s.lift is not None
                    and clears(s.lift, self.admission, support=sign_posterior(s.wins, s.losses))
                    for k, s in subjects.items()
                }
            case "sign_test":
                adjusted = holm_adjusted([sign_test_p(s.wins, s.losses) for s in subjects.values()])
                read = {k: p <= alpha for k, p in zip(subjects, adjusted, strict=True)}
            case "e_process":
                read = {
                    k: discordance_e_value(s.wins, s.losses) >= m / alpha
                    for k, s in subjects.items()
                }
            case "empirical_bayes":
                read = {
                    k: self.prior is not None
                    and s.lift is not None
                    and p_better(self.prior.shrunk(s.lift)) > self.admission
                    for k, s in subjects.items()
                }
        return {k: read[k] and s.wins > s.losses for k, s in subjects.items()}
