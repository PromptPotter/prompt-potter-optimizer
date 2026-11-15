"""The deep read: a campaign's cycles, their rounds and candidates, and the inner campaigns under them.

A fork IS a cycle, hung off the candidate it was cut from; status is derived after every line resolves.
"""

from __future__ import annotations

import math
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from difflib import SequenceMatcher
from functools import cached_property, lru_cache
from typing import Any, Literal

from promptpotter.application.hierarchy import (
    CycleReads,
    campaign_cycle_reads,
    campaign_entry,
    candidate_figures,
    candidate_lines,
    caveat_row,
    entries_version,
    superseded_tag,
)
from promptpotter.application.jobs.launcher.admission import deletable_stubs
from promptpotter.application.jobs.registry import JobRegistry
from promptpotter.application.mask.tree_lens import lensed, read_samples
from promptpotter.domain.arm_reading import (
    ARM_VERDICT_LABELS,
    arm_verdict,
)
from promptpotter.domain.campaign import Campaign
from promptpotter.domain.cycle_index import CycleIndex
from promptpotter.domain.cycle_paths import CycleHop, CyclePath, Lens, Subject
from promptpotter.domain.hierarchy import (
    CandidateFacts,
    CandidateStanding,
    GeneSource,
    HierarchyCandidate,
    HierarchyCycle,
    HierarchyEntry,
    HierarchyRound,
    HierarchyTag,
    MainLineStep,
    RoundFacts,
    SubtreeCampaign,
    SubtreeCampaignFacts,
    SubtreeCycle,
    SubtreeCycleEntryFacts,
    entries_under,
)
from promptpotter.domain.job import ScoringPass
from promptpotter.domain.opt_search_point import Locus, OptSearchPoint
from promptpotter.domain.paired_reading import ArmKey, RoundArm
from promptpotter.domain.pass_kinds import PASSES
from promptpotter.domain.records.run_records import ForkDirection, SpawnedBy
from promptpotter.domain.results import Election
from promptpotter.domain.said import Said
from promptpotter.domain.strict_model import FrozenModel
from promptpotter.infrastructure.store.lineage_queries import (
    MAX_CYCLE_DEPTH,
    CycleArm,
    iter_family_cycles,
    read_cycle,
)
from promptpotter.infrastructure.store.read_model import Moment
from promptpotter.infrastructure.store.stores import (
    Stores,
    descend_store,
    inner_sandbox_store,
    resolve_cycle_path,
)
from promptpotter.shared.errors import NotFoundError


@dataclass(frozen=True)
class _Retired:
    by: _ListedCycle


# What a candidate IS, derived once every line is resolved: no fork writes it.
type _Status = Literal["live", "rewound"] | _Retired


@dataclass
class _Read:
    """One cycle's record as one read saw it, and the line it stood on then."""

    cycle: _ListedCycle
    elections: dict[int, Election | None]
    arms: dict[tuple[int, ArmKey], CycleArm]
    rereads: dict[tuple[int, ArmKey], RoundArm]
    below: _Read | None
    owner: dict[int, _Read] = field(init=False)

    def __post_init__(self) -> None:
        cut, below = self.cycle.index.cut_round or 0, self.below
        lifted = {} if below is None else {r: o for r, o in below.owner.items() if r < cut}
        self.owner = lifted | dict.fromkeys(self.elections, self)

    def address(self, arm: RoundArm) -> str | None:
        """The candidate a record names by round and arm on this line: an id alone names none."""
        o = self.owner.get(arm.round)
        return None if o is None else o.cycle.at_round(arm.round, arm.arm)

    def crown(self, round_: int) -> str | None:
        election = self.owner[round_].elections[round_]
        selected = None if election is None else election.selected
        return None if selected is None else self.address(RoundArm(round=round_, arm=selected))

    def holds(self, c: _Candidate) -> bool:
        o = self.owner.get(c.round)
        return o is not None and o.cycle is c.read_in.cycle and (c.round, c.arm.key) in o.arms


@dataclass
class _Candidate:
    address: str
    arm: CycleArm
    # The read it was read in: its cycle's head, or one as of a fork's cut for an arm since re-run.
    read_in: _Read
    status: _Status = "live"
    inner: list[SubtreeCampaign] = field(default_factory=list)
    descends_from: _Candidate | None = None
    donor: _Candidate | None = None
    donated_loci: list[str] = field(default_factory=list)
    gene_sources: list[GeneSource] = field(default_factory=list)

    @property
    def round(self) -> int:
        return self.arm.round

    @property
    def individual(self) -> OptSearchPoint:
        return self.arm.state.individual

    @cached_property
    def loci(self) -> dict[Locus, Any]:
        return self.individual.loci()


# How many earlier candidates a moved locus names as its possible sources.
GENE_SOURCES_PER_LOCUS = 3


@lru_cache(maxsize=4096)
def _text_ratio(a: str, b: str) -> float:
    """Keyed by content: a live tree is rebuilt each poll over the same earlier texts."""
    return SequenceMatcher(None, a, b).ratio()


def _length_bound(a: int, b: int) -> float:
    """The most two texts of these lengths can share: `SequenceMatcher.real_quick_ratio`."""
    return 2.0 * min(a, b) / (a + b) if a + b else 1.0


def _similarity(a: object, b: object) -> float:
    """Text by its matching blocks, shots by the keys they share, anything else equal or not."""
    if isinstance(a, str) and isinstance(b, str):
        return _text_ratio(a, b)
    if isinstance(a, tuple) and isinstance(b, tuple):
        union = set(a) | set(b)
        return len(set(a) & set(b)) / len(union) if union else 1.0
    return 1.0 if a == b else 0.0


def _earlier(c: _Candidate, everyone: list[_Candidate]) -> dict[str, _Candidate]:
    """Each individual measured in a round before *c*'s, as its newest measurement."""
    latest: dict[str, _Candidate] = {}
    for x in sorted(
        (x for x in everyone if x.round < c.round),
        key=lambda x: (c.read_in.holds(x), x.round, x.read_in.cycle is c.read_in.cycle),
    ):
        latest[x.individual.id] = x
    return latest


def _descent(c: _Candidate, everyone: list[_Candidate]) -> None:
    """The candidate *c* descends from: its main (first parent), its donor (second), and the loci each gave."""
    parent_ids = c.individual.lineage.parent_ids
    earlier = _earlier(c, everyone)
    parent = earlier.get(parent_ids[0]) if parent_ids else None
    c.descends_from = parent
    if parent is None:
        return
    mine, theirs = c.loci, parent.loci
    by_name = {locus.name: locus for locus in (*mine, *theirs)}
    moved = c.individual.loci_moved_from(parent.individual)
    c.donor = earlier.get(parent_ids[1]) if len(parent_ids) > 1 else None
    if c.donor is not None:
        # What a variation donated and no later one rewrote: the act's record, never a value match.
        steps = c.individual.lineage.variations
        c.donated_loci = [
            name
            for i, step in enumerate(steps)
            for name in step.donated
            if name in moved and not any(name in later.loci for later in steps[i + 1 :])
        ]
    for name in moved:
        locus = by_name[name]
        if name in c.donated_loci or (value := mine.get(locus)) is None:
            continue
        floor = 0.0 if locus not in theirs else _similarity(value, theirs[locus])
        found = []
        for x in earlier.values():
            if x is parent or x.individual.id == c.individual.id:
                continue
            if (other := x.loci.get(locus)) is None:
                continue
            # `real_quick_ratio`'s bound off the lengths alone: no matcher is built for a pair it rules out.
            if (
                isinstance(value, str)
                and isinstance(other, str)
                and _length_bound(len(value), len(other)) <= floor
            ):
                continue
            if (alike := _similarity(value, other)) > floor:
                found.append((alike, x.round, x))
        found.sort(key=lambda f: (f[0], f[1]), reverse=True)
        c.gene_sources += [
            GeneSource(locus=name, source=x.address, similarity=round(alike, 3))
            for alike, _, x in found[:GENE_SOURCES_PER_LOCUS]
        ]


@dataclass
class _ListedCycle:
    entry: HierarchyCycle
    hop: CycleHop
    index: CycleIndex
    parent: _ListedCycle | None = None
    # Its reads by the ledger offset each stands at: `None` is its head.
    at: dict[int | None, _Read] = field(default_factory=dict)
    # Every arm it ran by round and key — its head's, then those re-run away since a cut — and each
    # round's re-read as the candidate it re-read: looked up once.
    ran: dict[tuple[int, ArmKey], _Candidate] = field(default_factory=dict)
    candidates: list[_Candidate] = field(default_factory=list)
    # Inner campaigns no arm of one of its rounds asked for.
    unplaced: list[SubtreeCampaign] = field(default_factory=list)
    # The candidate it hangs from: a fork's from-candidate, an inner root cycle's host.
    hangs_from: _Candidate | None = None

    @property
    def address(self) -> str:
        return self.entry.address

    @cached_property
    def subject(self) -> Subject:
        return Subject.parse_address(self.address)

    def at_round(self, round_: int, candidate: str = "") -> str:
        """The address of its round *round_*, or of *candidate* in it."""
        return self.subject._replace(round=round_, candidate_id=candidate).address

    @property
    def head(self) -> _Read:
        return self.at[None]

    @property
    def reads(self) -> list[_Read]:
        """Its head, then each read as of a cut, oldest first."""
        return [self.head, *(self.at[o] for o in sorted(o for o in self.at if o is not None))]

    @property
    def reach(self) -> int:
        """How far the branch actually GOT: a branch that died retires nothing."""
        return max((r for r, _ in self.head.arms), default=-1)


def _superseding(cycle: _ListedCycle, cycles: list[_ListedCycle]) -> list[_ListedCycle]:
    """Every fork the line was superseded through below *cycle*, nearest link first, then newest."""
    line: list[_ListedCycle] = []
    level = [cycle]
    while level := [
        child
        for child in cycles
        if any(child.parent is c for c in level)
        and (spec := child.index.fork)
        and spec.resolved_direction is ForkDirection.SUPERSEDE
    ]:
        line += reversed(level)
    return line


def _status(c: _Candidate, superseding: list[_ListedCycle]) -> _Status:
    """Retired iff it is off the line of a supersede fork that reached its round, else rewound."""
    by = [fork for fork in superseding if c.round <= fork.reach and not fork.head.holds(c)]
    if by:
        return _Retired(min(by, key=lambda f: (f.index.cut_round or 0, by.index(f))))
    return "live" if c.read_in is c.read_in.cycle.head else "rewound"


def _main_line(c: _Candidate) -> list[MainLineStep]:
    """As it ran, on the line it was read on, up to the first crown after it."""
    line = c.read_in
    rounds = sorted(line.owner.keys() | {c.round})
    base = line.owner[rounds[0]].cycle if rounds[0] in line.owner else line.cycle
    hung = None if base.index.cut_round else base.hangs_from
    steps = [] if hung is None else _through(hung)
    for round_ in rounds:
        if round_ == c.round:
            steps.append(MainLineStep(round=round_, candidates=[c.address]))
            continue
        if line.owner[round_].elections[round_] is None:
            continue
        crown = line.crown(round_)
        if round_ > c.round and crown is not None:
            break
        steps.append(MainLineStep(round=round_, candidates=[] if crown is None else [crown]))
    return steps


def _through(c: _Candidate) -> list[MainLineStep]:
    line = _main_line(c)
    return line[: next(i for i, step in enumerate(line) if c.address in step.candidates) + 1]


def _unplaced(spawned: SpawnedBy | None, *, asker_listed: bool) -> str:
    """Why no candidate holds a run, in the words of the pass its record names."""
    asked = (
        "it carries no spawn record"
        if spawned is None
        else "it was spawned by an origin pass, which measures no candidate"
        if spawned.role is None
        else "the cycle its record names is not among this campaign's cycles"
        if not asker_listed
        else f"the {spawned.role.value} arm its record names is not on that cycle as read"
        if PASSES[spawned.role].arm
        else f"the pass that spawned it ({spawned.role.value}) is not one of a round's arms"
    )
    return f"No candidate holds it: {asked}, so it hangs under the cycle."


def _fields(entry: FrozenModel) -> dict[str, Any]:
    return {name: getattr(entry, name) for name in type(entry).model_fields}


def _standing(c: _Candidate, *, stood_on: bool) -> CandidateStanding:
    match c.status:
        case _Retired():
            return CandidateStanding.RETIRED
        case "rewound":
            return CandidateStanding.REWOUND_STOOD_ON if stood_on else CandidateStanding.REWOUND
    return CandidateStanding.ORIGIN if c.round == 0 else CandidateStanding.PROPOSED


class _Build:
    """Per-request and thrown away: one subtree read, every cycle read once per offset."""

    def __init__(self, moment: Moment | None) -> None:
        self.moment = moment

    def campaign(
        self,
        stores: Stores,
        inside: CyclePath,
        campaign: Campaign,
        held: CycleReads,
        *,
        depth: int,
        host: _Candidate | None,
    ) -> SubtreeCampaign:
        entry = campaign_entry(stores, inside, campaign, held, depth=1)
        index_of = {hop.cycle_id: index for hop, index, _ in held.cycles}
        cycles = [
            _ListedCycle(listed, hop, index_of[hop.cycle_id])
            for listed in entry.children
            for hop in (Subject.parse_address(listed.address).path[-1],)
        ]
        self._place(stores, cycles, host)
        for cycle in cycles:
            sandbox = inner_sandbox_store(stores, cycle.hop.campaign_id, cycle.hop.cycle_id)
            if sandbox is not None and depth > 0:
                self._inner(sandbox, (*inside, cycle.hop), cycle, cycles, depth=depth - 1)
        return SubtreeCampaign(
            **_fields(entry)
            | {
                "children": [self._cycle(cycle, cycles) for cycle in cycles],
                "facts": SubtreeCampaignFacts(
                    **_fields(entry.facts),
                    deletable_stubs=len(deletable_stubs(stores, campaign.root_hop)),
                ),
            }
        )

    def _place(self, stores: Stores, cycles: list[_ListedCycle], host: _Candidate | None) -> None:
        """Every line first, then every candidate's status as a function of those lines."""
        by_address = {c.address: c for c in cycles}
        for cycle in cycles:
            spawn = cycle.entry.facts.spawn
            cycle.parent = None if spawn is None else by_address.get(spawn.parent)
        for cycle in cycles:
            self._read(stores, cycle, None)
        found: dict[str, _Candidate] = {}
        for cycle in cycles:
            for read in cycle.reads:
                for key, arm in read.arms.items():
                    if key not in cycle.ran:
                        c = _Candidate(cycle.at_round(*key), arm, read)
                        cycle.ran[key] = found[c.address] = c
                        cycle.candidates.append(c)
        for cycle in cycles:
            spec, then = cycle.index.fork, cycle.head.below
            if spec is None:
                cycle.hangs_from = host
            elif then is not None and (address := then.address(spec.from_candidate)) is not None:
                cycle.hangs_from = found[address]
        for cycle in cycles:
            for read in cycle.reads:
                for key, ref in read.rereads.items():
                    if (held := found.get(read.address(ref) or "")) is not None:
                        cycle.ran.setdefault(key, held)
            superseding = _superseding(cycle, cycles)
            for c in cycle.candidates:
                c.status = _status(c, superseding)
        everyone = list(found.values())
        for c in everyone:
            _descent(c, everyone)

    def _read(self, stores: Stores, cycle: _ListedCycle, offset: int | None) -> _Read:
        """*cycle* at its head, or as its ledger stood at a fork's cut, over its parent as of ITS cut."""
        if (read := cycle.at.get(offset)) is None:
            moment = (
                self.moment
                if offset is None
                else Moment(stores.campaigns.ledger(cycle.hop), offset - 1)
            )
            got = read_cycle(stores, cycle.hop, cycle.index, moment=moment)
            cut, parent = cycle.index.forked_at_offset, cycle.parent
            below = None if parent is None or cut is None else self._read(stores, parent, cut)
            read = cycle.at[offset] = _Read(
                cycle, got.rounds, {(a.round, a.key): a for a in got.arms}, got.rereads, below
            )
        return read

    def _inner(
        self,
        sandbox: Stores,
        inside: CyclePath,
        cycle: _ListedCycle,
        siblings: list[_ListedCycle],
        *,
        depth: int,
    ) -> None:
        """Each run hangs under the candidate its spawn record names, looked up, never searched for."""
        by_cycle = {s.hop.cycle_id: s for s in siblings}
        for listed in sandbox.campaigns.list_campaigns(None, lifecycle="all"):
            held = campaign_cycle_reads(sandbox, listed, moment=self.moment)
            index = next((i for hop, i, _ in held.cycles if hop == listed.root_hop), None)
            if index is None:
                continue
            spawned = index.spawned_by
            asker = None if spawned is None else by_cycle.get(spawned.cycle_id or "")
            arm = None if spawned is None else spawned.arm
            host = None if asker is None or arm is None else asker.ran.get((arm.round, arm.arm))
            nested = self.campaign(sandbox, inside, listed, held, depth=depth, host=host)
            if host is not None:
                host.inner.append(nested.model_copy(update={"parent": host.address}))
                continue
            under = asker or cycle
            unplaced = caveat_row("unplaced", _unplaced(spawned, asker_listed=asker is not None))
            under.unplaced.append(
                nested.model_copy(
                    update={"parent": under.address, "lines": [*nested.lines, unplaced]}
                )
            )

    def _cycle(self, cycle: _ListedCycle, siblings: list[_ListedCycle]) -> SubtreeCycle:
        head = cycle.head
        crowned = [
            c for c in cycle.candidates if c.status == "live" and c.arm.reading.election.selected
        ]
        stands = crowned[-1].address if crowned else None
        latest = next((c.address for c in reversed(cycle.candidates) if c.read_in is head), None)
        rounds: list[HierarchyRound] = []
        # A round re-run in place short of where a fork's cut stood still lists what ran there.
        for round_ in sorted(head.elections.keys() | {c.round for c in cycle.candidates}):
            election = head.elections.get(round_)
            elects_on = None if election is None else election.elects_on
            members = [
                self._candidate(
                    cycle,
                    c,
                    stands=stands,
                    latest=latest,
                    stood_on=any(s.head.holds(c) for s in siblings),
                )
                for c in cycle.candidates
                if c.round == round_
            ]
            rounds.append(
                HierarchyRound(
                    address=cycle.at_round(round_),
                    parent=cycle.address,
                    children=members,
                    facts=RoundFacts(round=round_, elects_on=elects_on),
                )
            )
        hung = cycle.hangs_from
        return SubtreeCycle(
            **_fields(cycle.entry)
            | {
                "children": [*rounds, *cycle.unplaced],
                "facts": SubtreeCycleEntryFacts(
                    **_fields(cycle.entry.facts),
                    origin=head.crown(0) if 0 in head.owner else None,
                    from_candidate=None
                    if cycle.index.fork is None or hung is None
                    else hung.address,
                    lens_criterion=None,
                    lens_shift=None,
                ),
            }
        )

    def _candidate(
        self,
        cycle: _ListedCycle,
        c: _Candidate,
        *,
        stands: str | None,
        latest: str | None,
        stood_on: bool,
    ) -> HierarchyCandidate:
        retired = c.status != "live"
        reading = c.arm.reading
        verdict = arm_verdict(reading, retired=retired)
        tags = [HierarchyTag(kind="verdict", value=verdict, label=ARM_VERDICT_LABELS[verdict])]
        if retired:
            by = c.status.by if isinstance(c.status, _Retired) else cycle
            tags.append(superseded_tag(by.address, by.entry.facts.mint))
        lineage = c.arm.state.individual.lineage
        standing = _standing(c, stood_on=stood_on)
        facts = CandidateFacts(
            individual=c.arm.state.individual.id,
            variations=list(lineage.variations),
            standing=standing,
            reading=reading,
            stands=c.address == stands,
            cycle_latest=c.address == latest,
            main_line=_main_line(c),
            descends_from=None if c.descends_from is None else c.descends_from.address,
            donor=None if c.donor is None else c.donor.address,
            donated_loci=c.donated_loci,
            gene_sources=c.gene_sources,
            lens_value=None,
            lens_rank_move=None,
            sample_set_fitness=None,
            sample_set_n=None,
            divergence=None,
            divergent=False,
        )
        return HierarchyCandidate(
            address=c.address,
            parent=cycle.at_round(c.round),
            label=reading.arm.label,
            tags=tags,
            figures=candidate_figures(reading),
            lines=candidate_lines(reading, standing),
            children=list(c.inner),
            facts=facts,
        )


def _restamped(
    root: SubtreeCampaign, stamp: Callable[[HierarchyEntry], dict[str, Any]]
) -> SubtreeCampaign:
    """The tree rebuilt bottom-up, each entry updated with what *stamp* says of it."""

    def walk(entry: HierarchyEntry) -> HierarchyEntry:
        children = [walk(child) for child in entry.children]
        return entry.model_copy(update={"children": children, **stamp(entry)})

    rebuilt = walk(root)
    assert isinstance(rebuilt, SubtreeCampaign)
    return rebuilt


def _read_under(
    root: SubtreeCampaign,
    lens: Lens | None,
    samples: frozenset[int] | None,
    moment: Moment | None,
) -> SubtreeCampaign:
    """Every cycle says what re-read its stats, so a frozen copy never passes as the record."""
    samples = read_samples(lens, samples)
    # Every cycle is cut at the moment's wall clock; its offset is only the viewed leaf's line.
    instant = None if moment is None else moment.instant
    # (the tag a toolbar badge carries, the clause the sentence behind it reads)
    under = [
        *(
            [("abort off" if lens.kind == "abort" else "re-scored", f"the {lens.spelling} lens")]
            if lens is not None
            else []
        ),
        *([("sample set", f"a {len(samples)}-sample set")] if samples else []),
        *(
            []
            if instant is None
            else [
                (
                    "rewound",
                    f"the moment {datetime.fromtimestamp(instant, UTC).isoformat(timespec='seconds')}"
                    if math.isfinite(instant)
                    else "a moment before its ledger's first timestamp",
                )
            ]
        ),
    ]
    if not under:
        return root
    read_under = Said(
        key="read_under",
        tone="attention",
        label=" · ".join(tag for tag, _ in under),
        detail=f"Read under {' and '.join(clause for _, clause in under)}, not the realized "
        "record.",
    )
    return _restamped(
        root,
        lambda e: {"lines": [*e.lines, read_under]} if isinstance(e, SubtreeCycle) else {},
    )


def hierarchy_subtree(
    stores: Stores,
    address: str,
    lens: Lens | None,
    samples: frozenset[int] | None,
    *,
    moment: Moment | None,
) -> HierarchyEntry:
    """The entry at *address* with everything under it; *moment* replays the ledgers only."""
    campaign = stores.campaigns.campaign(Subject.parse_address(address).root_campaign_id)
    held = campaign_cycle_reads(stores, campaign, moment=moment)
    root = _Build(moment).campaign(stores, (), campaign, held, depth=MAX_CYCLE_DEPTH, host=None)
    if lens is not None or samples:
        root = lensed(stores, root, lens, samples)
    root = _read_under(root, lens, samples, moment)
    found = next((entry for entry in entries_under(root) if entry.address == address), None)
    if found is None:
        raise NotFoundError(f"No hierarchy entry at {address!r}")
    return found


def _root_path(stores: Stores, address: str) -> CyclePath:
    subject = Subject.parse_address(address)
    inside = subject.inside
    return (*inside, descend_store(stores, inside).campaigns.campaign(subject.campaign_id).root_hop)


def subtree_moment(
    stores: Stores, address: str, at: int | None, at_cycle: CyclePath
) -> Moment | None:
    """``at`` is a line of *at_cycle*'s own ledger; no cycle named, the campaign root's. A cycle off
    the address's own tree is not found: its ledger is no moment of this subtree."""
    if at is None:
        return None
    if at_cycle and at_cycle[0].campaign_id != Subject.parse_address(address).root_campaign_id:
        raise NotFoundError(f"Cycle '{at_cycle[-1].campaign_id}/{at_cycle[-1].cycle_id}' not found")
    path = at_cycle or _root_path(stores, address)
    leaf_store, leaf = resolve_cycle_path(stores, path)
    layout = leaf_store.campaigns.layout(leaf)
    if not layout.cycle_dir.is_dir():
        raise NotFoundError(f"Cycle '{leaf.campaign_id}/{leaf.cycle_id}' not found")
    return Moment(layout.ledger, at)


def subtree_version(stores: Stores, address: str) -> tuple[object, ...]:
    """Every store the TOP campaign's family spans: the body is built from all of them."""
    top = (stores.campaigns.campaign(Subject.parse_address(address).root_campaign_id).root_hop,)
    held = [c.store for c in iter_family_cycles(stores, top)]
    workspaces = {str(s.campaigns.workspace) for s in held}
    passes = sorted(
        (job.workspace, job.campaign_id, job.cycle_id)
        for jobs_dir in {s.jobs_dir for s in held}
        for job in JobRegistry.attach(jobs_dir).list_running()
        if isinstance(job.holder, ScoringPass) and job.workspace in workspaces
    )
    return (*entries_version(held), *passes)


def subtree_cycle(stores: Stores, path: CyclePath) -> SubtreeCycle:
    """One cycle and its rounds, unlensed and at the head: only its own campaign is built."""
    stores.campaigns.campaign(path[0].campaign_id)
    inside, hop = path[:-1], path[-1]
    leaf = descend_store(stores, inside)
    campaign = leaf.campaigns.load_campaign(hop.campaign_id)
    address = Subject.of(path).address
    if campaign is not None:
        held = campaign_cycle_reads(leaf, campaign, moment=None)
        built = _Build(None).campaign(leaf, inside, campaign, held, depth=0, host=None)
        for cycle in built.children:
            if cycle.address == address:
                return cycle
    raise NotFoundError(f"No hierarchy entry at {address!r}")
