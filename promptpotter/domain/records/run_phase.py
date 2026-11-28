"""A phase's bracket and the runner's own declaration of the phase it is in."""

from __future__ import annotations

from typing import Annotated, ClassVar, Literal

from pydantic import Field

from promptpotter.domain.arms import Proposal
from promptpotter.domain.phase_acts import PhaseAct
from promptpotter.domain.phases import (
    STOP_REASON_INFO,
    PauseCause,
    RunPhase,
    StopOutcome,
    StopReading,
    StopReason,
)
from promptpotter.domain.records import CycleRecord
from promptpotter.domain.spend_rollup import StopSpend
from promptpotter.domain.strict_model import FrozenModel
from promptpotter.shared.clock import utcnow_iso


class PhaseRecord(CycleRecord):
    """A phase's bracket; ``act`` is what the step said on it, worded at render."""

    record_type: Literal["phase"] = "phase"
    phase: str
    step: Literal["enter", "exit"]
    round: int | None = None
    act: PhaseAct | None = None
    timestamp: str = Field(default_factory=utcnow_iso)


class Running(FrozenModel):
    run_phase: Literal[RunPhase.RUNNING] = RunPhase.RUNNING


class Paused(FrozenModel):
    run_phase: Literal[RunPhase.PAUSED] = RunPhase.PAUSED
    stop_reason: StopReason
    cause: PauseCause
    detail: str = ""


class Ended(FrozenModel):
    run_phase: Literal[RunPhase.TERMINAL] = RunPhase.TERMINAL
    stop_reason: StopReason
    # What said so, where the stop's raiser gave a reason: an optimizer's abort, a refused send.
    detail: str = ""


class ArmsGate(FrozenModel):
    run_phase: Literal[RunPhase.GATE] = RunPhase.GATE
    # What the hold offers to keep, in the order its act's `keep` indexes.
    drafts: list[Proposal] = Field(default_factory=list)


type DeclaredPhase = Annotated[
    Running | Paused | Ended | ArmsGate, Field(discriminator="run_phase")
]


class RunPhaseRecord(CycleRecord):
    """The runner's own declaration: every other phase is derived off the last one, never said."""

    forks_inherit: ClassVar[bool] = False
    record_type: Literal["run_phase"] = "run_phase"
    state: DeclaredPhase
    spend: StopSpend | None = None
    timestamp: str = Field(default_factory=utcnow_iso)

    @classmethod
    def stop(
        cls,
        stop_reason: StopReason,
        *,
        cause: PauseCause | None = None,
        detail: str = "",
        spend: StopSpend | None = None,
    ) -> RunPhaseRecord:
        if STOP_REASON_INFO[stop_reason].outcome is not StopOutcome.PAUSED:
            return cls(state=Ended(stop_reason=stop_reason, detail=detail), spend=spend)
        if cause is None:
            raise ValueError(f"a {stop_reason.value} stop pauses the cycle and names no cause")
        return cls(state=Paused(stop_reason=stop_reason, cause=cause, detail=detail), spend=spend)

    @property
    def stop_reading(self) -> StopReading | None:
        """The stop it declares, as every door reads it; ``None`` where it declares none."""
        state = self.state
        return declared_stop(state, self.timestamp) if isinstance(state, Paused | Ended) else None


def declared_stop(state: Paused | Ended, at: str) -> StopReading:
    return StopReading(
        stop_reason=state.stop_reason,
        cause=state.cause if isinstance(state, Paused) else None,
        detail=state.detail,
        at=at,
    )
