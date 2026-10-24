from __future__ import annotations

import itertools
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import NamedTuple

from promptpotter.domain.bench import BenchReading
from promptpotter.domain.campaign import Campaign
from promptpotter.domain.cycle_listing import CycleIndex, CycleListEntry
from promptpotter.domain.cycle_paths import CycleDir, CycleHop, CyclePath, encode_cycle_path
from promptpotter.domain.hierarchy import HierarchyCourse
from promptpotter.domain.lineage_tree import (
    ArmNode,
    CourseLineage,
    ForkStamp,
    LensShift,
    MainLineStep,
    RankMove,
)
from promptpotter.domain.paired_reading import ArmKey, ArmName
from promptpotter.domain.results import (
    ArmElection,
    ArmFit,
    ArmPanel,
    ArmReading,
    ArmState,
    DisplayMetric,
    Election,
    LineRate,
    ScoreSummary,
    VerifyReading,
    arm_name,
    arm_verdict,
    line_by_individual,
    overlap_line,
    panel_cuts,
)
from promptpotter.domain.run_records import ForkDirection
from promptpotter.infrastructure.ledger import ledger_chain
from promptpotter.infrastructure.projections.cycle_index import read_cycle_index
from promptpotter.infrastructure.store.campaign_store.ledger_scan import (
    StandingRounds,
    scan_bench_readings,
    scan_ledger_verify,
    scan_standing_rounds,
)
from promptpotter.infrastructure.store.layout import (
    CycleLayout,
    cycle_dir_for,
    sibling_kind,
)
from promptpotter.infrastructure.store.read_model import LedgerSpan, Moment
from promptpotter.infrastructure.store.stores import Stores, inner_sandbox_store, resolve_cycle_path
from promptpotter.shared.measurement_context import MeasurementRole

# A cost bound, never a caller's dial: sandboxes nest re-entrantly, so an unbounded walk is unbounded on disk.
_MAX_COURSE_DEPTH = 3


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

    def __init__(self, *, moment: Moment | None, courses: Mapping[str, HierarchyCourse]) -> None:
        self.moment = moment
        self.courses = courses
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


def _read_index(stores: Stores, hop: CycleHop, *, moment: Moment | None) -> CycleIndex | None:
    return read_cycle_index(_layout(stores, hop).cycle_dir, moment=moment)


def _course_edge(index: CycleIndex | None) -> str | None:
    """Null = a campaign root or a rebase fork, attached to the origin."""
    if index is None:
        return None
    if index.fork is not None and index.fork.from_candidate_id:
        return index.fork.from_candidate_id
    if (spawned := index.spawned_by) is not None:
        return spawned.individual_id or None
    return None


class _TreeArm(NamedTuple):
    """An arm its round PROPOSED: a reference a round re-read is a node of the round that walked it."""

    round: int
    key: ArmKey
    state: ArmState
    fold: ScoreSummary | None

    @property
    def id(self) -> str:
        return self.key


def _tree_arms(standing: StandingRounds) -> list[_TreeArm]:
    arms = [
        _TreeArm(rnd, key, held.state, held.fold)
        for (rnd, key), held in standing.arms.items()
        if held.state.role != MeasurementRole.PARENT
    ]
    return sorted(arms, key=lambda a: (a.round, 0 if a.state.slot is None else a.state.slot.idx))


_NO_ELECTION = ArmElection(held=False, selected=False, leading=False, crown=None)


class _RoundFacts(NamedTuple):
    election: ArmElection = _NO_ELECTION
    elects_on: DisplayMetric | None = None
    fit: ArmFit | None = None


def _round_facts(
    standing: StandingRounds, arms: list[_TreeArm]
) -> dict[tuple[int, ArmKey], _RoundFacts]:
    out: dict[tuple[int, ArmKey], _RoundFacts] = {}
    for arm in arms:
        election: Election | None = standing.elections.get(arm.round)
        if election is None and (held := standing.rounds.get(arm.round)) is not None:
            election = held.close.election
        if election is None:
            continue
        out[arm.round, arm.key] = _RoundFacts(
            election=ArmElection.of(
                arm.key,
                held=True,
                selected=election.selected,
                leading=election.leading,
                electable=election.electable,
            ),
            elects_on=election.elects_on,
            fit=election.fit.get(arm.key),
        )
    return out


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
                manifest=_read_index(stores, hop, moment=reads.moment),
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
                    manifest=_read_index(sandbox, hop, moment=reads.moment),
                    inner=True,
                )
            )
    return [c for c in out if c.manifest is not None]


def iter_family_courses(stores: Stores, path: CyclePath) -> list[FamilyCourse]:
    reads = _Reads(moment=None, courses={})
    root_store, _ = resolve_cycle_path(stores, path)
    root = FamilyCourse(
        store=root_store,
        path=path,
        manifest=_read_index(root_store, path[-1], moment=None),
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


def _parent_candidate_of(course: FamilyCourse, candidates: list[_TreeArm]) -> str:
    known = {c.id for c in candidates}
    origin = candidates[0].id if candidates else ""
    cid = _course_edge(course.manifest)
    return cid if cid is not None and cid in known else origin


def _bucket_by_parent(
    courses: list[FamilyCourse], candidates: list[_TreeArm]
) -> dict[str, list[FamilyCourse]]:
    out: dict[str, list[FamilyCourse]] = {}
    for course in courses:
        if target := _parent_candidate_of(course, candidates):
            out.setdefault(target, []).append(course)
    return out


def _retired_by(
    fork: FamilyCourse, candidates: list[_TreeArm], reach: int | None
) -> dict[str, str]:
    spec = None if fork.manifest is None else fork.manifest.fork
    if spec is None or spec.resolved_direction is not ForkDirection.SUPERSEDE or reach is None:
        return {}
    edge = spec.from_candidate_id
    cut = next((i + 1 for i, c in enumerate(candidates) if c.id == edge), None)
    if cut is None:
        cut_round = spec.from_round
        if cut_round is None:
            return {}
        cut = next((i for i, c in enumerate(candidates) if c.round >= cut_round), len(candidates))
    branch = fork.path[-1].cycle_id
    return {c.id: branch for c in candidates[cut:] if c.round <= reach}


def _is_replay(node: ArmNode) -> bool:
    return node.reading.role == MeasurementRole.ORIGIN and bool(node.parent_ids)


def _stamp(course: CourseLineage) -> ForkStamp:
    spawn = course.course.facts.spawn
    return ForkStamp(
        kind=sibling_kind(course.id),
        trigger="" if spawn is None else spawn.trigger.value,
        direction=None if spawn is None else spawn.direction,
        steered_by=None if spawn is None else spawn.issued_by,
        status=course.course.state.status,
        cut_from=None,
    )


def _empty_attempt(course: CourseLineage, *, cut_from: str, round_: int, row: int) -> ArmNode:
    return ArmNode(
        id=course.id,
        row=row,
        parent_ids=[cut_from],
        label=course.label,
        path=course.path,
        reading=ArmReading(
            arm=ArmName(key=ArmKey(course.id), round=round_, label=course.label),
            role=MeasurementRole.PANEL,
            sp_hash="",
            changes_description="",
            state="proposed",
            end=None,
            walked_short=False,
            own=None,
            panel=ArmPanel.of(None, None, None, cut=False),
            tokens=None,
            prefix=None,
            ability=None,
            vs_reference=None,
            election=_NO_ELECTION,
            bench=None,
            verify=None,
            on_origin_panel=None,
        ),
        fork=_stamp(course),
    )


class _Contribution(NamedTuple):
    attempts: list[ArmNode]
    replayed_runs: list[CourseLineage]
    supersedes: bool
    takes_the_line: bool
    reach: int | None
    course: CourseLineage


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
    spec = None if fork.manifest is None else fork.manifest.fork
    direction = None if spec is None else spec.resolved_direction
    return _Contribution(
        attempts,
        [k for c in replays for k in c.children],
        direction is ForkDirection.SUPERSEDE,
        direction is not None and direction is not ForkDirection.OFFSHOOT,
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
    verify: Mapping[ArmKey, VerifyReading]
    bench: Mapping[tuple[int, ArmKey], BenchReading]
    line: Mapping[str, LineRate]


def _arm_node(
    cand: _TreeArm,
    *,
    row: int,
    facts: _RoundFacts,
    readings: _Readings,
    children: list[CourseLineage],
    retired_by: str | None,
    hops: list[CycleHop],
) -> ArmNode:
    election = (
        facts.election
        if retired_by is None
        else facts.election.model_copy(update={"selected": False, "crown": None})
    )
    state = cand.state
    reading = ArmReading.of(
        arm_name(cand.key, state, cand.round),
        state,
        fold=cand.fold,
        cut=False,
        election=election,
        fit=facts.fit,
    )
    lineage = state.individual.lineage
    return ArmNode(
        id=cand.id,
        row=row,
        parent_ids=list(lineage.parent_ids),
        variations=list(lineage.variations),
        label=state.label,
        path=hops,
        reading=reading.on_line(readings.line).model_copy(
            update={
                "verify": readings.verify.get(cand.key),
                "bench": readings.bench.get((cand.round, cand.key)),
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


def _with_heads(root: CourseLineage) -> CourseLineage:
    at_path: dict[tuple[CycleHop, ...], list[ArmNode]] = {}
    at_id: dict[str, ArmNode] = {}
    stands: set[int] = set()

    def collect(course: CourseLineage) -> None:
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

    def stamp(course: CourseLineage) -> CourseLineage:
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
    layout: CycleLayout, spans: list[LedgerSpan], standing: StandingRounds, *, replay: bool
) -> _Readings:
    closes = [standing.rounds[r].close for r in sorted(standing.rounds, reverse=True)]
    return _Readings(
        verify={}
        if replay
        else {key: read for key, (_, read) in scan_ledger_verify(layout.ledger).graded.items()},
        bench=scan_bench_readings(spans),
        line=line_by_individual(
            next(
                (c.overlap for c in closes if c.overlap is not None and overlap_line(c.overlap)),
                None,
            )
        ),
    )


def _build(stores: Stores, path: CyclePath, *, depth: int, reads: _Reads) -> CourseLineage:
    leaf = path[-1]
    index = _read_index(stores, leaf, moment=reads.moment)
    layout = _layout(stores, leaf)
    # A sibling fork's lifted rounds are its parent's nodes, so it reads its own ledger; a cycle
    # cut from ANOTHER tree has no parent node here, and its prefix is its own timeline.
    foreign = index is not None and index.parent is not None and index.parent_cycle_id is None
    spans = (
        ledger_chain(CycleDir(layout.cycle_dir), moment=reads.moment)
        if foreign
        else [
            LedgerSpan(layout.ledger) if reads.moment is None else reads.moment.span(layout.ledger)
        ]
    )
    standing = scan_standing_rounds(spans)
    candidates = _tree_arms(standing)
    children = _child_courses(stores, path, reads)
    inner = [c for c in children if c.inner]
    forks = sorted(
        (c for c in children if not c.inner), key=lambda c: (c.created_at, c.path[-1].cycle_id)
    )
    buckets = _bucket_by_parent(inner, candidates)
    hops = list(path)

    decided = _round_facts(standing, candidates)
    readings = _readings(layout, spans, standing, replay=reads.moment is not None)

    # Forks resolve first: a replayed origin grafts its runs onto the candidate it replays.
    by_id = {c.id: c for c in candidates}
    contributions: list[_Contribution] = []
    grafts: dict[str, list[CourseLineage]] = {}
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
                facts=decided.get((cand.round, cand.key), _NO_ROUND_FACTS),
                readings=readings,
                children=[
                    _build(c.store, c.path, depth=depth - 1, reads=reads)
                    for c in buckets.get(cand.id, [])
                    if depth > 0
                ]
                + grafts.get(cand.id, []),
                retired_by=retired.get(cand.id),
                hops=hops,
            )
            for cand in candidates
        ],
        contributions,
    )

    branch = next((c.course for c in reversed(contributions) if c.takes_the_line), None)

    # After the fold: a fork's attempts arrive naming its replayed C0 and judged short against its own rounds.
    origin_row = _origin_row(kids, candidates[0].id) if candidates else None
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

    edge_id = _course_edge(index)
    return CourseLineage(
        id=leaf.cycle_id,
        origin_row=origin_row,
        parent_ids=[edge_id]
        if edge_id
        else (list(candidates[0].state.individual.lineage.parent_ids) if candidates else []),
        label=leaf.cycle_id,
        path=hops,
        children=kids,
        course=reads.courses[encode_cycle_path(path)] if branch is None else branch.course,
        # A fork's course is folded into its parent's and never served: only a family root is asked.
        deletable_stubs=len(stores.campaigns.deletable_stubs(leaf))
        if sibling_kind(leaf.cycle_id) == "root"
        else 0,
    )


def build_lineage_tree(
    stores: Stores,
    path: CyclePath,
    *,
    moment: Moment | None,
    courses: Mapping[str, HierarchyCourse],
) -> CourseLineage:
    """*moment* replays the ledgers only; *courses* is every family course's entry by address."""
    store_at, _ = resolve_cycle_path(stores, path)
    reads = _Reads(moment=moment, courses=courses)
    return _with_heads(_build(store_at, path, depth=_MAX_COURSE_DEPTH, reads=reads))
