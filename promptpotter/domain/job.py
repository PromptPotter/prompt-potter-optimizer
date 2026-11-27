from __future__ import annotations

import enum
from collections.abc import Mapping
from collections.abc import Set as AbstractSet
from dataclasses import dataclass
from typing import Annotated, ClassVar, Literal, assert_never, get_args

from pydantic import ConfigDict, Field, TypeAdapter

from promptpotter.domain.cycle_paths import CycleHop, WorkspaceDir
from promptpotter.domain.phases import LaunchStage, RunPhase
from promptpotter.shared.measurement_context import MeasurementRole

# The slot is held before the mint resolves the cycle it will name; its LAND fills it in.
UNRESOLVED_HOP = CycleHop(campaign_id="", cycle_id="")


def launch_campaign(holder: JobHolder, hop: CycleHop) -> str | None:
    """The campaign a job runs on: its cycle's, or a fork's before its cut names one; ``None`` for
    a fresh mint and a job on no campaign."""
    if hop != UNRESOLVED_HOP:
        return hop.campaign_id
    return holder.campaign_id if isinstance(holder, CampaignLaunch) else None


JobStage = LaunchStage | Literal[RunPhase.RUNNING]
_JOB_STAGES: frozenset[RunPhase] = frozenset(
    stage for arm in get_args(JobStage) for stage in get_args(arm)
)
SlotStage = Literal[RunPhase.STARTING, RunPhase.RUNNING]
SLOT_STAGES: frozenset[RunPhase] = frozenset(get_args(SlotStage))
assert SLOT_STAGES < _JOB_STAGES, "a slot stage no job can be in"


@dataclass(frozen=True)
class CampaignLaunch:
    """A campaign run: its hop is UNRESOLVED until a fresh mint or a fork's cut names the cycle."""

    __pydantic_config__: ClassVar[ConfigDict] = ConfigDict(extra="forbid")

    dataset_name: str
    # The check-in a Start mints from; null for every other launch. Its draft is served at its stage.
    draft_id: str | None = None
    # The campaign a fork spends on before its cut names its cycle; null for every other launch.
    campaign_id: str | None = None
    kind: Literal["campaign_launch"] = "campaign_launch"


@dataclass(frozen=True)
class ScoringPass:
    """A paid pass scoring outside any run: its hop is the cycle it scores, or none at all."""

    __pydantic_config__: ClassVar[ConfigDict] = ConfigDict(extra="forbid")

    # Each kind is its own rate bucket.
    pass_kind: MeasurementRole
    kind: Literal["scoring_pass"] = "scoring_pass"


@dataclass(frozen=True)
class EmbeddedRun:
    """A host program's run: a share and a reservation, never a slot, since the host schedules itself."""

    __pydantic_config__: ClassVar[ConfigDict] = ConfigDict(extra="forbid")

    dataset_name: str
    kind: Literal["embedded_run"] = "embedded_run"


@dataclass(frozen=True)
class InnerRun:
    """An L4 inner campaign: a share of what its enclosing run's book has left, and nothing more."""

    __pydantic_config__: ClassVar[ConfigDict] = ConfigDict(extra="forbid")

    dataset_name: str
    kind: Literal["inner_run"] = "inner_run"


# Each paid call outside a run is its own rate bucket.
CallBucket = Literal["checkin", "turn"]


@dataclass(frozen=True)
class PaidCall:
    """One LLM call outside any run (a check-in, a chat turn): a hold, never a slot."""

    __pydantic_config__: ClassVar[ConfigDict] = ConfigDict(extra="forbid")

    bucket: CallBucket
    kind: Literal["paid_call"] = "paid_call"


# Where a launch stands, read off its job's stamps (`Job.state`): ADMITTED holds its money on no
# named cycle yet, HELD names the cycle it holds. It moves only by a `LAUNCH_MOVES` row.
LaunchState = enum.StrEnum("LaunchState", ["ASKED", "ADMITTED", "HELD", "RUNNING", "ENDED"])
# CLAIM: the queue's head takes its slot, still asked; LAND: onto the cycle its mint named, or a
# running launch onto the fork it rebased on.
LaunchMove = enum.StrEnum("LaunchMove", ["CLAIM", "ADMIT", "LAND", "START", "END"])

_S = LaunchState
_LIVE = frozenset(_S) - {_S.ENDED}
# THE launch lifecycle, (sources, targets) per move: anything else raises `LaunchMoveError`.
LAUNCH_MOVES: Mapping[LaunchMove, tuple[AbstractSet[LaunchState], AbstractSet[LaunchState]]] = {
    LaunchMove.CLAIM: ({_S.ASKED}, {_S.ASKED}),
    LaunchMove.ADMIT: ({_S.ASKED}, {_S.ADMITTED, _S.HELD}),
    LaunchMove.LAND: ({_S.ADMITTED, _S.RUNNING}, {_S.HELD, _S.RUNNING}),
    LaunchMove.START: ({_S.ADMITTED, _S.HELD}, {_S.RUNNING}),
    LaunchMove.END: (_LIVE, {_S.ENDED}),
}


class LaunchMoveError(RuntimeError):
    """A move the lifecycle does not declare: the caller's bug, never a refusal to serve."""


SlotHolder = Annotated[CampaignLaunch | ScoringPass, Field(discriminator="kind")]
JobHolder = Annotated[
    CampaignLaunch | ScoringPass | EmbeddedRun | InnerRun | PaidCall, Field(discriminator="kind")
]


@dataclass(frozen=False)
class Job:
    """Outlives its release to the end of its UTC day: the daily launch quota counts it. It holds
    what the quota, a landing and the live listings read, never how its run ended: that is its
    cycle's stop, or its command's refusal (`command_reading.py::job_status`)."""

    __pydantic_config__: ClassVar[ConfigDict] = ConfigDict(extra="forbid")

    job_id: str
    user_id: str
    # WHO launched it, never the account: a delegate shares its owner's `user_id`.
    principal_id: str
    # The store its hop names a cycle in: an L4 sandbox repeats content-addressed campaign ids.
    workspace: str
    campaign_id: str
    cycle_id: str
    # WHAT holds (or waits for) the slot, or the spend alone.
    holder: JobHolder
    stage: JobStage
    created_at: str
    started_at: str | None
    # A release is its own act, never a fourth stage: `stage` stays where the launch got to.
    released_at: str | None
    # The code its producer runs, stamped with `producer_id`: a landing waits on a live job on another.
    code_revision: str
    # Unset = never granted a run, so its failure is a refusal.
    admitted_at: str | None = None
    producer_id: str = ""

    @property
    def what(self) -> str:
        """What holds the slot, in words: every surface naming a job says it this way."""
        resolved = self.hop != UNRESOLVED_HOP
        where = f"{self.campaign_id}/{self.cycle_id}"
        match self.holder:
            case CampaignLaunch(dataset_name=dataset):
                return where if resolved else f"a new campaign on {dataset}"
            case ScoringPass(pass_kind=pass_kind):
                return f"{pass_kind} on {where if resolved else 'the workspace'}"
            case EmbeddedRun(dataset_name=dataset):
                return f"an embedded run on {where if resolved else dataset}"
            case InnerRun(dataset_name=dataset):
                return f"an inner run on {where if resolved else dataset}"
            case PaidCall(bucket=bucket):
                return f"a {bucket} call"
            case _:
                assert_never(self.holder)

    @property
    def hop(self) -> CycleHop:
        return CycleHop(campaign_id=self.campaign_id, cycle_id=self.cycle_id)

    def on_campaign(self, workspace: WorkspaceDir, campaign_id: str) -> bool:
        return self.workspace == str(workspace) and self.campaign_id == campaign_id

    @property
    def admitted(self) -> bool:
        """Its reservation is stamped and its cycle's share landed: before this, a failure is a refusal."""
        return self.admitted_at is not None

    @property
    def released(self) -> bool:
        return self.released_at is not None

    def launched_on(self, day: str) -> bool:
        return self.created_at.startswith(day)

    @property
    def queued(self) -> bool:
        return not self.released and self.stage is RunPhase.QUEUED

    @property
    def holds_spend(self) -> bool:
        """Its reservation and its share bind the account and the campaign while this holds."""
        return not self.released and self.stage in SLOT_STAGES

    @property
    def state(self) -> LaunchState:
        if self.released:
            return LaunchState.ENDED
        if self.stage is RunPhase.RUNNING:
            return LaunchState.RUNNING
        if not self.admitted:
            return LaunchState.ASKED
        return LaunchState.ADMITTED if self.hop == UNRESOLVED_HOP else LaunchState.HELD

    @property
    def holds_slot(self) -> bool:
        return self.holds_spend and not isinstance(self.holder, EmbeddedRun | InnerRun | PaidCall)


JOB_ADAPTER = TypeAdapter(Job)
# The live-jobs index: each unreleased job's id, kept by `JobRegistry`'s writer.
LIVE_JOB_IDS = TypeAdapter(tuple[str, ...])
# A periodic step's faults, its last pass's whole: the sweep's unreadable cycles, or its raise.
UNSWEPT_FAULTS = TypeAdapter(tuple[str, ...])
