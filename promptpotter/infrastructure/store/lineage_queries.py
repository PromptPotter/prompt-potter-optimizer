from __future__ import annotations

import itertools
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Annotated, Literal, NamedTuple, TypedDict

from pydantic import ConfigDict, Field

from promptpotter.domain.bench import BenchReading
from promptpotter.domain.campaign import Campaign
from promptpotter.domain.cycle_listing import CycleIndex, CycleListEntry, RunStatus
from promptpotter.domain.cycle_paths import CycleHop, CyclePath
from promptpotter.domain.opt_search_point import Variation
from promptpotter.domain.paired_reading import ArmPointer, PairedReading
from promptpotter.domain.phases import RunPhase
from promptpotter.domain.results import (
    ArmAbility,
    ArmElection,
    ArmReading,
    ArmVerdict,
    DisplayMetric,
    LineRate,
    RunStanding,
    VerifyReading,
    arm_verdict,
    line_by_individual,
    overlap_line,
    panel_cuts,
)
from promptpotter.domain.run_records import (
    ElectionRecord,
    ForkDirection,
    LedgerCandidate,
    LedgerFit,
)
from promptpotter.domain.strict_model import StrictModel
from promptpotter.infrastructure.projections.cycle_index import read_cycle_index
from promptpotter.infrastructure.runtime_flags import derive_run_state
from promptpotter.infrastructure.store.campaign_store.ledger_scan import (
    StandingRounds,
    scan_bench_readings,
    scan_ledger_verify,
    scan_standing_rounds,
)
from promptpotter.infrastructure.store.layout import (
    CycleLayout,
    cycle_dir_for,
    in_inner_sandbox,
    sibling_kind,
)
from promptpotter.infrastructure.store.read_model import LedgerSpan, Moment
from promptpotter.infrastructure.store.stores import Stores, inner_sandbox_store, resolve_cycle_path

__all__ = [
    "ArmNode",
    "CourseNode",
    "FamilyCourse",
    "ForkStamp",
    "LensShift",
    "LineageDivergence",
    "LineageNode",
    "MainLineStep",
    "build_lineage_tree",
    "iter_family_courses",
    "rank_moves",
]


CourseKind = Literal["root", "fork", "diag", "inner"]

# A cost bound, never a caller's dial: sandboxes nest re-entrantly, so an unbounded walk is unbounded on disk.
_MAX_COURSE_DEPTH = 3


class LineageDivergence(StrictModel):
    """Where an alternative criterion would have elected someone else, on the node it describes."""

    model_config = ConfigDict(frozen=True)

    alternative_candidate_id: str | None = Field(
        default=None,
        description="The candidate the masked criterion would have elected instead "
        "(measured, so nameable); null when the round would simply have held on origin.",
    )


class ForkStamp(StrictModel):
    """What marks an attempt the operator cut: a fork is NOT a node, so its identity rides them."""

    model_config = ConfigDict(frozen=True)

    kind: CourseKind
    trigger: str
    direction: ForkDirection | None = Field(
        description="Which side of the cut the run CONTINUES on (`FORK_DIRECTION`): `offshoot` "
        "hangs off a line that keeps running, `supersede` IS the line."
    )
    steered_by: str | None = Field(
        description="Who cut the fork, as its fork record names them: an account or delegate id, "
        "`system`, or the layer and round that proposed it. An id, never a display name."
    )
    status: RunStatus = Field(
        description="How the fork's own run reads — what the stand-in row of a branch that "
        "minted nothing shows in place of a level."
    )
    cut_from: str | None = Field(
        description="The timeline label of the attempt this fork was cut from — its first parent, "
        "as the course it sits on numbers it. Null where that parent is not on this timeline."
    )


RankMove = Literal["up", "down", "unchanged"]


class LensShift(StrictModel):
    """What the request's lens did to one course's sibling ordering, over the arms on its timeline."""

    model_config = ConfigDict(frozen=True)

    top_composite: str | None = Field(
        description="The timeline label of the arm ranked first by composite; null where that "
        "arm is on the retired side of a supersede cut."
    )
    top_lens: str | None = Field(description="The same, ranked by `lens_value`.")
    top_changed: bool = Field(description="The lens ranks another arm first.")
    moved_up: int
    moved_down: int
    unchanged: int


class MainLineStep(StrictModel):
    """One round on the main line to an arm."""

    model_config = ConfigDict(frozen=True)

    round: int
    rows: list[int] = Field(
        description="The arms the round's election crowned, each by its `row`; at the head's "
        "round, the head. Empty: the election crowned nobody."
    )


class _Node(StrictModel):
    model_config = ConfigDict(frozen=True)

    id: str = Field(
        description="Course: the cycle_id. Candidate: the searchpoint id minted at L1/origin."
    )
    parent_ids: list[str] = Field(
        default_factory=list,
        description="Every candidate this node derives from — one for a mutation, several for a "
        "crossover; empty only at the true root. Lineage is a DAG; this tree hangs the node "
        "under `parent_ids[0]`. A course carries the same edge its own C0 carries.",
    )
    label: str = Field(
        description="`C{round}.{n}` on the campaign's ONE timeline: this course's own "
        "candidates keep their minted label; an attempt a fork contributed takes the next "
        "free index of its round, by mint time — UNLESS the cut superseded, where it keeps "
        "its own label because it replaced that position rather than joining it, and the "
        "candidate it replaced carries `superseded_by`. So one label can appear twice in a "
        "round: at most once LIVE, the other retired. A course's is its cycle_id.",
    )
    path: list[CycleHop] = Field(
        default_factory=list,
        description="THE address, root → leaf: the course this node belongs to. A candidate "
        "a fork contributed carries the FORK's path, so selecting it re-roots onto that fork.",
    )
    origin_row: int | None = Field(
        default=None,
        description="The `row` of the origin arm — C0 — of the timeline this node is on. A "
        "course names its own; a candidate names its course's, and an attempt a fork contributed "
        "names the origin of the timeline it was folded onto, never the fork's replayed C0. An "
        "inner course starts its own timeline, so its candidates name ITS C0. Where a repair "
        "left the origin's individual on two rows, the one still on a line. Null on a course "
        "that has minted nothing.",
    )
    elects_on: DisplayMetric | None = Field(
        default=None,
        description="The column the selector elects on. Candidate: the declaration its round's "
        "election carried (`RoundResult.elects_on`), null on a round that never elected. Course: "
        "the declaration its own elections carried. `ability`: the rounds are won on theta, not "
        "on accuracy.",
    )


class ArmNode(_Node):
    """One arm on a course's timeline; everything measured or decided about it is its `reading`."""

    kind: Literal["candidate"] = "candidate"
    row: int = Field(
        description="THE key of this row in this response: unique tree-wide, where `id` is not — "
        "a supersede cut leaves one individual on two rows, and a later round can read it again. "
        "Every pointer the tree serves at an arm (`origin_row`, `main_line`) names one. Minted "
        "per build, so it addresses nothing across two responses."
    )
    variations: list[Variation] = Field(
        default_factory=list,
        description="The variation nodes that wrote this arm's individual, in the order they "
        "ran: each `{manifest}:{node}`, whether it computed its result or asked a model, and the "
        "loci it left different. Empty on an origin, and on the stand-in row of a branch.",
    )
    children: list[CourseNode] = Field(
        default_factory=list, description="The runs that measured this arm's individual."
    )
    reading: ArmReading = Field(
        description="`reading.arm` is the arm in the course that MINTED it — the key every "
        "per-cycle document of that course speaks — while `label` above is its position on this "
        "timeline; the two differ only on an attempt a fork contributed and the fold renumbered. "
        "`reading.sp_hash` joins to the archive rows. On the retired side of a supersede cut the "
        "election wears no crown: the branch re-asks it."
    )
    superseded_by: str | None = Field(
        default=None,
        description="The cycle_id of the branch that took this candidate's place. Set on the "
        "LEFT-BEHIND side of a `supersede` cut (`ForkDirection`) — the tail its own course "
        "kept as the record of what ran, while the line continued elsewhere. Null on every "
        "candidate still on a line, including both sides of an `offshoot` or `equivalent` "
        "cut. Served because a fork is NOT a node: without a name the operator sees a "
        "retired attempt and a live one as peers of one round, which is the whole reason a "
        "cut records its direction.",
    )
    fork: ForkStamp | None = Field(
        default=None,
        description="Set on an attempt a fork contributed here; null on a course's own "
        "candidates and on a branch the run moved to, whose attempts ARE the line.",
    )
    verdict: ArmVerdict = Field(
        default="awaiting",
        description="Where this arm stands on its timeline, as one closed value "
        "(`domain/results.py::arm_verdict`); its words are `ARM_VERDICT_LABELS`. Stamped over the "
        "finished tree, like the heads below.",
    )
    stands: bool = Field(
        default=False,
        description="The newest crowned arm still on this timeline — the parent the next round "
        "mutates from. At most one child of a course.",
    )
    course_winner: bool = Field(
        default=False,
        description="The last arm an election crowned among those sharing this node's `path` — "
        "a course's, or a fork's, which has no node of its own to say it.",
    )
    course_latest: bool = Field(
        default=False,
        description="The newest arm minted at this node's `path`, crowned or not.",
    )
    answers_for_id: bool = Field(
        default=False,
        description="The ONE row tree-wide that answers for its `id` — what a remembered "
        "candidate id (a selection, a deep link) resolves to: the row still on a line where a "
        "supersede cut left the individual on two, the first where several stand.",
    )
    main_line: list[MainLineStep] = Field(
        default_factory=list,
        description="The main line to THIS arm at its own address, origin first: each elected "
        "round before it with the arms it crowned, then this arm at its own round, then any "
        "later round that crowned nobody up to the next crown. Read off crowns, never "
        "`parent_ids`: a parent edge names an individual, not the arm that was crowned.",
    )
    lens_value: float | None = Field(
        default=None,
        description="This candidate's composite fitness under the request's `score:` lens — its "
        "rows re-graded per cell under that `per_cell` formula and folded, the number a fresh "
        "run under it reports. Null without a lens, or where no row carries a verdict under it.",
    )
    lens_rank_move: RankMove | None = Field(
        default=None,
        description="Which way the lens moved this arm among its siblings — the bars one chart "
        "draws: its 1-based position by `lens_value` descending against its position by the "
        "reading's composite. An ordering is a score, so the move is served rather than sorted "
        "client-side. Null without a lens, or where either value is.",
    )
    sample_set_accuracy: float | None = Field(
        default=None,
        description="Scorer-faithful accuracy over the request's `samples=` subset, served only "
        "where this candidate carries a scoreable verdict for ALL of it — a rate over part of the "
        "subset sat a different exam, and these are read side by side. Null without a `samples=` "
        "mask, and short of the subset.",
    )
    sample_set_n: int | None = Field(
        default=None,
        description="How many of the `samples=` subset this candidate carries a SCOREABLE verdict "
        "for. The denominator of `sample_set_accuracy` where that is served; below the subset "
        "size, how far short of it the candidate is.",
    )
    divergence: LineageDivergence | None = Field(
        default=None,
        description="Set when the request's lens would have FORKED the record at this node. "
        "Only ever set on a closed round's node.",
    )
    divergent: bool = Field(
        default=False,
        description="This node is inside the counterfactual subtree below a divergence — the "
        "client dims it.",
    )

    @property
    def round(self) -> int:
        return self.reading.arm.round


class CourseNode(_Node):
    """One run, whose children are the arms on its ONE timeline, forks folded in."""

    kind: Literal["course"] = "course"
    children: list[ArmNode] = Field(default_factory=list)
    course_kind: CourseKind
    run_phase: RunPhase = Field(
        description="The ONE server-owned run-state (`derive_run_state`), the same value "
        "`/cycles` serves."
    )
    status: RunStatus = Field(
        description="How the course reads on a row — its phase and why it ended as one word and "
        "one mark, as `/cycles` serves it."
    )
    dataset_name: str
    trigger: str = Field(description="Fork trigger; empty for roots and inner runs.")
    fork_direction: ForkDirection | None = Field(
        description="Which side of this cut the run CONTINUES on, derived from `trigger` "
        "(`FORK_DIRECTION`). `offshoot` = this branch hangs off a line that keeps running; "
        "`supersede` = this branch IS the line and the PARENT is what was left behind. Null "
        "for roots and inner runs, which were not cut from anything. Served, never derived "
        "in the client — the two read identically on disk and only this says them apart.",
    )
    steered_by: str | None = Field(description="As `ForkStamp.steered_by`.")
    task: str | None = Field(
        description="An inner run's benchmark task. Load-bearing: every task runs for every "
        "candidate, so the candidate edge alone does not identify an inner run.",
    )
    run_standing: RunStanding | None = Field(
        description="Where the run stands as the course's newest standing round left it, over "
        "its whole history: its selection, that selection against the origin on the origin "
        "panel, and what it has cost. A course whose line moved to a branch serves the "
        "branch's. Null before round 0 closes.",
    )
    lens_criterion: str | None = Field(
        default=None,
        description="The `per_cell` formula this course's record was read under for the "
        "request's lens — a `dials:` lens realized against this campaign's anchors, a `score:` "
        "one as given. What a fork applying the lens carries as `scoring.per_cell`. Null without "
        "one.",
    )
    lens_shift: LensShift | None = Field(
        default=None,
        description="How the request's lens reorders this course's arms. Null without a lens, "
        "and where it ranked none of them.",
    )


LineageNode = Annotated[CourseNode | ArmNode, Field(discriminator="kind")]

ArmNode.model_rebuild()
CourseNode.model_rebuild()


class FamilyCourse(NamedTuple):
    store: Stores
    path: CyclePath
    # Not `index`: a NamedTuple field by that name shadows `tuple.index`.
    manifest: CycleIndex | None
    inner: bool
    depth: int = 0

    @property
    def created_at(self) -> str:
        return "" if self.manifest is None else self.manifest.created_at


class _Reads:
    """Per-build and thrown away: a memo outliving the request serves a round that has closed."""

    def __init__(self, moment: Moment | None = None) -> None:
        self.moment = moment
        self._cycles: dict[Path, list[CycleListEntry]] = {}
        self._campaigns: dict[tuple[Path, str], Campaign | None] = {}
        # Keyed on the full identity: a cycle_id is a content hash, so campaigns and inner cells repeat it.
        self.seen: set[tuple[Path, str, str]] = set()
        self._rows = itertools.count(1)

    def row(self) -> int:
        return next(self._rows)

    def cycles(self, stores: Stores) -> list[CycleListEntry]:
        if (key := stores.base_dir) not in self._cycles:
            self._cycles[key] = stores.campaigns.enumerate_cycles()
        return self._cycles[key]

    def campaign(self, stores: Stores, campaign_id: str) -> Campaign | None:
        if (key := (stores.base_dir, campaign_id)) not in self._campaigns:
            self._campaigns[key] = stores.campaigns.load_campaign(campaign_id)
        return self._campaigns[key]


def _layout(stores: Stores, hop: CycleHop) -> CycleLayout:
    return CycleLayout(cycle_dir_for(stores.base_dir, hop))


def _read_index(stores: Stores, hop: CycleHop, moment: Moment | None = None) -> CycleIndex | None:
    return read_cycle_index(_layout(stores, hop).cycle_dir, moment)


def _course_edge(index: CycleIndex | None) -> tuple[str | None, str | None]:
    """Both null = a campaign root or a rebase fork, attached to the origin."""
    if index is None:
        return None, None
    if index.fork is not None and index.fork.from_candidate_id:
        return index.fork.from_candidate_id, None
    if (spawned := index.spawned_by) is not None:
        return spawned.candidate_id or None, spawned.candidate_label or None
    return None, None


_NO_ELECTION = ArmElection(held=False, selected=False, leading=False, crown=None)


class _RoundFacts(NamedTuple):
    election: ArmElection = _NO_ELECTION
    elects_on: DisplayMetric | None = None
    ability: ArmAbility | None = None
    vs_reference: PairedReading | None = None


def _round_facts(
    standing: StandingRounds, candidates: list[LedgerCandidate]
) -> dict[str, _RoundFacts]:
    """The join stays on `label`: `candidate_id` names an individual a later round can read again."""
    out: dict[str, _RoundFacts] = {}
    for cand in candidates:
        election = standing.elections.get(cand.round)
        held = standing.rounds.get(cand.round)
        close = None if held is None else held.close
        if election is None and close is None:
            continue
        fit = (election.fit.get(cand.label) if election is not None else None) or LedgerFit()
        row = (
            None
            if close is None
            else next((cs for cs in close.candidate_scores if cs.label == cand.label), None)
        )
        ability = (
            fit
            if row is None or (row.theta, row.theta_se, row.theta_caveat) == (None, None, None)
            else row
        )
        out[cand.candidate_id] = _RoundFacts(
            election=ArmElection.of(
                cand.label,
                held=election is not None,
                selected=() if election is None else election.selected_labels,
                leading=None if close is None else close.leading_label,
                electable=None if close is None else close.electable_count,
            ),
            elects_on=None if election is None else election.elects_on,
            ability=ArmAbility.of(ability.theta, ability.theta_se, ability.theta_caveat),
            vs_reference=fit.vs_reference,
        )
    return out


class _CourseScalars(TypedDict):
    course_kind: CourseKind
    run_phase: RunPhase
    status: RunStatus
    trigger: str
    fork_direction: ForkDirection | None
    steered_by: str | None
    task: str | None
    dataset_name: str
    run_standing: RunStanding | None
    elects_on: DisplayMetric | None


def _course_scalars(
    stores: Stores,
    hop: CycleHop,
    index: CycleIndex | None,
    reads: _Reads,
    elections: Mapping[int, ElectionRecord],
) -> _CourseScalars:
    layout = _layout(stores, hop)

    fork = None if index is None else index.fork
    spawned = None if index is None else index.spawned_by
    campaign = reads.campaign(stores, hop.campaign_id)

    # Inner by where it lives: a rebase pair in the sandbox has no `spawned_by`, and "root" puts two roots in one tree.
    kind: CourseKind = sibling_kind(hop.cycle_id)
    if kind == "root" and (spawned or in_inner_sandbox(stores.projects_root)):
        kind = "inner"

    run = derive_run_state(layout.cycle_dir)
    return {
        "course_kind": kind,
        "run_phase": run.run_phase,
        "status": RunStatus.of(run.run_phase, None if index is None else index.stop_reason),
        "trigger": "" if fork is None else fork.trigger.value,
        "fork_direction": None if fork is None else fork.resolved_direction,
        "steered_by": None if fork is None else fork.issued_by or None,
        "task": None if spawned is None else spawned.task or None,
        "dataset_name": campaign.dataset_name if campaign else "",
        "run_standing": None if index is None else index.standing,
        "elects_on": next((e.elects_on for e in elections.values()), None),
    }


def _child_courses(stores: Stores, path: CyclePath, reads: _Reads) -> list[FamilyCourse]:
    leaf = path[-1]
    reads.seen.add((stores.base_dir, leaf.campaign_id, leaf.cycle_id))
    out: list[FamilyCourse] = []
    for entry in reads.cycles(stores):
        if entry.parent_cycle_id != leaf.cycle_id:
            continue
        if entry.campaign_id != leaf.campaign_id:
            continue
        hop = CycleHop(campaign_id=entry.campaign_id, cycle_id=entry.cycle_id)
        if (key := (stores.base_dir, hop.campaign_id, hop.cycle_id)) in reads.seen:
            continue
        reads.seen.add(key)
        out.append(
            FamilyCourse(
                store=stores,
                path=(*path[:-1], hop),
                manifest=_read_index(stores, hop, reads.moment),
                inner=False,
            )
        )

    sandbox = inner_sandbox_store(stores, leaf.campaign_id, leaf.cycle_id)
    if sandbox is not None:
        for entry in reads.cycles(sandbox):
            if entry.parent_cycle_id:
                continue
            hop = CycleHop(campaign_id=entry.campaign_id, cycle_id=entry.cycle_id)
            if (key := (sandbox.base_dir, hop.campaign_id, hop.cycle_id)) in reads.seen:
                continue
            reads.seen.add(key)
            out.append(
                FamilyCourse(
                    store=sandbox,
                    path=(*path, hop),
                    manifest=_read_index(sandbox, hop, reads.moment),
                    inner=True,
                )
            )
    return out if reads.moment is None else [c for c in out if c.manifest is not None]


def iter_family_courses(stores: Stores, path: CyclePath) -> list[FamilyCourse]:
    reads = _Reads()
    root_store, _ = resolve_cycle_path(stores, path)
    root = FamilyCourse(
        store=root_store,
        path=path,
        manifest=_read_index(root_store, path[-1]),
        inner=False,
    )
    out = [root]
    frontier = [root]
    while frontier:
        level: list[FamilyCourse] = []
        for course in frontier:
            for child in _child_courses(course.store, course.path, reads):
                depth = len(child.path) - len(path)
                if depth > _MAX_COURSE_DEPTH:
                    continue
                level.append(child._replace(depth=depth))
        # Stable order: the time-ray's ETag must hold across identical requests.
        level.sort(key=lambda c: (c.created_at, c.path[-1].campaign_id, c.path[-1].cycle_id))
        out.extend(level)
        frontier = level
    return out


def _parent_candidate_of(course: FamilyCourse, candidates: list[LedgerCandidate]) -> str:
    by_label = {c.label: c.candidate_id for c in candidates}
    known = {c.candidate_id for c in candidates}
    origin = candidates[0].candidate_id if candidates else ""
    cid, label = _course_edge(course.manifest)
    return cid if cid in known else by_label.get(label or "", origin)


def _bucket_by_parent(
    courses: list[FamilyCourse], candidates: list[LedgerCandidate]
) -> dict[str, list[FamilyCourse]]:
    out: dict[str, list[FamilyCourse]] = {}
    for course in courses:
        if target := _parent_candidate_of(course, candidates):
            out.setdefault(target, []).append(course)
    return out


def _retired_by(
    fork: FamilyCourse, candidates: list[LedgerCandidate], reach: int | None
) -> dict[str, str]:
    spec = None if fork.manifest is None else fork.manifest.fork
    if spec is None or spec.resolved_direction is not ForkDirection.SUPERSEDE or reach is None:
        return {}
    edge = spec.from_candidate_id
    cut = next((i + 1 for i, c in enumerate(candidates) if c.candidate_id == edge), None)
    if cut is None:
        cut_round = spec.from_round
        if cut_round is None:
            return {}
        cut = next((i for i, c in enumerate(candidates) if c.round >= cut_round), len(candidates))
    branch = fork.path[-1].cycle_id
    return {c.candidate_id: branch for c in candidates[cut:] if c.round <= reach}


def _is_replay(node: ArmNode) -> bool:
    return node.label == "C0" and bool(node.parent_ids)


def _stamp(course: CourseNode) -> ForkStamp:
    return ForkStamp(
        kind=course.course_kind,
        trigger=course.trigger,
        direction=course.fork_direction,
        steered_by=course.steered_by,
        status=course.status,
        cut_from=None,
    )


def _empty_attempt(course: CourseNode, *, cut_from: str, round_: int, row: int) -> ArmNode:
    return ArmNode(
        id=course.id,
        row=row,
        parent_ids=[cut_from],
        label=course.label,
        path=course.path,
        reading=ArmReading.walking(
            ArmPointer(round=round_, label=course.label, candidate_id=""),
            fold=None,
            scored=None,
            expected=None,
            cached=None,
            cut=False,
            election=_NO_ELECTION,
        ),
        fork=_stamp(course),
    )


class _Contribution(NamedTuple):
    attempts: list[ArmNode]
    replayed_runs: list[CourseNode]
    supersedes: bool
    takes_the_line: bool
    reach: int | None
    course: CourseNode


def _contributions(
    fork: FamilyCourse, *, cut_from: str, cut_round: int, depth: int, reads: _Reads
) -> _Contribution:
    # `depth` passes unchanged: a fork is not a course node, so its candidates sit at this course's depth.
    course = _build(fork.store, fork.path, depth=depth, reads=reads)
    replays = [c for c in course.children if _is_replay(c)]
    replay_ids = {c.id for c in replays}

    attempts: list[ArmNode] = []
    for cand in course.children:
        if _is_replay(cand):
            continue
        attempts.append(
            cand.model_copy(
                update={
                    "parent_ids": [cut_from if p in replay_ids else p for p in cand.parent_ids],
                    "fork": _stamp(course),
                }
            )
        )

    # Before `_empty_attempt` fabricates a row: a stand-in is no evidence of reach.
    reach = max((c.round for c in course.children), default=None)
    if not attempts:
        attempts = [
            _empty_attempt(course, cut_from=cut_from, round_=cut_round + 1, row=reads.row())
        ]
    return _Contribution(
        attempts,
        [k for c in replays for k in c.children],
        course.fork_direction is ForkDirection.SUPERSEDE,
        course.fork_direction is not None and course.fork_direction is not ForkDirection.OFFSHOOT,
        reach,
        course,
    )


def _fold_contributions(kids: list[ArmNode], contributions: list[_Contribution]) -> list[ArmNode]:
    by_round: dict[int, int] = {}
    for k in kids:
        by_round[k.round] = by_round.get(k.round, 0) + 1
    at_id = {k.id: i for i, k in enumerate(kids)}
    positions = {k.label for k in kids}
    for contribution in contributions:
        for attempt in contribution.attempts:
            if contribution.takes_the_line and contribution.reach is not None:
                attempt = attempt.model_copy(update={"fork": None})
            twin = at_id.get(attempt.id)
            if twin is not None and not contribution.supersedes:
                kids[twin] = attempt.model_copy(
                    update={
                        "label": kids[twin].label,
                        "reading": attempt.reading.model_copy(
                            update={"arm": kids[twin].reading.arm}
                        ),
                        "children": [*kids[twin].children, *attempt.children],
                    }
                )
                continue
            if twin is not None:
                attempt = attempt.model_copy(
                    update={"children": [*kids[twin].children, *attempt.children]}
                )
                kids[twin] = kids[twin].model_copy(update={"children": []})
            round_ = attempt.round
            if not (contribution.supersedes and (twin is not None or attempt.label in positions)):
                by_round[round_] = by_round.get(round_, 0) + 1
                attempt = attempt.model_copy(update={"label": f"C{round_}.{by_round[round_]}"})
            positions.add(attempt.label)
            at_id[attempt.id] = len(kids)
            kids.append(attempt)
    # The sort's stability keeps arrival order within a round, the order labels were assigned in.
    kids.sort(key=lambda k: k.round)
    return kids


_NO_ROUND_FACTS = _RoundFacts()


class _Readings(NamedTuple):
    verify: Mapping[str, VerifyReading]
    bench: Mapping[tuple[int, str], BenchReading]
    line: Mapping[str, LineRate]


def _arm_node(
    cand: LedgerCandidate,
    *,
    row: int,
    facts: _RoundFacts,
    readings: _Readings,
    children: list[CourseNode],
    retired_by: str | None,
    hops: list[CycleHop],
) -> ArmNode:
    election = (
        facts.election
        if retired_by is None
        else facts.election.model_copy(update={"selected": False, "crown": None})
    )
    arm = ArmPointer(round=cand.round, label=cand.label, candidate_id=cand.candidate_id)
    if cand.report is None:
        reading = ArmReading.walking(
            arm,
            fold=None,
            scored=None,
            expected=cand.walk_length,
            cached=None,
            cut=False,
            election=election,
            changes_description=cand.lineage.changes_description,
        )
    else:
        reading = ArmReading.of(
            arm,
            cand.report,
            cut=False,
            election=election,
            changes_description=cand.lineage.changes_description,
            ability=facts.ability,
            vs_reference=facts.vs_reference,
        )
    return ArmNode(
        id=cand.candidate_id,
        row=row,
        parent_ids=list(cand.lineage.parent_ids),
        variations=list(cand.lineage.variations),
        label=cand.label,
        path=hops,
        reading=reading.on_line(readings.line).model_copy(
            update={
                "verify": readings.verify.get(cand.label),
                "bench": readings.bench.get((cand.round, cand.label)),
            }
        ),
        elects_on=facts.elects_on,
        superseded_by=retired_by,
        children=children,
    )


def _composite(node: ArmNode) -> float | None:
    own = node.reading.own
    return None if own is None or own.composite is None else own.composite.value


def _sibling_ranks(
    kids: list[ArmNode], value_of: Callable[[ArmNode], float | None]
) -> dict[str, int]:
    scored = {k.id: value for k in kids if (value := value_of(k)) is not None}
    return {
        cid: i + 1
        for i, (cid, _) in enumerate(sorted(scored.items(), key=lambda kv: (-kv[1], kv[0])))
    }


def rank_moves(kids: list[ArmNode]) -> tuple[list[ArmNode], LensShift | None]:
    by_composite = _sibling_ranks(kids, _composite)
    by_lens = _sibling_ranks(kids, lambda k: k.lens_value)
    moved: list[ArmNode] = []
    for k in kids:
        before, after = by_composite.get(k.id), by_lens.get(k.id)
        move: RankMove | None = None
        if before is not None and after is not None:
            move = "up" if after < before else "down" if after > before else "unchanged"
        moved.append(k.model_copy(update={"lens_rank_move": move}))
    if not by_lens:
        return moved, None
    live = [k for k in moved if k.superseded_by is None]
    first_composite = next((cid for cid, rank in by_composite.items() if rank == 1), None)
    first_lens = next((cid for cid, rank in by_lens.items() if rank == 1), None)

    def label(first: str | None) -> str | None:
        return next((k.label for k in live if k.id == first), None)

    return moved, LensShift(
        top_composite=label(first_composite),
        top_lens=label(first_lens),
        top_changed=first_composite is not None
        and first_lens is not None
        and first_composite != first_lens,
        moved_up=sum(k.lens_rank_move == "up" for k in live),
        moved_down=sum(k.lens_rank_move == "down" for k in live),
        unchanged=sum(k.lens_rank_move == "unchanged" for k in live),
    )


def _panel_cuts(kids: list[ArmNode]) -> list[bool]:
    """A cohort per side of a supersede cut: a retired tail is never the fuller panel a live arm is short of."""
    cohorts: dict[tuple[int, str | None], list[int]] = {}
    for i, kid in enumerate(kids):
        cohorts.setdefault((kid.round, kid.superseded_by), []).append(i)
    out = [False] * len(kids)
    for members in cohorts.values():
        cuts = panel_cuts(
            [(kids[i].reading.panel.scored, kids[i].reading.panel.expected) for i in members]
        )
        for i, cut in zip(members, cuts, strict=True):
            out[i] = cut
    return out


def _origin_row(kids: list[ArmNode], origin_id: str) -> int | None:
    on_origin = [k for k in kids if k.id == origin_id]
    live = next((k for k in on_origin if k.superseded_by is None), None)
    answering = live or (on_origin[-1] if on_origin else None)
    return None if answering is None else answering.row


def _main_line(arms: list[ArmNode], head: ArmNode) -> list[MainLineStep]:
    crowned: dict[int, list[int]] = {}
    elected: set[int] = set()
    for arm in arms:
        if not arm.reading.election.held:
            continue
        elected.add(arm.round)
        if arm.reading.election.selected:
            crowned.setdefault(arm.round, []).append(arm.row)
    steps: list[MainLineStep] = []
    for round_ in sorted(elected | {head.round}):
        picks = crowned.get(round_)
        if round_ == head.round:
            steps.append(MainLineStep(round=round_, rows=[head.row]))
        elif round_ < head.round and picks:
            steps.append(MainLineStep(round=round_, rows=picks))
        elif round_ > head.round and picks:
            break
        else:
            steps.append(MainLineStep(round=round_, rows=[]))
    return steps


def _with_heads(root: CourseNode) -> CourseNode:
    at_path: dict[tuple[CycleHop, ...], list[ArmNode]] = {}
    at_id: dict[str, ArmNode] = {}
    stands: set[int] = set()

    def collect(course: CourseNode) -> None:
        standing: int | None = None
        for arm in course.children:
            at_path.setdefault(tuple(arm.path), []).append(arm)
            held = at_id.get(arm.id)
            if held is None or (held.superseded_by is not None and arm.superseded_by is None):
                at_id[arm.id] = arm
            if arm.reading.election.selected and arm.superseded_by is None:
                standing = arm.row
            for run in arm.children:
                collect(run)
        if standing is not None:
            stands.add(standing)

    collect(root)
    winners: set[int] = set()
    latest: set[int] = set()
    lines: dict[int, list[MainLineStep]] = {}
    for arms in at_path.values():
        newest: ArmNode | None = None
        crowned: ArmNode | None = None
        for arm in arms:
            if newest is None or arm.round >= newest.round:
                newest = arm
            if arm.reading.election.selected and (crowned is None or arm.round >= crowned.round):
                crowned = arm
            lines[arm.row] = _main_line(arms, arm)
        if newest is not None:
            latest.add(newest.row)
        if crowned is not None:
            winners.add(crowned.row)

    def stamp(course: CourseNode) -> CourseNode:
        kids: list[ArmNode] = []
        labels: dict[str, str] = {}
        for arm in course.children:
            labels.setdefault(arm.id, arm.label)
        for arm in course.children:
            at = arm.row
            kids.append(
                arm.model_copy(
                    update={
                        "children": [stamp(run) for run in arm.children],
                        "fork": None
                        if arm.fork is None
                        else arm.fork.model_copy(
                            update={"cut_from": labels.get(next(iter(arm.parent_ids), ""))}
                        ),
                        "verdict": arm_verdict(arm.reading, retired=arm.superseded_by is not None),
                        "stands": at in stands,
                        "course_winner": at in winners,
                        "course_latest": at in latest,
                        "main_line": lines[at],
                        "answers_for_id": at_id[arm.id].row == at,
                    }
                )
            )
        return course.model_copy(update={"children": kids})

    return stamp(root)


def _readings(
    layout: CycleLayout, span: LedgerSpan, standing: StandingRounds, *, replay: bool
) -> _Readings:
    closes = [standing.rounds[r].close for r in sorted(standing.rounds, reverse=True)]
    return _Readings(
        verify={}
        if replay
        else {label: read for label, (_, read) in scan_ledger_verify(layout.ledger).graded.items()},
        bench=scan_bench_readings(span),
        line=line_by_individual(
            next(
                (c.overlap for c in closes if c.overlap is not None and overlap_line(c.overlap)),
                None,
            )
        ),
    )


def _build(stores: Stores, path: CyclePath, *, depth: int, reads: _Reads) -> CourseNode:
    leaf = path[-1]
    index = _read_index(stores, leaf, reads.moment)
    layout = _layout(stores, leaf)
    # The course's own ledger, never its chain: a fork's lifted rounds are its parent's nodes.
    own = LedgerSpan(layout.ledger)
    span = own if reads.moment is None else reads.moment.span(own.path)
    standing = scan_standing_rounds([span])
    candidates = standing.candidates()
    elections = standing.elections
    children = _child_courses(stores, path, reads)
    inner = [c for c in children if c.inner]
    forks = sorted(
        (c for c in children if not c.inner), key=lambda c: (c.created_at, c.path[-1].cycle_id)
    )
    buckets = _bucket_by_parent(inner, candidates)
    hops = list(path)

    decided = _round_facts(standing, candidates)
    readings = _readings(layout, span, standing, replay=reads.moment is not None)

    # Forks resolve first: a replayed origin grafts its runs onto the candidate it replays.
    by_id = {c.candidate_id: c for c in candidates}
    contributions: list[_Contribution] = []
    grafts: dict[str, list[CourseNode]] = {}
    retired: dict[str, str] = {}
    for fork in forks:
        cut_from = _parent_candidate_of(fork, candidates)
        cut = by_id.get(cut_from)
        contribution = _contributions(
            fork, cut_from=cut_from, cut_round=cut.round if cut else 0, depth=depth, reads=reads
        )
        contributions.append(contribution)
        grafts.setdefault(cut_from, []).extend(contribution.replayed_runs)
        retired |= _retired_by(fork, candidates, contribution.reach)

    kids = _fold_contributions(
        [
            _arm_node(
                cand,
                row=reads.row(),
                facts=decided.get(cand.candidate_id, _NO_ROUND_FACTS),
                readings=readings,
                children=[
                    _build(c.store, c.path, depth=depth - 1, reads=reads)
                    for c in buckets.get(cand.candidate_id, [])
                    if depth > 0
                ]
                + grafts.get(cand.candidate_id, []),
                retired_by=retired.get(cand.candidate_id),
                hops=hops,
            )
            for cand in candidates
        ],
        contributions,
    )

    scalars = _course_scalars(stores, leaf, index, reads, elections)
    branch = next((c.course for c in reversed(contributions) if c.takes_the_line), None)
    if branch is not None:
        scalars |= {
            "run_phase": branch.run_phase,
            "status": branch.status,
            "run_standing": branch.run_standing,
        }

    # After the fold: a fork's attempts arrive naming its replayed C0 and judged short against its own rounds.
    origin_row = _origin_row(kids, candidates[0].candidate_id) if candidates else None
    kids = [
        k.model_copy(
            update={
                "origin_row": origin_row,
                "reading": k.reading.model_copy(
                    update={"panel": k.reading.panel.model_copy(update={"cut": cut})}
                ),
            }
        )
        for k, cut in zip(kids, _panel_cuts(kids), strict=True)
    ]

    edge_id, _ = _course_edge(index)
    return CourseNode(
        id=leaf.cycle_id,
        origin_row=origin_row,
        parent_ids=[edge_id]
        if edge_id
        else (list(candidates[0].lineage.parent_ids) if candidates else []),
        label=leaf.cycle_id,
        path=hops,
        children=kids,
        **scalars,
    )


def build_lineage_tree(stores: Stores, path: CyclePath, moment: Moment | None = None) -> CourseNode:
    """*moment* replays the ledgers only: run-state stays the live one, as on a replayed dashboard."""
    store_at, _ = resolve_cycle_path(stores, path)
    return _with_heads(_build(store_at, path, depth=_MAX_COURSE_DEPTH, reads=_Reads(moment)))
