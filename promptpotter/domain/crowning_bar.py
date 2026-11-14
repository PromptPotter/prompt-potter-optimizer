"""The ONE bar crown, advance and promotion read: a predicate whose statistic is a switch
(``OptimizationConfig.crowning_bar``), each reading naming what a result quotes."""

from __future__ import annotations

import math
from collections.abc import Iterable, Mapping
from statistics import fmean
from typing import Literal, NamedTuple

from promptpotter.domain.paired_reading import Estimate, MeasuredLift
from promptpotter.domain.ruler import p_better, theta_lift
from promptpotter.domain.strict_model import FrozenModel
from promptpotter.shared.statistics import (
    discordance_e_value,
    holm_adjusted,
    sign_test_p,
)

#: ``posterior`` is today's bar and the default until separability is read on banked runs.
CrowningStatistic = Literal["posterior", "sign_test", "e_process", "empirical_bayes"]

#: The ``1 − α`` a subject is read at where no selector declares one: the advance's one-sided 2.5%.
BAR_ADMISSION = 0.975


class LiftPrior(FrozenModel):
    """The generator's lift prior N(μ0, τ²) over arm-vs-parent θ lifts: μ0 their mean, τ² their
    observed variance less their mean noise variance, floored at 0."""

    mu0: float
    tau: float
    pairs: int

    def shrunk(self, lift: Estimate) -> Estimate:
        """The arm's posterior: mean μ0 + τ²/(τ²+se²)·(x−μ0), spread τ·se/√(τ²+se²)."""
        tau2, se2 = self.tau**2, (lift.se or 0.0) ** 2
        weight = tau2 / (tau2 + se2) if tau2 + se2 > 0.0 else 0.0
        spread = math.sqrt(tau2 * se2 / (tau2 + se2)) if tau2 + se2 > 0.0 else 0.0
        return theta_lift(self.mu0 + weight * (lift.value - self.mu0), spread, 0.0, 0.0)


def lift_prior(lifts: Iterable[Estimate]) -> LiftPrior | None:
    """``None`` below two pairs: one lift has no variance to read τ off."""
    held = list(lifts)
    if len(held) < 2:
        return None
    mu0 = fmean(lift.value for lift in held)
    observed = sum((lift.value - mu0) ** 2 for lift in held) / (len(held) - 1)
    noise = fmean((lift.se or 0.0) ** 2 for lift in held)
    return LiftPrior(mu0=mu0, tau=math.sqrt(max(0.0, observed - noise)), pairs=len(held))


class BarSubject(NamedTuple):
    """One subject's paired evidence against its bar: the discordant cells, the θ lift (``None``
    where none was fit), and today's posterior verdict on it."""

    wins: int
    losses: int
    lift: Estimate | None
    posterior: bool


class BarRead(FrozenModel):
    """The bar's verdict on one subject."""

    statistic: CrowningStatistic
    clears: bool


class CrowningBar(FrozenModel):
    """*admission* is ``1 − α``; *prior* is the tenant's banked lift prior, which only
    ``empirical_bayes`` reads. A subject its paired cells contradict (no more wins than losses)
    clears under no reading."""

    statistic: CrowningStatistic
    admission: float
    prior: LiftPrior | None = None

    def read[K](self, subjects: Mapping[K, BarSubject]) -> dict[K, BarRead]:
        """Every subject of the round at once: the multiplicity is the round's arms."""
        alpha, m = 1.0 - self.admission, len(subjects)
        match self.statistic:
            case "posterior":
                # Today's bar as its reader holds it, the paired cells' agreement included.
                return {
                    k: BarRead(statistic="posterior", clears=s.posterior)
                    for k, s in subjects.items()
                }
            case "sign_test":
                adjusted = holm_adjusted([sign_test_p(s.wins, s.losses) for s in subjects.values()])
                return {
                    k: self._read(s, p <= alpha)
                    for (k, s), p in zip(subjects.items(), adjusted, strict=True)
                }
            case "e_process":
                return {
                    k: self._read(s, discordance_e_value(s.wins, s.losses) >= m / alpha)
                    for k, s in subjects.items()
                }
            case "empirical_bayes":
                return {k: self._shrunk(s) for k, s in subjects.items()}

    def separates(self, lift: MeasuredLift) -> bool:
        """The advance: the pick over C0 on the origin panel, its one subject. ``posterior`` is the
        lift's own side; the discordance readings count its flips, so a continuous measurand has
        none to clear on; ``empirical_bayes``'s prior is over θ lifts on a parent, so it reads the
        side."""
        above = lift.side == "above"
        if self.statistic in ("posterior", "empirical_bayes"):
            return above
        if lift.flips is None:
            return False
        subject = BarSubject(lift.flips.gained, lift.flips.lost, None, above)
        return self.read({0: subject})[0].clears

    def _read(self, subject: BarSubject, clears: bool) -> BarRead:
        agrees = subject.wins > subject.losses
        return BarRead(statistic=self.statistic, clears=agrees and clears)

    def _shrunk(self, subject: BarSubject) -> BarRead:
        if self.prior is None or subject.lift is None:
            return self._read(subject, False)
        return self._read(subject, p_better(self.prior.shrunk(subject.lift)) > self.admission)
