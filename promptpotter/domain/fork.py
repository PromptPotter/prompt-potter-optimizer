"""A fork: its trigger and the side a cut continues on, its config delta, and a rebase's request."""

from __future__ import annotations

import enum
from typing import Self

from pydantic import Field, field_validator, model_validator

from promptpotter.domain.paired_reading import RoundArm
from promptpotter.domain.pipeline_schema import ManifestNodeOverlay
from promptpotter.domain.records.rounds import RoundStatement
from promptpotter.domain.spend import Budget, cycle_axes_only
from promptpotter.domain.strict_model import FrozenModel
from promptpotter.shared.identity import Principal


class ForkTrigger(enum.StrEnum):
    """One value per caller of :func:`cut_fork`."""

    OPERATOR_REWIND = "operator_rewind"
    OPERATOR_STEERED = "operator_steered"
    OPTIMIZER_REBASE = "optimizer_rebase"
    SCORING_DIVERGENCE = "scoring_divergence"


class ForkDirection(enum.StrEnum):
    """Which side of a cut the run CONTINUES on, the half a cut alone cannot say."""

    # The CHILD is the branch; the parent stays the line it was.
    OFFSHOOT = "offshoot"
    # The CHILD is the continuation; the parent is what was left behind.
    SUPERSEDE = "supersede"


# Derived from the trigger, never stored. Exhaustiveness is checked at import below.
FORK_DIRECTION: dict[ForkTrigger, ForkDirection] = {
    ForkTrigger.OPERATOR_STEERED: ForkDirection.OFFSHOOT,
    ForkTrigger.OPERATOR_REWIND: ForkDirection.SUPERSEDE,
    ForkTrigger.OPTIMIZER_REBASE: ForkDirection.SUPERSEDE,
    ForkTrigger.SCORING_DIVERGENCE: ForkDirection.SUPERSEDE,
}


_undirected = [t for t in ForkTrigger if t not in FORK_DIRECTION]


if _undirected:
    raise RuntimeError(
        f"ForkTrigger members missing from FORK_DIRECTION: {_undirected}. A cut whose "
        "direction nobody declared renders as an offshoot, which is a lie half the time."
    )


del _undirected


class ConfigOverrides(FrozenModel):
    """A fork's campaign-config delta, in which an absent field inherits the parent's value."""

    # Laid axis by axis over the parent's `optimization.budget`.
    budget: Budget = Budget()
    # Laid key by key onto the parent's `optimization.nodes`; nothing here switches the manifest.
    nodes: dict[str, ManifestNodeOverlay] = Field(default_factory=dict)
    # Laid key by key over the parent's `CampaignConfig.scoring`; `{"per_cell": F}` is the `score:F` lens.
    scoring: str | dict[str, str] | None = None

    _cycle_axes = field_validator("budget")(cycle_axes_only)


class ForkRemainder(FrozenModel):
    """What a cycle's rounds cap has left, which an offshoot takes as its own. Spend is no
    remainder: the ceiling is the campaign's, and its book already counts every cycle's."""

    rounds_closed: int = Field(
        description="Rounds closed AFTER the origin — what `budget.rounds` counts"
    )
    parent_rounds: int | None = Field(description="The cycle's rounds cap; null where it has none")
    rounds: int | None = Field(description="`parent_rounds` less `rounds_closed`, never under 1")

    @classmethod
    def of(cls, *, rounds_closed: int, rounds: int | None) -> ForkRemainder:
        return cls(
            rounds_closed=rounds_closed,
            parent_rounds=rounds,
            rounds=None if rounds is None else max(1, rounds - rounds_closed),
        )

    def past_cut(self, cut_round: int) -> ForkRemainder:
        """A superseding fork's cap re-counts the rounds it lifts below *cut_round*."""
        if self.rounds is None:
            return self
        return self.model_copy(update={"rounds": max(cut_round - 1, 0) + self.rounds})

    def under(self, overrides: ConfigOverrides) -> ConfigOverrides:
        """A rounds axis the overrides NAME wins, ``0`` and a lift included."""
        left = Budget() if self.rounds is None else Budget(rounds=self.rounds)
        return overrides.model_copy(update={"budget": left.over(overrides.budget)})


class ForkSpec(FrozenModel):
    trigger: ForkTrigger
    reason: str
    issued_by: Principal
    # The parent arm the fork branches from: the one its caller named, else the pick the parent's line stood on.
    from_candidate: RoundArm

    @property
    def direction(self) -> ForkDirection:
        return FORK_DIRECTION[self.trigger]


class RebaseRequest(FrozenModel):
    """A policy change and a rewind are ONE move: the new axis is searched only on the sibling."""

    # The cut: the first round the fork runs, every round below it lifted.
    fork_from_round: int = Field(ge=1)
    trigger: ForkTrigger
    reason: str
    issued_by: Principal
    config_overrides: ConfigOverrides | None = None
    # The round the fork hangs from, re-elected to the arm a layer named; ``None`` keeps the round's own pick.
    hangs_from: RoundStatement | None = None

    @model_validator(mode="after")
    def _hangs_from_its_branch(self) -> Self:
        if self.hangs_from is not None and self.hangs_from.round != self.fork_from_round - 1:
            raise ValueError(
                f"a fork cut at round {self.fork_from_round} hangs from round "
                f"{self.fork_from_round - 1}, not the round {self.hangs_from.round} it re-elects"
            )
        return self
