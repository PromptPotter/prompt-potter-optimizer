from __future__ import annotations

from collections.abc import Iterator, Sequence
from enum import StrEnum
from typing import Annotated, Literal

from pydantic import Field

from promptpotter.domain.arm_reading import ArmReading, DisplayMetric
from promptpotter.domain.bench import BenchCurve, BenchScore, PassAdmission
from promptpotter.domain.campaign import CampaignRunsWith, LifecycleStatus
from promptpotter.domain.command_kinds import (
    PAYLOAD_MODEL_FOR_KIND,
    RUN_VERB_KIND,
    CommandKind,
    acts_filed,
)
from promptpotter.domain.cycle_index import CycleIndex, RunStatus
from promptpotter.domain.opt_search_point import Variation
from promptpotter.domain.paired_reading import ArmKey
from promptpotter.domain.phases import (
    RUN_PHASE_INFO,
    STOP_REASON_INFO,
    RunAction,
    RunState,
    StopReading,
    worded_refusal,
)
from promptpotter.domain.records.run_records import ForkDirection, ForkTrigger
from promptpotter.domain.refusal import Refusal
from promptpotter.domain.run_standing import RunStanding
from promptpotter.domain.said import Said
from promptpotter.domain.spend import MeteredSpend
from promptpotter.domain.strict_model import FrozenModel, StrictModel
from promptpotter.shared.errors import ActValue, NextAct

type CycleMint = Literal["root", "fork", "inner"]
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
type RankMove = Literal["up", "down", "unchanged"]
DeleteKind = Literal[CommandKind.DELETE_CAMPAIGN, CommandKind.DISCARD_DRAFT]


class CandidateStanding(StrEnum):
    """What a candidate is on its cycle's record, derived once every line resolves."""

    ORIGIN = "origin"
    PROPOSED = "proposed"
    REWOUND = "rewound"
    # Rewound, and a fork's line stands on it.
    REWOUND_STOOD_ON = "rewound_stood_on"
    RETIRED = "retired"


CANDIDATE_STANDING_INFO: dict[CandidateStanding, str] = {
    CandidateStanding.ORIGIN: "This cycle's ORIGIN: the specification it started from, measured.",
    CandidateStanding.PROPOSED: "A candidate this cycle proposed and measured.",
    CandidateStanding.REWOUND: "Rewound: its cycle re-ran this round in place after a fork was "
    "cut from it. It stays as the record of what ran.",
    CandidateStanding.REWOUND_STOOD_ON: "Rewound: its cycle re-ran this round in place after a "
    "fork was cut from it. It stays as the record of what ran, and a fork's line stands on it.",
    CandidateStanding.RETIRED: "Retired: the run branched and continued in the cycle that took "
    "its place. It stays as the record of what ran, its line as it ran.",
}
assert CANDIDATE_STANDING_INFO.keys() == set(CandidateStanding)

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


class HierarchySpan(FrozenModel):
    """When an entry opened and last moved."""

    opened_at: str
    last_at: str = Field(description="The newest activity under the entry.")


READ_ONLY = worded_refusal("read_only", "You read this run; acting on it is not yours.")


class RunAdmission(FrozenModel):
    """The launch half of a cycle's run verbs, its campaign's refusal and its reader's kinds
    joined: what the one run control offers. The loop's verbs answer off `RunState` alone, which
    no campaign moves."""

    offers: RunAction | None = Field(
        description="The verb the one run control offers; null where it offers none."
    )
    refusal: Refusal | None = Field(
        description="Why the control offers none, with the acts past it; null where it offers one."
    )
    payload: dict[str, dict[str, ActValue]] = Field(
        description="What the offered start or resume sends beside the cycle's address: the "
        "`start-run` its stop names (a DIVERGED stop's `mode.on_divergence: fork`), sent as served."
    )
    stop_acts: tuple[NextAct, ...] = Field(
        description="The acts past the run's stop its reader files: no `start-run` where its "
        "campaign refuses every launch (archived, its definition moved)."
    )

    @classmethod
    def of(
        cls,
        run: RunState,
        *,
        campaign_refusal: Refusal | None,
        filable: tuple[CommandKind, ...],
    ) -> RunAdmission:
        """Read only through `admission.py::admitted`, which joins *campaign_refusal*, the cycle's
        campaign's own, and *filable*, its reader's: a verb either refuses is not offered."""
        info = RUN_PHASE_INFO[run.run_phase]
        pausing = info.action == "pause"
        declined = run.pause_refusal if pausing else run.start_refusal
        refusal: Refusal | None = None
        if info.action is None:
            refusal = info.refusal
        elif declined:
            # The phase names a verb its producer does not admit yet; a pause and an inner run say
            # their own refusal.
            refusal = (
                worded_refusal("run_declined", declined) if pausing or run.inner else info.refusal
            )
        # A resume files what a start does: the launch a campaign may refuse.
        launches = info.action is not None and RUN_VERB_KIND[info.action] is RUN_VERB_KIND["start"]
        refuses_launch = campaign_refusal is not None and launches
        if refuses_launch and refusal is None:
            refusal = campaign_refusal
        if refusal is None:
            # The run asks its verb alone: a reader who may not file it reads the run.
            if info.action and RUN_VERB_KIND[info.action] not in filable:
                refusal = READ_ONLY
        elif refusal.next_acts:
            # Its own refusal stands with the acts past it its reader files; none, it reads the run.
            filed = acts_filed(refusal.next_acts, filable)
            refusal = Refusal(**{**dict(refusal), "next_acts": filed}) if filed else READ_ONLY
        stop = None if run.stop is None else run.stop.stop_reason
        named = () if stop is None else STOP_REASON_INFO[stop].next_acts
        acts = acts_filed(
            tuple(a for a in named if not refuses_launch or a.kind != "start-run"), filable
        )
        return cls(
            offers=info.action if refusal is None else None,
            refusal=refusal,
            payload={} if pausing else {k: v for act in acts for k, v in act.payload.items()},
            stop_acts=acts,
        )


class HierarchyState(FrozenModel):
    """A run's state as one value: its status, whether it is held, the verbs."""

    status: RunStatus
    attached: bool = Field(description="A process holds this cycle, running or launching it.")
    stop: StopReading | None = Field(description="Why the run reads paused or ended, or null.")
    admission: RunAdmission
    intervened: bool = Field(
        description="An operator intervened by hand, so the run is no longer purely reproducible."
    )

    @classmethod
    def of(
        cls,
        run: RunState,
        index: CycleIndex | None,
        *,
        lifecycle: LifecycleStatus,
        admission: RunAdmission,
    ) -> HierarchyState:
        """*admission* is *run*'s with its campaign's refusal joined (`admission.py::admitted`)."""
        stop = None if index is None else index.stop_reason
        return cls(
            status=RunStatus.of(run.run_phase, stop, lifecycle=lifecycle),
            attached=run.producer.attached,
            stop=run.stop,
            admission=admission,
            intervened=index is not None and index.human_intervened,
        )


class CampaignFacts(FrozenModel):
    """A campaign's typed facts, for a pane or a route argument; its rows read the envelope."""

    label: str | None = Field(
        description="The operator's own name for it; null where none was given, and the envelope's "
        "`label` falls back to the dataset."
    )
    short_label: str = Field(
        description="Its name where runs stand side by side: `label`, else its dataset and the "
        "suffix that tells two runs of it apart."
    )
    root: str = Field(description="The root cycle's address.")
    root_label: str = Field(description="The root cycle's served `label`.")
    live_cycle: str = Field(
        description="The live cycle's address: the root, or the fork a supersede handed the "
        "line to."
    )
    lifecycle: LifecycleStatus
    runs_with: CampaignRunsWith | None = Field(
        description="What the root cycle runs with; null where its pipeline did not resolve."
    )


class CycleSpawn(FrozenModel):
    """Where a fork was born on its parent's time line: the cut, as an event."""

    parent: str = Field(description="The address of the cycle it branched from.")
    cut_round: int = Field(
        description="The first round it ran itself: every round below it is its parent line's."
    )
    trigger: ForkTrigger
    direction: ForkDirection = Field(
        description="Which side of the cut the run continues on, measured or implied by `trigger`."
    )
    issued_by: str = Field(description="Who cut it, as its fork record names them.")


class CycleEntryFacts(FrozenModel):
    """A cycle's typed facts, for a pane or a route argument; its rows read the envelope."""

    mint: CycleMint = Field(description="How the cycle was minted; the `mint` tag renders it.")
    spawn: CycleSpawn | None = Field(
        description="Null on a root, and on a cycle cut from another tree."
    )
    task: str | None = Field(description="The outer panel cell an L4 inner cycle measures.")
    standing: RunStanding | None = Field(description="Null before round 0 closes.")
    rounds_closed: int = Field(description="Rounds closed AFTER the origin.")
    elects_on: DisplayMetric | None = Field(
        description="The column the cycle's newest election elected on; null before its first. "
        "`ability`: its rounds are won on θ, not on fitness."
    )
    spend: MeteredSpend = Field(description="This cycle's own ledger's bill, at the moment.")
    bench: BenchScore | None = Field(
        description="The campaign's bench, on the cycle holding the line, read whole at the "
        "head; null on every other cycle (the line's reads `held_elsewhere` there) and under a "
        "moment."
    )
    curve: BenchCurve | None = Field(
        description="The line's held-out curve and its area, where `bench` is served and the "
        "line banked an origin pass; null otherwise."
    )
    verify: PassAdmission | None = Field(
        description="Whether `verify-candidate` on this cycle is admitted now; null under a moment."
    )


class SubtreeCampaignFacts(CampaignFacts):
    """What only the subtree read knows: it scans every sibling's ledger."""

    deletable_stubs: int = Field(
        description="How many cycles `cleanup-empty-cycles` deletes here now "
        "(`admission.py::deletable_stubs`, the command's own list: no held stub counts); none "
        "where its reader may not file it."
    )
    filable: list[CommandKind] = Field(
        description="The kinds this read's reader may file (`command_kinds.py::filable`): what "
        "an act no read offers (limits, look-ahead, a fork, a scenario, a bank) is drawn by."
    )


class SubtreeCycleEntryFacts(CycleEntryFacts):
    """What only the subtree read knows: it reads every candidate, under the request's lens."""

    lens_criterion: str | None = Field(
        description="The `per_cell` formula this cycle was read under for the request's lens — "
        "a `dials:` lens realized against this campaign's anchors, a `score:` one as given. What a "
        "fork applying the lens carries as `scoring.per_cell`. Null without one."
    )
    lens_shift: LensShift | None = Field(
        description="How the request's lens reorders this cycle's candidates. Null without a "
        "lens, and where it ranked none of them."
    )

    origin: str | None = Field(
        description="The address of the arm the cycle's standing is paired against: the "
        "round-0 pick its line holds, whichever cycle ran it. Null on a branch cut at round 0 "
        "before it measured its own."
    )
    from_candidate: str | None = Field(
        description="The address of the candidate a fork hangs from: the arm its mint record "
        "names, on the cycle of its parent's line that ran it. Null on a root."
    )


class LensShift(FrozenModel):
    """What the request's lens did to one cycle's candidate ordering."""

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

    alternative: str | None = Field(
        description="The address of the candidate the masked criterion would have elected instead "
        "(measured, so nameable); null when the round would simply have held on origin."
    )


class MainLineStep(FrozenModel):
    """One round on the main line to a candidate."""

    round: int
    candidates: list[str] = Field(
        description="The addresses of the candidates the round's election crowned; at the "
        "candidate's own round, the candidate. Empty: the election crowned nobody."
    )


class RoundFacts(FrozenModel):
    """A round's typed facts: what its election declared."""

    round: int
    elects_on: DisplayMetric | None = Field(
        description="The column the round's election elected on (`Election.elects_on`); null on "
        "a round never elected. `ability`: the round is won on θ, not on fitness."
    )


class GeneSource(FrozenModel):
    """A candidate one locus of a child may have come from, other than the parent it descends from."""

    locus: str = Field(description="The locus the child moved off its parent, as `node.key`.")
    source: str = Field(description="The address of the candidate whose value it resembles.")
    similarity: float = Field(
        description="How alike the child's value and the source's are, 0 to 1: a verbatim copy "
        "is 1. Always above the child's similarity to its parent on that locus."
    )


class CandidateFacts(FrozenModel):
    """One arm a round proposed; everything measured or decided about it is its `reading`."""

    individual: str = Field(
        description="`OptSearchPoint.id`. Not a key: a later round, or a fork, can propose one "
        "individual again."
    )
    variations: list[Variation] = Field(
        description="The variation nodes that wrote this individual, in the order they ran. Empty "
        "on an origin."
    )
    standing: CandidateStanding
    reading: ArmReading = Field(
        description="`reading.arm` is the key every per-cycle document of this cycle speaks; "
        "`reading.sp_hash` joins to the archive rows. A retired candidate's election is its "
        "round's as recorded: its `retired` verdict says it no longer stands."
    )
    stands: bool = Field(
        description="The newest crowned live candidate of its cycle — the parent the next round "
        "mutates from. At most one per cycle."
    )
    cycle_latest: bool = Field(description="The newest candidate its cycle proposed.")
    main_line: list[MainLineStep] = Field(
        description="The main line to THIS candidate, origin first: the line of the candidate its "
        "cycle hangs from (a fork's from-candidate, an inner cycle's host) up to the cut, each elected round of its cycle before it with the "
        "candidates crowned, then this candidate, then any later round that crowned nobody up to "
        "the next crown. Read off crowns, never `parent_ids`."
    )
    descends_from: str | None = Field(
        description="The address of the candidate its first parent, its main, was last measured as "
        "before it: the ONE edge a lineage view draws it from. Null at an origin, and where no "
        "candidate of its campaign measured that parent."
    )
    donor: str | None = Field(
        description="The address of the candidate its second parent, the donor of a crossover, was "
        "last measured as before it, of any round. Null without a second parent or a main, and "
        "where no candidate of its campaign measured it."
    )
    donated_loci: list[str] = Field(
        description="The loci, as `node.key`, its lineage records the donor gave and no later "
        "variation rewrote. Empty without a donor."
    )
    gene_sources: list[GeneSource] = Field(
        description="Per other locus it moved off its main, the earlier candidates whose value there "
        "is closer to its own than the main's: most similar first, then most recently measured."
    )
    lens_value: float | None = Field(
        description="This candidate's composite fitness under the request's `score:` lens. Null "
        "without a lens, or where no row carries a verdict under it."
    )
    lens_rank_move: RankMove | None = Field(
        description="Which way the lens moved this candidate among its cycle's: its position by "
        "`lens_value` against its position by the reading's composite. Null without a lens, or "
        "where either value is."
    )
    sample_set_fitness: float | None = Field(
        description="Fitness over the request's `samples=` subset, served only where this "
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
        "cycle's is its `CyclePath` on the wire (`encodeCyclePath`)."
    )
    parent: str | None = Field(
        description="The hierarchy parent's address; a top-level campaign's is null, an inner "
        "one's the outer cycle it sits under."
    )


type FigureKey = Literal[
    "state",
    "optimizer",
    "spend",
    "spend_lifetime",
    "bench",
    "rounds",
    "candidates",
    "selection",
    "lift",
    "lift_per_usd",
    "lift_per_candidate",
    "time_per_lift",
    "lives",
    "fitness",
    "fitness_terms",
    "ability",
]


class Figure(Said):
    """An entry's figure: a `Said` row keyed by the closed set code looks a figure up by."""

    key: FigureKey = Field(description="Which figure, for code to look up; never shown.")


class _HierarchyEntry(_HierarchyNode):
    """A level a row or a card renders: named, tagged and figured by the server; its numbers for
    code are `facts`."""

    label: str = Field(description="The display name.")
    tags: list[HierarchyTag]
    figures: list[Figure] = Field(
        description="Its figures for a person, each composed once with a value, in the order a "
        "row shows them."
    )
    lines: list[Said] = Field(
        description="Its sentences under the figures, without a value; what could not be read is "
        "among them."
    )

    def tag(self, kind: TagKind) -> HierarchyTag | None:
        return next((t for t in self.tags if t.kind == kind), None)


class DeleteAct(FrozenModel):
    """What a listed campaign's Delete files, worded by its kind's own payload."""

    kind: DeleteKind
    says: str = Field(description="The payload's own sentence: what the act removes and keeps.")
    keep_results: str | None = Field(
        description="What pressing `keep_results` spares; null where the act takes no such choice."
    )

    @classmethod
    def of(cls, kind: DeleteKind) -> DeleteAct:
        payload = PAYLOAD_MODEL_FOR_KIND[kind]
        keep = payload.model_fields.get("keep_results")
        return cls(kind=kind, says=str(payload.__doc__), keep_results=keep and keep.description)


class _RunEntry(_HierarchyEntry):
    """What only a level with a run of its own carries."""

    state: HierarchyState
    span: HierarchySpan


class HierarchyCycle(_RunEntry):
    """One cycle's run — root, fork or inner — as the listing serves it."""

    entry_kind: Literal["cycle"] = "cycle"
    facts: CycleEntryFacts
    climb: Said | None = Field(
        description="The lead line (`RunStanding.climb`): the selection's lift vs origin and vs "
        "parent, then per search dollar, per candidate tested and in worked time per unit. Its "
        "detail is what they all read in: the measurand and, under an ability election, that "
        "rounds are won on θ. Null until the lift vs origin is read."
    )
    said: Said | None = Field(
        description="The run's pinned line from what no clock reads (`run_said`): a producer's "
        "row is its dashboard's alone. Null where nothing needs the operator."
    )


class HierarchyCampaign(_RunEntry):
    """A campaign as the listing serves it: its state and line stats are its line cycle's."""

    entry_kind: Literal["campaign"] = "campaign"
    facts: CampaignFacts
    delete: DeleteAct = DeleteAct.of(CommandKind.DELETE_CAMPAIGN)
    children: Sequence[HierarchyCycle] = Field(
        description="Its cycles to `?depth=`, root first; none at depth 0."
    )


class HierarchyCandidate(_HierarchyEntry):
    """One candidate a round proposed; its children are the inner campaigns it spawned."""

    entry_kind: Literal["candidate"] = "candidate"
    facts: CandidateFacts
    children: list[SubtreeCampaign]


class HierarchyRound(_HierarchyNode):
    """One round of a cycle's own ledger; its children are the candidates it proposed."""

    entry_kind: Literal["round"] = "round"
    facts: RoundFacts
    children: list[HierarchyCandidate] = Field(description="In slot order.")


class SubtreeCycle(HierarchyCycle):
    """A cycle as the subtree read serves it: its rounds under it."""

    facts: SubtreeCycleEntryFacts
    children: list[
        Annotated[HierarchyRound | SubtreeCampaign, Field(discriminator="entry_kind")]
    ] = Field(
        description="Its rounds in round order, then any inner campaign its sandbox holds "
        "that no candidate's spawn stamp names — that one says so among its `lines`."
    )


class SubtreeCampaign(HierarchyCampaign):
    """A campaign as the subtree read serves it: every level under it."""

    facts: SubtreeCampaignFacts
    children: Sequence[SubtreeCycle] = Field(description="Its cycles, root first.")


class StepFacts(FrozenModel):
    offset: int = Field(
        ge=0,
        description="The line index in its cycle's OWN ledger — the space `?at=` and "
        "`ProjectionEnvelope.sequence` speak, so a live frame de-duplicates against a step on "
        "(cycle, offset). SPARSE: a record no line is made of is no step.",
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
    activity: Said = Field(
        description="The record's line, as the readout log writes it. A `live` step with no later "
        "step on its cycle is a call still open."
    )
    round: int | None = Field(description="The round the record names.")
    arm: ArmKey | None = Field(description="The arm the record is about, by its key.")


class HierarchyStep(_HierarchyNode):
    """One ledger record that reads as a line, under its own cycle; served in a window only."""

    entry_kind: Literal["step"] = "step"
    parent: str = Field(description="The cycle whose own ledger holds the record.")
    facts: StepFacts


class HierarchySteps(FrozenModel):
    """One window of a family's chronology, oldest-first: a cycle, its forks and inner runs."""

    steps: list[HierarchyStep]
    cursor_prev: str | None = Field(
        description="Opaque cursor for the window immediately older than this one; null when "
        "this window already reaches the family's beginning."
    )


HierarchyEntry = Annotated[
    SubtreeCampaign | SubtreeCycle | HierarchyRound | HierarchyCandidate,
    Field(discriminator="entry_kind"),
]


def entries_under(entry: HierarchyEntry) -> Iterator[HierarchyEntry]:
    yield entry
    for child in entry.children:
        yield from entries_under(child)


class UnstartedCampaign(_HierarchyEntry):
    """An open check-in, listed as a campaign: no Start has minted it, so it has no cycle, cycle,
    line or run. Its `address` is the draft id Start mints from and its `delete` discards."""

    entry_kind: Literal["unstarted"] = "unstarted"
    span: HierarchySpan
    delete: DeleteAct = DeleteAct.of(CommandKind.DISCARD_DRAFT)


# What a listing filter picks: a stored lifecycle, or the open check-ins no manifest holds.
type ListingLifecycle = Literal["unstarted", "active", "archived", "deleted", "all"]

ListedCampaign = Annotated[HierarchyCampaign | UnstartedCampaign, Field(discriminator="entry_kind")]


class HierarchyResponse(StrictModel):
    """Every campaign in one store: the unstarted first, then root-major and newest first."""

    entries: list[ListedCampaign] = Field(
        description="Unstarted campaigns lead, most recently edited first; then campaigns sharing "
        "a root cycle sit together, the group of the newest first. A `campaign` or `attached` "
        "filter asks after runs, so it serves no unstarted one."
    )
