from __future__ import annotations

from typing import Literal, Self, get_args

from pydantic import Field, model_validator

from promptpotter.domain.bench import OwnLevel
from promptpotter.domain.paired_reading import ArmName, PairedReading
from promptpotter.domain.scoring import WalkedCell
from promptpotter.domain.strict_model import FrozenModel
from promptpotter.shared.measurement_context import IndividualWalk, MeasurementRole

VerifyStrategy = Literal["random", "hard"]

VERIFY_STRATEGY_LABELS: dict[VerifyStrategy, str] = {
    "random": "picked at random",
    "hard": "hardest first",
}
if set(VERIFY_STRATEGY_LABELS) != set(get_args(VerifyStrategy)):
    raise RuntimeError("VERIFY_STRATEGY_LABELS is out of step with VerifyStrategy")


class VerifyPass(FrozenModel):
    """The facts a verify pass banked, never a grade of them."""

    arm: ArmName
    sp_hash: str
    cells: list[WalkedCell]
    positions: list[int]
    strategy: VerifyStrategy
    seed: int | None
    # The grader that stamped its live reading; a reader re-grades under its own.
    scorer_id: str

    def walk(self) -> IndividualWalk[WalkedCell]:
        return IndividualWalk(self.arm.key, frozenset({MeasurementRole.VERIFY}), self.cells)


VerifyHeldAbsent = Literal["hard_picks", "under_two_fresh"]


class VerifyReading(FrozenModel):
    """A verify pass under a named scorer: the candidate on fresh cells, beside its decided ones."""

    label: str
    scorer_id: str = Field(
        description="The grader every number here was read under. A stored copy is a cache of "
        "that reading: a reader under another grader reads the pass again, never this."
    )
    strategy: VerifyStrategy = Field(
        description="How the fresh cells were picked: `random` from the unmeasured search split, "
        "or `hard` — its highest-δ cells first, which read BELOW the level by construction."
    )
    fresh: OwnLevel = Field(description="The level on the fresh cells alone, with its band.")
    recorded: OwnLevel = Field(
        description="The level on every cell a round's decision read this candidate on — its own "
        "walk and each re-score as a parent — re-read under this scorer: the number the fresh "
        "cells are a check on."
    )
    accuracy_increment: float | None = Field(
        description="`fresh` minus `recorded`, hit rate. Unpaired — the two are different cells."
    )
    composite_increment: float | None
    vs_origin: PairedReading = Field(
        description="This candidate over the campaign origin, on every cell the cycle measured "
        "both on outside the bench — the fresh ones and the overlap pass's included. "
        "`same_individual` on the origin itself."
    )
    held: bool | None = Field(
        description="Whether the fresh cells leave the recorded hit rate standing: its level sits "
        "at or below the fresh band's upper bound. `None` exactly where `held_absent` says why."
    )
    held_absent: VerifyHeldAbsent | None = Field(
        description="Why the fresh cells make no held / dropped call: `hard_picks` sit below the "
        "level whatever the candidate is worth, and `under_two_fresh` cells have no band. Read "
        "`vs_origin` there."
    )

    @model_validator(mode="after")
    def _held_or_why_not(self) -> Self:
        if (self.held is None) is (self.held_absent is None):
            raise ValueError("a verify reading makes its held call, or says why it makes none")
        return self
