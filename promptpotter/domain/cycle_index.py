from __future__ import annotations

from typing import Literal

from pydantic import Field

from promptpotter.domain.arm_reading import DisplayMetric
from promptpotter.domain.campaign import LIFECYCLE_STATUS_LABELS, LifecycleStatus
from promptpotter.domain.phases import (
    STOP_REASON_INFO,
    RunPhase,
    StopReason,
    run_phase_label,
)
from promptpotter.domain.run_records import CycleFinal, ForkSpec, SpawnedBy, sibling_cycle_id
from promptpotter.domain.run_standing import RunStanding, closed_after_origin
from promptpotter.domain.strict_model import FrozenModel


class IndexRound(FrozenModel):
    """The level is ``None`` where the round's measurand is not an accuracy: an L4 outer round."""

    round: int
    accuracy: float | None


class Intervention(FrozenModel):
    kind: str
    at: str


class CycleIndex(FrozenModel):
    """Folded off the ledger; ``index.json`` is this written out for a human, and no code reads it."""

    cycle_id: str
    created_at: str
    updated_at: str
    # The mint record's own address (`CycleMintedRecord.parent`).
    parent: str | None = None
    forked_at_offset: int | None = None
    cut_round: int | None = None
    fork: ForkSpec | None = None
    spawned_by: SpawnedBy | None = None
    scorer_cell_formula: str | None = None
    interventions: list[Intervention] = Field(default_factory=list)
    # ``final`` is banked only by the cycle's own runner: a reaped or superseded cycle ends with none.
    stop_reason: StopReason | None = None
    finished_at: str | None = None
    interrupted_round: int | None = None
    crash_traceback: str | None = None
    final: CycleFinal | None = None
    superseded_by: str | None = None
    rounds: list[IndexRound] = Field(default_factory=list)
    # Rounds an arm walked in that no close stands for yet.
    open_rounds: list[int] = Field(default_factory=list)
    # How the newest election elected; ``None`` before the first.
    elects_on: DisplayMetric | None = None
    standing: RunStanding | None = None

    @property
    def parent_cycle_id(self) -> str | None:
        """The parent where it is a cycle of this campaign; a cut from another tree reads as a root."""
        return sibling_cycle_id(self.parent)

    @property
    def human_intervened(self) -> bool:
        return bool(self.interventions)

    @property
    def rounds_closed(self) -> int:
        """Rounds closed AFTER the origin — the unit a rounds cap counts."""
        return closed_after_origin([r.round for r in self.rounds])


type StatusMark = Literal[
    "archived",
    "running",
    "scoring",
    "starting",
    "queued",
    "gate",
    "paused",
    "detached",
    "success",
    "halted",
    "failed",
    "unknown",
]


class RunStatus(FrozenModel):
    """How a run reads on a row: one word and the mark drawn beside it."""

    label: str
    mark: StatusMark

    @classmethod
    def of(
        cls,
        run_phase: RunPhase,
        stop_reason: StopReason | None,
        *,
        lifecycle: LifecycleStatus,
    ) -> RunStatus:
        if lifecycle == "archived":
            return cls(label=LIFECYCLE_STATUS_LABELS[lifecycle], mark="archived")
        mark: str = "unknown"
        if run_phase is not RunPhase.TERMINAL:
            mark = run_phase.value
        elif stop_reason is not None:
            mark = STOP_REASON_INFO[stop_reason].outcome.value
        return cls.model_validate({"label": run_phase_label(run_phase, stop_reason), "mark": mark})
