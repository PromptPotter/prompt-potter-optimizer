from __future__ import annotations

from typing import Literal, Self

from pydantic import Field, model_validator

from promptpotter.domain.bench import BandedValue, OwnLevel, PassStop
from promptpotter.domain.paired_reading import ArmName
from promptpotter.domain.scoring import WalkedCell
from promptpotter.domain.strict_model import FrozenModel
from promptpotter.domain.verify_pass import VerifyStrategy
from promptpotter.shared.measurement_context import IndividualWalk, MeasurementRole

#: `verify` re-scores search rows the arm never read; `bench` reads the held-out rows;
#: `noise_floor` re-scores the origin's round-0 rows fresh, `k` times over.
VerificationKind = Literal["verify", "bench", "noise_floor"]

# A noise-floor pass re-reads rows already standing, so it joins no walk.
_ROLE: dict[VerificationKind, MeasurementRole] = {
    "verify": MeasurementRole.VERIFY,
    "bench": MeasurementRole.BENCH,
}


class VerificationPass(FrozenModel):
    """One individual's held-out pass, as the facts it banked — never a grade of them."""

    kind: VerificationKind
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
        if (self.strategy is None) is (self.kind == "verify"):
            raise ValueError("a verify pass names its draw, and only a verify pass")
        if (self.reads_before is None) is (self.kind == "bench"):
            raise ValueError("a bench pass counts its reads before, and only a bench pass")
        return self

    @property
    def role(self) -> MeasurementRole:
        return _ROLE[self.kind]

    def walk(self) -> IndividualWalk[WalkedCell]:
        return IndividualWalk(self.arm.key, frozenset({self.role}), self.cells)

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
    fresh_accuracy: float
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
