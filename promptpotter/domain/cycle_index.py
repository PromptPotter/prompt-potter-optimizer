from __future__ import annotations

from typing import Literal, NamedTuple

from pydantic import Field

from promptpotter.domain.arm_reading import DisplayMetric
from promptpotter.domain.campaign import LIFECYCLE_STATUS_LABELS, LifecycleStatus
from promptpotter.domain.dashboard_rows import RoundHealth
from promptpotter.domain.phases import (
    STOP_REASON_INFO,
    RunPhase,
    StopOutcome,
    StopReason,
    run_phase_label,
)
from promptpotter.domain.records.error import ErrorRecord
from promptpotter.domain.records.run_records import (
    CycleFinal,
    ForkSpec,
    SpawnedBy,
    sibling_cycle_id,
)
from promptpotter.domain.run_standing import RunStanding, closed_after_origin
from promptpotter.domain.said import Tone
from promptpotter.domain.strict_model import FrozenModel
from promptpotter.shared.worded import Worded


class IndexRound(FrozenModel):
    """The level is ``None`` where the round folded no cell."""

    round: int
    fitness: float | None
    health: RoundHealth


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
    # The failed stop's, where the run stands on one.
    error: ErrorRecord | None = None
    final: CycleFinal | None = None
    superseded_by: str | None = None
    rounds: list[IndexRound] = Field(default_factory=list)
    # Rounds an arm walked in that no close stands for yet.
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


# A live phase and a stop outcome ARE their marks: one spelling, the enum's.
type StatusMark = (
    Literal[
        RunPhase.QUEUED,
        RunPhase.STARTING,
        RunPhase.RUNNING,
        RunPhase.SCORING,
        RunPhase.PAUSED,
        RunPhase.GATE,
        RunPhase.DETACHED,
    ]
    # A terminal run's outcome; `StopOutcome.PAUSED` is `RunPhase.PAUSED`'s mark.
    | Literal[StopOutcome.SUCCESS, StopOutcome.HALTED, StopOutcome.FAILED]
    | Literal["archived", "unknown"]
)


class StatusMarkInfo(NamedTuple):
    glyph: str
    tone: Tone


STATUS_MARK_INFO: Worded[StatusMark, StatusMarkInfo] = Worded(
    {
        RunPhase.RUNNING: StatusMarkInfo("●", "live"),
        RunPhase.SCORING: StatusMarkInfo("◐", "live"),
        RunPhase.STARTING: StatusMarkInfo("◌", "live"),
        RunPhase.QUEUED: StatusMarkInfo("…", "quiet"),
        RunPhase.GATE: StatusMarkInfo("◆", "attention"),
        RunPhase.PAUSED: StatusMarkInfo("‖", "quiet"),
        RunPhase.DETACHED: StatusMarkInfo("⊘", "warn"),
        StopOutcome.SUCCESS: StatusMarkInfo("✓", "success"),
        StopOutcome.HALTED: StatusMarkInfo("■", "warn"),
        StopOutcome.FAILED: StatusMarkInfo("✕", "danger"),
        "archived": StatusMarkInfo("▫", "quiet"),
        "unknown": StatusMarkInfo("?", "quiet"),
    }
)


class RunStatus(FrozenModel):
    """How a run stands on a row: its phase, why it stopped, and the mark drawn beside it."""

    run_phase: RunPhase
    stop_reason: StopReason | None = Field(description="Null while the run has not stopped.")
    mark: StatusMark

    @classmethod
    def of(
        cls,
        run_phase: RunPhase,
        stop_reason: StopReason | None,
        *,
        lifecycle: LifecycleStatus,
    ) -> RunStatus:
        mark: StatusMark = "unknown"
        if lifecycle == "archived":
            mark = "archived"
        elif run_phase is not RunPhase.TERMINAL:
            mark = run_phase
        elif (
            stop_reason is not None
            and (outcome := STOP_REASON_INFO[stop_reason].outcome) is not StopOutcome.PAUSED
        ):
            mark = outcome
        return cls(run_phase=run_phase, stop_reason=stop_reason, mark=mark)

    @property
    def label(self) -> str:
        if self.mark == "archived":
            return LIFECYCLE_STATUS_LABELS["archived"]
        return run_phase_label(self.run_phase, self.stop_reason)
