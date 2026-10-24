from __future__ import annotations

from typing import Annotated, Literal

from pydantic import Field

from promptpotter.domain.campaign import CampaignRunsWith, LifecycleStatus
from promptpotter.domain.cycle_index import CycleIndex, RunStatus
from promptpotter.domain.phases import (
    PauseReading,
    ProducerAlert,
    ProducerReading,
    ProducerState,
    RunAdmission,
    RunPhase,
    RunState,
    StopReason,
)
from promptpotter.domain.results import RunStanding
from promptpotter.domain.run_records import ForkDirection, ForkTrigger
from promptpotter.domain.spend import LifetimeSpend
from promptpotter.domain.strict_model import FrozenModel, StrictModel

type Level = Literal["campaign", "course"]
type CourseKind = Literal["root", "fork", "diag", "inner"]
type TagKind = Literal[
    "dataset", "optimizer", "vendor", "mint", "lifecycle", "self_optimization", "arm", "superseded"
]
type StatKey = Literal["spend_lifetime", "spend", "bench", "rounds", "accuracy", "lives"]


class HierarchyTag(FrozenModel):
    """One chip an entry wears, worded by the server."""

    kind: TagKind
    value: str = Field(
        description="What a surface routes or branches on: the dataset slug, the vendor id, the "
        "mint word, the successor's address."
    )
    label: str = Field(description="The chip as it reads.")


class HierarchyStat(FrozenModel):
    """One number an entry shows, worded by the server in the order it is served."""

    key: StatKey
    label: str
    value: str = Field(description="The number as it reads: `≥ $1.20`, `3 of 10`, `+2.1 pp`.")
    sub: str | None = Field(description="The second line under the value; null where none.")
    amount: float | None = Field(
        description="The number `value` words, for a meter's geometry only: never re-worded and "
        "never re-ranked. Null where `value` words no number."
    )


class HierarchySpan(FrozenModel):
    """When an entry opened, last moved and closed."""

    opened_at: str
    last_at: str = Field(description="The newest activity under the entry.")
    closed_at: str | None = Field(description="Null while the entry's run has not ended.")


class HierarchyProducer(FrozenModel):
    """A cycle's producer as a listing serves it: what a write moves, never a clock."""

    state: ProducerState
    label: str = Field(description="The state as a walking run's head reads it.")
    attached: bool = Field(description="A process holds this cycle, running or launching it.")
    appending: bool = Field(description="That process is walking the loop.")
    alert: ProducerAlert | None = Field(
        description="What this state raises on its own; how long it has, the dashboard says."
    )

    @classmethod
    def of(cls, reading: ProducerReading) -> HierarchyProducer:
        return cls(
            state=reading.state,
            label=reading.label,
            attached=reading.attached,
            appending=reading.appending,
            alert=ProducerAlert.of(reading.state),
        )


class HierarchyState(FrozenModel):
    """A run's state as one value: the phase, its word and mark, the producer and the verbs."""

    run_phase: RunPhase
    status: RunStatus
    producer: HierarchyProducer
    pause: PauseReading | None = Field(description="Why the run reads paused; null otherwise.")
    admission: RunAdmission
    stop_reason: StopReason | None = Field(description="Why the run ended; null while it has not.")
    intervened: bool = Field(
        description="An operator intervened by hand, so the run is no longer purely reproducible."
    )

    @classmethod
    def of(
        cls, run: RunState, index: CycleIndex | None, *, lifecycle: LifecycleStatus
    ) -> HierarchyState:
        stop = None if index is None else index.stop_reason
        return cls(
            run_phase=run.run_phase,
            status=RunStatus.of(run.run_phase, stop, lifecycle=lifecycle),
            producer=HierarchyProducer.of(run.producer),
            pause=run.pause,
            admission=run.admission,
            stop_reason=stop,
            intervened=index is not None and index.human_intervened,
        )


class CampaignFacts(FrozenModel):
    """A campaign's typed facts, for a pane or a route argument; its rows read the envelope."""

    label: str | None = Field(
        description="The operator's own name for it; null where none was given, and the envelope's "
        "`label` falls back to the dataset."
    )
    root: str = Field(description="The root course's address.")
    line: str = Field(
        description="The address of the course holding the line now: the root, or the fork a "
        "supersede handed it to."
    )
    lifecycle: LifecycleStatus
    spend_lifetime: LifetimeSpend = Field(
        description="Every cycle's bill, forks, forwarded inner spend and deleted cycles included."
    )
    runs_with: CampaignRunsWith | None = Field(
        description="What the root course runs with; null where its pipeline did not resolve."
    )


class CourseSpawn(FrozenModel):
    """Where a fork was born on its parent's time line: the cut, as an event."""

    parent: str = Field(description="The address of the course it branched from.")
    at_round: int | None = Field(description="The round it branched at; null where none was named.")
    at_offset: int = Field(description="The parent ledger's offset the cut was taken at.")
    from_candidate: str | None = Field(description="The arm key it branched from, where one.")
    trigger: ForkTrigger
    direction: ForkDirection = Field(
        description="Which side of the cut the run continues on, measured or implied by `trigger`."
    )
    reason: str
    issued_by: str = Field(description="Who cut it, as its fork record names them.")


class CourseFacts(FrozenModel):
    """A course's typed facts, for a pane or a route argument; its rows read the envelope."""

    mint: CourseKind = Field(description="How the course was minted; the `mint` tag renders it.")
    spawn: CourseSpawn | None = Field(
        description="Null on a root, and on a cycle cut from another tree."
    )
    task: str | None = Field(description="The outer panel cell an L4 inner course measures.")
    standing: RunStanding | None = Field(description="Null before round 0 closes.")
    rounds_closed: int = Field(description="Rounds closed AFTER the origin.")


class _HierarchyEntry(FrozenModel):
    address: str = Field(
        description="Server-minted and stable: the key of every row and every open set. A "
        "course's is its `CyclePath` on the wire (`encodeCyclePath`)."
    )
    parent: str | None = Field(
        description="The hierarchy parent's address; a top-level campaign's is null, an inner "
        "one's the outer course it sits under."
    )
    label: str = Field(description="The display name.")
    lede: str | None = Field(description="The one sentence under the title: the selection line.")
    caveat: str | None = Field(description="What could not be read; null where all was.")
    tags: list[HierarchyTag]
    state: HierarchyState
    stats: list[HierarchyStat] = Field(description="Server-ordered; `stats[0]` is the headline.")
    span: HierarchySpan
    child_count: int = Field(
        description="A campaign's courses; a course's rounds on its ledger, closed and open."
    )
    children: list[HierarchyEntry] = Field(
        description="Served to `?depth=`: a campaign's courses root first; empty under a course."
    )


class HierarchyCampaign(_HierarchyEntry):
    """A campaign as one hierarchy entry: its state and line stats are its line course's."""

    level: Literal["campaign"] = "campaign"
    facts: CampaignFacts


class HierarchyCourse(_HierarchyEntry):
    """A course — one cycle's run, root, fork, diag or inner — as one hierarchy entry."""

    level: Literal["course"] = "course"
    facts: CourseFacts


HierarchyEntry = Annotated[HierarchyCampaign | HierarchyCourse, Field(discriminator="level")]


class HierarchyResponse(StrictModel):
    """Every campaign in one store, root-major and newest first."""

    entries: list[HierarchyCampaign] = Field(
        description="Campaigns sharing a root cycle sit together, the group of the newest first."
    )
