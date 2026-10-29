from __future__ import annotations

from collections.abc import Iterator, Sequence
from typing import Annotated, Literal

from pydantic import Field

from promptpotter.domain.activity import ActivityItem
from promptpotter.domain.arm_reading import ArmReading, DisplayMetric
from promptpotter.domain.bench import BenchStatus
from promptpotter.domain.campaign import CampaignRunsWith, LifecycleStatus
from promptpotter.domain.cycle_index import CycleIndex, RunStatus
from promptpotter.domain.opt_search_point import Variation
from promptpotter.domain.paired_reading import PairedReading
from promptpotter.domain.phases import (
    PauseReading,
    RunAdmission,
    RunPhase,
    RunState,
    stop_next_step,
)
from promptpotter.domain.run_records import ForkDirection, ForkTrigger
from promptpotter.domain.run_standing import RunStanding
from promptpotter.domain.spend import LifetimeSpend
from promptpotter.domain.strict_model import FrozenModel, StrictModel

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
# A plain alias: a discriminator reads through no `type` statement.
StatKey = Literal["spend_lifetime", "spend", "rounds", "accuracy", "lives", "ability", "lift"]
type RankMove = Literal["up", "down", "unchanged"]

# A step window: what one request is served, and the most a store's fold keeps.
DEFAULT_STEP_LIMIT = 200
MAX_STEP_LIMIT = 1000


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


class BenchStat(FrozenModel):
    """The bench as one stat: its wording, and the readings a control or a headline acts on."""

    key: Literal["bench"] = "bench"
    label: str
    value: str
    sub: str | None = Field(description="`BenchScore.line`, or why the bench is not read here.")
    status: BenchStatus | None = Field(
        description="Null on a course beside the line, which no command grades."
    )
    vs_origin: PairedReading | None = Field(
        description="`BenchScore.vs_origin`; null with `status`."
    )
    lift_per_usd: float | None = Field(
        description="`BenchScore.cost.lift_per_usd`; null where the lift is unpriced or unread."
    )


type EntryStat = Annotated[HierarchyStat | BenchStat, Field(discriminator="key")]


class HierarchySpan(FrozenModel):
    """When an entry opened and last moved."""

    opened_at: str
    last_at: str = Field(description="The newest activity under the entry.")


class HierarchyState(FrozenModel):
    """A run's state as one value: the phase, its word and mark, whether it is held, the verbs."""

    run_phase: RunPhase
    status: RunStatus
    attached: bool = Field(description="A process holds this cycle, running or launching it.")
    pause: PauseReading | None = Field(description="Why the run reads paused; null otherwise.")
    admission: RunAdmission
    next_step: str = Field(
        description="What the operator does now, as the run's stop reason states it "
        "(`STOP_REASON_INFO::next_step`) — the sentence the terminal and `review.md` advise. "
        "Empty while the run has not stopped, or where its reason owes nothing."
    )
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
            attached=run.producer.attached,
            pause=run.pause,
            admission=run.admission,
            next_step=stop_next_step(stop),
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
    cut_round: int = Field(
        description="The first round it ran itself: every round below it is its parent line's."
    )
    trigger: ForkTrigger
    direction: ForkDirection = Field(
        description="Which side of the cut the run continues on, measured or implied by `trigger`."
    )
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
    elects_on: DisplayMetric | None = Field(
        description="The column the course's newest election elected on; null before its first. "
        "`ability`: its rounds are won on θ, not on accuracy."
    )


class SubtreeCampaignFacts(CampaignFacts):
    """What only the subtree read knows: it scans every sibling's ledger."""

    deletable_stubs: int = Field(
        description="How many cycles `cleanup-empty-cycles` deletes here now "
        "(`CampaignStore.deletable_stubs`, the command's own list)."
    )


class SubtreeCourseFacts(CourseFacts):
    """What only the subtree read knows: it reads every candidate, under the request's lens."""

    lens_criterion: str | None = Field(
        description="The `per_cell` formula this course was read under for the request's lens — "
        "a `dials:` lens realized against this campaign's anchors, a `score:` one as given. What a "
        "fork applying the lens carries as `scoring.per_cell`. Null without one."
    )
    lens_shift: LensShift | None = Field(
        description="How the request's lens reorders this course's candidates. Null without a "
        "lens, and where it ranked none of them."
    )

    origin: str | None = Field(
        description="The address of the arm the course's standing is paired against: the "
        "round-0 pick its line holds, whichever course ran it. Null on a branch cut at round 0 "
        "before it measured its own."
    )
    from_candidate: str | None = Field(
        description="The address of the candidate a fork hangs from: the arm its mint record "
        "names, on the course of its parent's line that ran it. Null on a root."
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
        "individual its `parent`-role arm re-read, as the pick this course's line stood on going "
        "into the round. Never a child of the round: a reference is not re-listed. Null at round "
        "0, where none was read, and where the re-read individual is not the line's pick."
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
        description="The main line to THIS candidate, origin first: the line of the candidate its "
        "course hangs from (a fork's from-candidate, an inner course's host) up to the cut, each elected round of its course before it with the "
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


class _HierarchyNode(FrozenModel):
    address: str = Field(
        description="Server-minted and stable: the key of every row and every open set. A "
        "course's is its `CyclePath` on the wire (`encodeCyclePath`)."
    )
    parent: str | None = Field(
        description="The hierarchy parent's address; a top-level campaign's is null, an inner "
        "one's the outer course it sits under."
    )


class _HierarchyEntry(_HierarchyNode):
    """A level a row or a card renders: worded, tagged and counted by the server."""

    label: str = Field(description="The display name.")
    lede: str | None = Field(description="The one sentence under the title: the selection line.")
    caveat: str | None = Field(description="What could not be read; null where all was.")
    tags: list[HierarchyTag]
    stats: list[EntryStat] = Field(description="Server-ordered; `stats[0]` is the headline.")

    def tag(self, kind: TagKind) -> HierarchyTag | None:
        return next((t for t in self.tags if t.kind == kind), None)


class _RunEntry(_HierarchyEntry):
    """What only a level with a run of its own carries."""

    state: HierarchyState
    span: HierarchySpan


class HierarchyCourse(_RunEntry):
    """A course — one cycle's run, root, fork, diag or inner — as the listing serves it."""

    level: Literal["course"] = "course"
    facts: CourseFacts


class HierarchyCampaign(_RunEntry):
    """A campaign as the listing serves it: its state and line stats are its line course's."""

    level: Literal["campaign"] = "campaign"
    facts: CampaignFacts
    children: Sequence[HierarchyCourse] = Field(
        description="Its courses to `?depth=`, root first; none at depth 0."
    )


class HierarchyCandidate(_HierarchyEntry):
    """One candidate a round proposed; its children are the inner campaigns it spawned."""

    level: Literal["candidate"] = "candidate"
    facts: CandidateFacts
    children: list[SubtreeCampaign]


class HierarchyRound(_HierarchyNode):
    """One round of a course's own ledger; its children are the candidates it proposed."""

    level: Literal["round"] = "round"
    facts: RoundFacts
    children: list[HierarchyCandidate] = Field(description="In slot order.")


class SubtreeCourse(HierarchyCourse):
    """A course as the subtree read serves it: its rounds under it."""

    facts: SubtreeCourseFacts
    children: list[Annotated[HierarchyRound | SubtreeCampaign, Field(discriminator="level")]] = (
        Field(
            description="Its rounds in round order, then any inner campaign its sandbox holds "
            "that no candidate's spawn stamp names — that one says so in its `caveat`."
        )
    )


class SubtreeCampaign(HierarchyCampaign):
    """A campaign as the subtree read serves it: every level under it."""

    facts: SubtreeCampaignFacts
    children: Sequence[SubtreeCourse] = Field(description="Its courses, root first.")


class StepFacts(FrozenModel):
    offset: int = Field(
        ge=0,
        description="The line index in its course's OWN ledger — the space `?at=` and "
        "`ProjectionEnvelope.sequence` speak, so a live frame de-duplicates against a step on "
        "(course, offset). SPARSE: a record no line is made of is no step.",
    )
    ts: str = Field(
        description="The record's own time, raised to its file predecessor's when the two invert "
        "(records are stamped at construction but appended later)."
    )
    gap_before_s: float = Field(
        ge=0,
        description="Seconds of silence before it: since the newest step or heartbeat anywhere in "
        "the family that the window knows of; 0 where none. A heartbeat takes no slot of a "
        "window, so a long open call never serves one empty.",
    )
    activity: ActivityItem = Field(
        description="The record's reading. A `running` step with no later step on its course is a "
        "call still open."
    )


class HierarchyStep(_HierarchyNode):
    """One ledger record that reads as a line, under its own course; served in a window only."""

    level: Literal["step"] = "step"
    parent: str = Field(description="The course whose own ledger holds the record.")
    facts: StepFacts


class HierarchySteps(FrozenModel):
    """One window of a family's chronology, oldest-first: a course, its forks and inner runs."""

    steps: list[HierarchyStep]
    cursor_prev: str | None = Field(
        description="Opaque cursor for the window immediately older than this one; null when "
        "this window already reaches the family's beginning."
    )


HierarchyEntry = Annotated[
    SubtreeCampaign | SubtreeCourse | HierarchyRound | HierarchyCandidate,
    Field(discriminator="level"),
]


def entries_under(entry: HierarchyEntry) -> Iterator[HierarchyEntry]:
    yield entry
    for child in entry.children:
        yield from entries_under(child)


class HierarchyResponse(StrictModel):
    """Every campaign in one store, root-major and newest first."""

    entries: list[HierarchyCampaign] = Field(
        description="Campaigns sharing a root cycle sit together, the group of the newest first."
    )
