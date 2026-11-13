from __future__ import annotations

from typing import Literal, Self, get_args

from pydantic import Field, model_validator

from promptpotter.domain.bench import BandedValue, OwnLevel, PassStop
from promptpotter.domain.paired_reading import ArmName, PairedReading
from promptpotter.domain.pass_kinds import PASSES, IndividualWalk
from promptpotter.domain.scoring import WalkedCell
from promptpotter.domain.strict_model import FrozenModel
from promptpotter.shared.measurement_context import MeasurementRole
from promptpotter.shared.worded import Worded

VerifyStrategy = Literal["random", "hard"]

VERIFY_STRATEGY_LABELS: Worded[VerifyStrategy, str] = Worded(
    {
        "random": "picked at random",
        "hard": "hardest first",
    }
)
if set(VERIFY_STRATEGY_LABELS) != set(get_args(VerifyStrategy)):
    raise RuntimeError("VERIFY_STRATEGY_LABELS is out of step with VerifyStrategy")

VerifyHeldAbsent = Literal["hard_picks", "under_two_fresh"]


class VerificationPass(FrozenModel):
    """One individual's held-out pass, as the facts it banked — never a grade of them."""

    # A kind whose `Pass` row brackets it on the ledger.
    kind: MeasurementRole
    arm: ArmName
    sp_hash: str
    # In walk order; a row the pass never reached is absent.
    cells: list[WalkedCell]
    # Every row it was sent on, by content (`Sample.key`): the cell set two passes pair on.
    sample_keys: list[str]
    # `None` once it sent every row.
    stop: PassStop | None
    # The grader that stamped its live reading; a reader re-grades under its own.
    scorer_id: str
    # `verify` alone: how its fresh rows were drawn, and the draw's seed.
    strategy: VerifyStrategy | None
    seed: int | None
    # `bench` alone: individuals already graded on these rows when it was sent; a high count reads the headline optimistic.
    reads_before: int | None
    # By `Sample.key`, each row measuring another dataset than the one the cycle is filed under.
    measured_datasets: dict[str, str] = Field(default_factory=dict)

    @model_validator(mode="after")
    def _kind_fields(self) -> Self:
        if PASSES[self.kind].phase is None:
            raise ValueError(f"a {self.kind} pass is no held-out pass")
        if (self.strategy is None) is (self.kind == MeasurementRole.VERIFY):
            raise ValueError("a verify pass names its draw, and only a verify pass")
        if (self.reads_before is None) is (self.kind == MeasurementRole.BENCH):
            raise ValueError("a bench pass counts its reads before, and only a bench pass")
        return self

    def walk(self) -> IndividualWalk[WalkedCell]:
        return IndividualWalk(self.arm.key, frozenset({self.kind}), self.cells)

    def answers(self, sp_hash: str, sample_keys: list[str], scorer_id: str) -> bool:
        """Banked whole for this point, on these rows, under this grader: re-sending it buys nothing."""
        return self.stop is None and (self.sp_hash, self.sample_keys, self.scorer_id) == (
            sp_hash,
            sample_keys,
            scorer_id,
        )

    def by_dataset(self) -> dict[str | None, list[str]]:
        """``None`` is the dataset the cycle is filed under."""
        out: dict[str | None, list[str]] = {}
        for key in self.sample_keys:
            out.setdefault(self.measured_datasets.get(key), []).append(key)
        return out


class NoiseBand(FrozenModel):
    """A noise-floor pass's reading: its fresh composites beside the recorded level, both over
    exactly the pass's rows (bl3.B2)."""

    # One composite per rescore, in order.
    raw: list[float]
    band: BandedValue
    fresh_fitness: float
    recorded: OwnLevel


class BenchPasses(FrozenModel):
    """``runner/bench.py::read_bench`` is the one reading of these passes."""

    tolerance: int
    origin: VerificationPass
    # Set aside so the selection's pass fits under the search's ceiling (``runner/bench.py::reserve_selection_pass``).
    reserve_usd: float
    reserve_tokens: int
    # The latest pass of each individual the line selected, by its id: a pass measures an
    # individual, so a rewind displaces none.
    selections: dict[str, VerificationPass]

    def pass_of(self, individual_id: str) -> VerificationPass | None:
        if individual_id == self.origin.arm.key:
            return self.origin
        return self.selections.get(individual_id)


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
    fitness_increment: float | None = Field(
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
