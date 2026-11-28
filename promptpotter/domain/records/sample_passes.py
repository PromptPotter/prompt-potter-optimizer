"""One cell of a pass, started or scored, and the race's standing and catch-up walks over it."""

from __future__ import annotations

from typing import Annotated, Literal

from pydantic import AfterValidator, Field

from promptpotter.domain.paired_reading import ArmKey
from promptpotter.domain.pass_kinds import PASSES
from promptpotter.domain.records import CycleRecord
from promptpotter.domain.scoring import LedgerCell, WalkedCell
from promptpotter.shared.clock import utcnow_iso
from promptpotter.shared.measurement_context import MeasurementRole


class _Pass(CycleRecord):
    """One cell of a pass: an ARM of the round where its `role`'s row says so."""

    round: int
    individual_id: str
    role: MeasurementRole
    timestamp: str = Field(default_factory=utcnow_iso)

    @property
    def arm(self) -> ArmKey | None:
        """The arm whose walk this cell continues; ``None`` for a pass no arm walks (a bench pass)."""
        return ArmKey(self.individual_id) if PASSES[self.role].arm else None


class SampleStartedRecord(_Pass):
    record_type: Literal["sample_started"] = "sample_started"
    sample_total: int
    position: int
    # Capped at the writer: the whole query is a dataset fact, in every measurement row.
    query_preview: Annotated[str, AfterValidator(lambda q: q[:120])] = ""
    # The look-ahead the walk ran under: cells out per candidate, candidates sending at once.
    sample_lookahead: int = 1
    sample_abreast: int | Literal["all"] = 1


class SampleScoredRecord(_Pass):
    """A re-banked row has no ``sample_idx``."""

    record_type: Literal["sample_scored"] = "sample_scored"
    sample_idx: int | None = None
    sample_total: int | None = None
    # The row's slot in the bank this cycle drew, as `SampleStartedRecord` names it: the cell carries none.
    position: int
    result: LedgerCell


class RaceStandingRecord(CycleRecord):
    """Archive-only, not divergence-gated; ``p_best`` is about the arm ``current`` alone."""

    record_type: Literal["race_standing"] = "race_standing"
    round: int
    member: str
    current: ArmKey
    n_samples: int
    p_best: float
    paired_breakdown: dict[ArmKey, float]
    timestamp: str = Field(default_factory=utcnow_iso)


class RaceCatchUpRecord(CycleRecord):
    """The catch-up walks one just-measured cell of ``arm`` took, each prior's pass continued."""

    record_type: Literal["race_catch_up"] = "race_catch_up"
    round: int
    arm: ArmKey
    member: str
    # The cell's ``Sample.key``: every address below names it.
    sample_key: str
    # Its slot in the bank this cycle drew, as a readout names it.
    position: int
    # By prior.
    cells: dict[ArmKey, WalkedCell] = Field(min_length=1)
    timestamp: str = Field(default_factory=utcnow_iso)

    @property
    def prior_keys(self) -> list[ArmKey]:
        return list(self.cells)
