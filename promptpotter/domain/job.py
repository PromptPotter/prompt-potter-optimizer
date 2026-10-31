from __future__ import annotations

from dataclasses import dataclass, field
from typing import Annotated, ClassVar, Literal, assert_never, get_args

from pydantic import ConfigDict, Field, TypeAdapter

from promptpotter.domain.cycle_paths import CycleHop, WorkspaceDir
from promptpotter.domain.phases import LaunchStage, RunPhase
from promptpotter.domain.spend import Budget

# The slot is held before the mint resolves the cycle it will name; `update_target` fills it in.
UNRESOLVED_HOP = CycleHop(campaign_id="", cycle_id="")

JobStage = LaunchStage | Literal[RunPhase.RUNNING]
_JOB_STAGES: frozenset[RunPhase] = frozenset(
    stage for arm in get_args(JobStage) for stage in get_args(arm)
)
SLOT_STAGES: frozenset[RunPhase] = frozenset({RunPhase.STARTING, RunPhase.RUNNING})
assert SLOT_STAGES < _JOB_STAGES, "a slot stage no job can be in"


@dataclass(frozen=True)
class CampaignLaunch:
    """A campaign run: its hop is UNRESOLVED until a fresh mint names the cycle."""

    __pydantic_config__: ClassVar[ConfigDict] = ConfigDict(extra="forbid")

    dataset_name: str
    # The check-in a Start mints from; null for every other launch. A queued one serves its draft queued.
    draft_id: str | None = None
    kind: Literal["campaign_launch"] = "campaign_launch"


# Every paid verb that scores outside a run; each is its own rate bucket.
ScoringVerb = Literal["bench", "noise-floor", "probe-reasoning", "seed-screen", "verify"]


@dataclass(frozen=True)
class ScoringPass:
    """A paid verb scoring outside any run: its hop is the cycle it scores, or none at all."""

    __pydantic_config__: ClassVar[ConfigDict] = ConfigDict(extra="forbid")

    verb: ScoringVerb
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


SlotHolder = Annotated[CampaignLaunch | ScoringPass, Field(discriminator="kind")]
JobHolder = Annotated[
    CampaignLaunch | ScoringPass | EmbeddedRun | InnerRun, Field(discriminator="kind")
]


@dataclass(frozen=False)
class Job:
    """Outlives its release to the end of its UTC day: the daily launch quota counts it."""

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
    # What the account holds back for it, which its run's book reads live; its cycle's share of the
    # campaign's ceiling is the cycle's (`CycleShareRecord`).
    reserve: Budget = field(default_factory=Budget)
    # Unset = never granted a run, so its failure is a refusal.
    admitted_at: str | None = None
    producer_id: str = ""
    # Non-empty IS the refused launch: set only where it was never admitted.
    refusal: str = ""

    @property
    def what(self) -> str:
        """What holds the slot, in words: every surface naming a job says it this way."""
        resolved = self.hop != UNRESOLVED_HOP
        where = f"{self.campaign_id}/{self.cycle_id}"
        match self.holder:
            case CampaignLaunch(dataset_name=dataset):
                return where if resolved else f"a new campaign on {dataset}"
            case ScoringPass(verb=verb):
                return f"{verb} on {where if resolved else 'the workspace'}"
            case EmbeddedRun(dataset_name=dataset):
                return f"an embedded run on {where if resolved else dataset}"
            case InnerRun(dataset_name=dataset):
                return f"an inner run on {where if resolved else dataset}"
            case _:
                assert_never(self.holder)

    @property
    def hop(self) -> CycleHop:
        return CycleHop(campaign_id=self.campaign_id, cycle_id=self.cycle_id)

    def holds(self, workspace: WorkspaceDir, hop: CycleHop) -> bool:
        return self.workspace == str(workspace) and self.hop == hop

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
    def holds_slot(self) -> bool:
        return self.holds_spend and not isinstance(self.holder, EmbeddedRun | InnerRun)


JOB_ADAPTER = TypeAdapter(Job)
