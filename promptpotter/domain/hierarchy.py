from __future__ import annotations

from typing import Annotated, Literal

from pydantic import Field

from promptpotter.domain.campaign import CampaignRunsWith, LifecycleStatus
from promptpotter.domain.cycle_index import CycleIndex, RunStatus
from promptpotter.domain.opt_search_point import Variation
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
from promptpotter.domain.results import ArmReading, DisplayMetric, RunStanding
from promptpotter.domain.run_records import ForkDirection, ForkTrigger
from promptpotter.domain.spend import LifetimeSpend
from promptpotter.domain.strict_model import FrozenModel, StrictModel

type Level = Literal["campaign", "course", "round", "candidate"]
type CourseKind = Literal["root", "fork", "diag", "inner"]
type TagKind = Literal[
    "dataset",
    "optimizer",
    "vendor",
    "mint",
    "lifecycle",
    "self_optimization",
    "arm",
    "superseded",
    "verdict",
]
type StatKey = Literal[
    "spend_lifetime", "spend", "bench", "rounds", "accuracy", "lives", "ability", "lift"
]
type RankMove = Literal["up", "down", "unchanged"]


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
    deletable_stubs: int | None = Field(
        description="How many cycles `cleanup-empty-cycles` deletes here now "
        "(`CampaignStore.deletable_stubs`, the command's own list). Counted on the subtree read "
        "only — every sibling's ledger is scanned per fork — so null in the listing."
    )


class CourseSpawn(FrozenModel):
    """Where a fork was born on its parent's time line: the cut, as an event."""

    parent: str = Field(description="The address of the course it branched from.")
    at_round: int | None = Field(description="The round it branched at; null where none was named.")
    at_offset: int = Field(description="The parent ledger's offset the cut was taken at.")
    from_candidate: str | None = Field(
        description="The address of the parent candidate it hangs from: the one its fork record "
        "names, else the reference its first round re-read, else the parent's origin. Served on "
        "the subtree read; null in the listing, which reads no candidate."
    )
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
    origin: str | None = Field(
        description="The address of the arm the course's standing is paired against: its own "
        "round-0 candidate, else its spawn parent's origin. Served on the subtree read; null in "
        "the listing."
    )
    lens_criterion: str | None = Field(
        description="The `per_cell` formula this course was read under for the request's lens — "
        "a `dials:` lens realized against this campaign's anchors, a `score:` one as given. What a "
        "fork applying the lens carries as `scoring.per_cell`. Null without one."
    )
    lens_shift: LensShift | None = Field(
        description="How the request's lens reorders this course's candidates. Null without a "
        "lens, and where it ranked none of them."
    )


class LensShift(FrozenModel):
    """What the request's lens did to one course's candidate ordering."""

    top_composite: str | None = Field(
        description="The address of the candidate ranked first by composite; null where that "
        "candidate is retired."
    )
    top_lens: str | None = Field(description="The same, ranked by `lens_value`.")
    top_changed: bool = Field(description="The lens ranks another candidate first.")
    moved_up: int
    moved_down: int
    unchanged: int


class LineageDivergence(FrozenModel):
    """Where an alternative criterion would have elected someone else, on the candidate it names."""

    alternative_candidate_id: str | None = Field(
        description="The individual the masked criterion would have elected instead (measured, "
        "so nameable); null when the round would simply have held on origin."
    )


class MainLineStep(FrozenModel):
    """One round on the main line to a candidate."""

    round: int
    candidates: list[str] = Field(
        description="The addresses of the candidates the round's election crowned; at the "
        "candidate's own round, the candidate. Empty: the election crowned nobody."
    )


class RoundFacts(FrozenModel):
    """A round's typed facts: what its election declared and what it measured against."""

    round: int
    elects_on: DisplayMetric | None = Field(
        description="The column the round's election elected on (`Election.elects_on`); null on "
        "a round never elected. `ability`: the round is won on θ, not on accuracy."
    )
    reference: str | None = Field(
        description="The address of the candidate the round measured its arms against — the "
        "individual its `parent`-role arm re-read, in this course or up its spawn chain. Never a "
        "child of the round: a reference is not re-listed. Null at round 0 and where none was read."
    )


class CandidateFacts(FrozenModel):
    """One arm a round proposed; everything measured or decided about it is its `reading`."""

    individual: str = Field(
        description="`OptSearchPoint.id`. Not a key: a later round, or a fork, can propose one "
        "individual again."
    )
    parent_ids: list[str] = Field(
        description="Every individual this one derives from — one for a mutation, several for a "
        "crossover; empty only at an origin."
    )
    variations: list[Variation] = Field(
        description="The variation nodes that wrote this individual, in the order they ran. Empty "
        "on an origin."
    )
    reading: ArmReading = Field(
        description="`reading.arm` is the key every per-cycle document of this course speaks; "
        "`reading.sp_hash` joins to the archive rows. A retired candidate's election wears no "
        "crown: the course that took its place re-asks it."
    )
    stands: bool = Field(
        description="The newest crowned live candidate of its course — the parent the next round "
        "mutates from. At most one per course."
    )
    course_latest: bool = Field(description="The newest candidate its course proposed.")
    answers_for_id: bool = Field(
        description="The ONE candidate across the subtree a remembered individual id resolves "
        "to: the live one where a supersede left the individual on two, the first where several "
        "stand."
    )
    main_line: list[MainLineStep] = Field(
        description="The main line to THIS candidate, origin first: the spawn anchor's line up to "
        "where its course branched, each elected round of its course before it with the "
        "candidates crowned, then this candidate, then any later round that crowned nobody up to "
        "the next crown. Read off crowns, never `parent_ids`."
    )
    lens_value: float | None = Field(
        description="This candidate's composite fitness under the request's `score:` lens. Null "
        "without a lens, or where no row carries a verdict under it."
    )
    lens_rank_move: RankMove | None = Field(
        description="Which way the lens moved this candidate among its course's: its position by "
        "`lens_value` against its position by the reading's composite. Null without a lens, or "
        "where either value is."
    )
    sample_set_accuracy: float | None = Field(
        description="Accuracy over the request's `samples=` subset, served only where this "
        "candidate carries a scoreable verdict for ALL of it. Null without a `samples=` mask."
    )
    sample_set_n: int | None = Field(
        description="How many of the `samples=` subset this candidate carries a scoreable verdict "
        "for. Null without a `samples=` mask."
    )
    divergence: LineageDivergence | None = Field(
        description="Set on a closed round's crowned candidate where the request's lens would "
        "have elected someone else."
    )
    divergent: bool = Field(
        description="Inside the counterfactual below a divergence — the client dims it."
    )


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
    stats: list[HierarchyStat] = Field(description="Server-ordered; `stats[0]` is the headline.")
    child_count: int = Field(
        description="A campaign's courses; a course's rounds on its own ledger, closed and open; "
        "a round's candidates; a candidate's inner campaigns."
    )
    children: list[HierarchyEntry] = Field(
        description="A listing serves a campaign's courses to `?depth=` and nothing under them; "
        "the subtree read serves every level, courses root first, candidates in slot order."
    )

    def tag(self, kind: TagKind) -> HierarchyTag | None:
        return next((t for t in self.tags if t.kind == kind), None)


class _RunEntry(_HierarchyEntry):
    """What only a level with a run of its own carries."""

    state: HierarchyState
    span: HierarchySpan


class HierarchyCampaign(_RunEntry):
    """A campaign as one hierarchy entry: its state and line stats are its line course's."""

    level: Literal["campaign"] = "campaign"
    facts: CampaignFacts


class HierarchyCourse(_RunEntry):
    """A course — one cycle's run, root, fork, diag or inner — as one hierarchy entry."""

    level: Literal["course"] = "course"
    facts: CourseFacts


class HierarchyRound(_HierarchyEntry):
    """One round of a course's own ledger; its children are the candidates it proposed."""

    level: Literal["round"] = "round"
    facts: RoundFacts


class HierarchyCandidate(_HierarchyEntry):
    """One candidate a round proposed; its children are the inner campaigns it spawned."""

    level: Literal["candidate"] = "candidate"
    facts: CandidateFacts


HierarchyEntry = Annotated[
    HierarchyCampaign | HierarchyCourse | HierarchyRound | HierarchyCandidate,
    Field(discriminator="level"),
]


class HierarchyResponse(StrictModel):
    """Every campaign in one store, root-major and newest first."""

    entries: list[HierarchyCampaign] = Field(
        description="Campaigns sharing a root cycle sit together, the group of the newest first."
    )
