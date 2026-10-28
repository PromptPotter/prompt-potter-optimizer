"""The deep read: a campaign's courses, their rounds and candidates, and the inner campaigns under them."""

from __future__ import annotations

import math
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, Literal

from promptpotter.application.hierarchy import (
    campaign_courses,
    campaign_entry,
    entries_version,
    level_text,
    lift_stat,
    superseded_tag,
)
from promptpotter.application.mask.record import Lens
from promptpotter.application.mask.tree_lens import lensed, read_samples
from promptpotter.domain.campaign import Campaign
from promptpotter.domain.cycle_index import CycleIndex
from promptpotter.domain.cycle_paths import (
    HOP_SEP,
    UNIT_SEP,
    CycleHop,
    CyclePath,
    candidate_address,
    decode_cycle_path,
    encode_cycle_path,
    round_address,
)
from promptpotter.domain.hierarchy import (
    CandidateFacts,
    HierarchyCandidate,
    HierarchyCourse,
    HierarchyEntry,
    HierarchyRound,
    HierarchyStat,
    HierarchyTag,
    MainLineStep,
    RoundFacts,
    SubtreeCampaign,
    SubtreeCampaignFacts,
    SubtreeCourse,
    SubtreeCourseFacts,
    entries_under,
)
from promptpotter.domain.paired_reading import ArmKey, RoundArm
from promptpotter.domain.results import (
    ARM_VERDICT_LABELS,
    ArmReading,
    DisplayMetric,
    Election,
    arm_verdict,
    panel_cuts,
)
from promptpotter.domain.ruler import THETA_CAVEAT_INFO
from promptpotter.domain.run_records import ForkDirection, SpawnedBy
from promptpotter.domain.strict_model import FrozenModel
from promptpotter.infrastructure.projections.cycle_index import read_cycle_index
from promptpotter.infrastructure.store.lineage_queries import (
    MAX_COURSE_DEPTH,
    CourseArm,
    iter_family_courses,
    read_course,
)
from promptpotter.infrastructure.store.read_model import Moment
from promptpotter.infrastructure.store.stores import (
    Stores,
    descend_store,
    inner_sandbox_store,
    owned_campaign,
    resolve_cycle_path,
)
from promptpotter.shared.errors import NotFoundError
from promptpotter.shared.measurement_context import ARM_ROLES


def _campaign_of(address: str) -> tuple[CyclePath, str]:
    """The campaign an address sits in, and the store path it is listed inside."""
    head = address.partition("/")[0]
    *above, last = head.split(HOP_SEP)
    inside = decode_cycle_path(HOP_SEP.join(above))
    campaign_id, sep, _ = last.partition(UNIT_SEP)
    return inside, campaign_id if sep else last


def _top_campaign(address: str) -> str:
    """The top-level campaign an address sits under: what a subtree is built and versioned from."""
    inside, campaign_id = _campaign_of(address)
    return inside[0].campaign_id if inside else campaign_id


@dataclass(frozen=True)
class _Retired:
    by: str


# What a candidate IS, derived once every line is resolved: no fork writes it.
type _Status = Literal["live", "rewound"] | _Retired


@dataclass
class _Read:
    """One course's record as one read saw it — at its head, or as of a fork's cut — and the line
    it stood on then: the read owning each round. A round below the cut is its parent's as of that
    cut, so a rewind in place after it moves no fork's line."""

    course: _Course
    elections: dict[int, Election | None]
    arms: dict[tuple[int, ArmKey], CourseArm]
    rereads: dict[tuple[int, ArmKey], RoundArm]
    below: _Read | None
    owner: dict[int, _Read] = field(init=False)

    def __post_init__(self) -> None:
        cut, below = self.course.index.cut_round or 0, self.below
        lifted = {} if below is None else {r: o for r, o in below.owner.items() if r < cut}
        self.owner = lifted | dict.fromkeys(self.elections, self)

    def address(self, arm: RoundArm) -> str | None:
        """The candidate a record names by round and arm on this line: an id alone names none."""
        o = self.owner.get(arm.round)
        return None if o is None else candidate_address(o.course.address, arm.round, arm.arm)

    def crown(self, round_: int) -> str | None:
        election = self.owner[round_].elections[round_]
        selected = None if election is None else election.selected
        return None if selected is None else self.address(RoundArm(round=round_, arm=selected))

    def holds(self, c: _Candidate) -> bool:
        o = self.owner.get(c.round)
        return o is not None and o.course is c.read_in.course and (c.round, c.arm.key) in o.arms


@dataclass
class _Candidate:
    address: str
    arm: CourseArm
    # The read it was read in: its course's head, or one as of a fork's cut for an arm since re-run.
    read_in: _Read
    status: _Status = "live"
    inner: list[SubtreeCampaign] = field(default_factory=list)

    @property
    def round(self) -> int:
        return self.arm.round


@dataclass
class _Course:
    entry: HierarchyCourse
    hop: CycleHop
    index: CycleIndex
    parent: _Course | None = None
    # Its reads by the ledger offset each stands at: `None` is its head.
    at: dict[int | None, _Read] = field(default_factory=dict)
    # Every arm it ran by round and key — its head's, then those re-run away since a cut — and each
    # round's re-read as the candidate it re-read: looked up once.
    ran: dict[tuple[int, ArmKey], _Candidate] = field(default_factory=dict)
    candidates: list[_Candidate] = field(default_factory=list)
    # Inner campaigns no arm of one of its rounds asked for.
    unplaced: list[SubtreeCampaign] = field(default_factory=list)

    @property
    def address(self) -> str:
        return self.entry.address

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


def _superseding(course: _Course, courses: list[_Course]) -> list[_Course]:
    """Every fork the line was superseded through below *course*, nearest link first and, within
    one, the newest — the one holding the line now: an offshoot link stops it."""
    line: list[_Course] = []
    level = [course]
    while level := [
        child
        for child in courses
        if any(child.parent is c for c in level)
        and (spec := child.index.fork)
        and spec.resolved_direction is ForkDirection.SUPERSEDE
    ]:
        line += reversed(level)
    return line


def _status(c: _Candidate, superseding: list[_Course]) -> _Status:
    """Retired iff it is off the line of a supersede fork that reached its round — the lowest cut
    names it, the nearest link of a tie — else rewound where only a read as of a cut holds it."""
    by = [fork for fork in superseding if c.round <= fork.reach and not fork.head.holds(c)]
    if by:
        return _Retired(min(by, key=lambda f: (f.index.cut_round or 0, by.index(f))).address)
    return "live" if c.read_in is c.read_in.course.head else "rewound"


def _main_line(c: _Candidate) -> list[MainLineStep]:
    """As it ran, on the line it was read on: every elected round up to it, then on to the first
    crown after it — a retired crown stands behind every candidate after it."""
    line = c.read_in
    steps: list[MainLineStep] = []
    for round_ in sorted(line.owner.keys() | {c.round}):
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


def _theta(reading: ArmReading, *, elected: bool) -> HierarchyStat | None:
    ability = reading.ability
    if ability is None or (ability.theta is None and ability.caveat is None):
        return None
    value = "—" if ability.theta is None else f"{ability.theta:.2f}"
    if ability.theta is not None and ability.se is not None:
        value += f" ± {ability.se:.2f}"
    sub = (
        "not ability — see below"
        if ability.caveat
        else "what the round elects on"
        if elected
        else None
    )
    return HierarchyStat(key="ability", label="Ability θ", value=value, sub=sub)


def _accuracy(reading: ArmReading) -> HierarchyStat:
    own = reading.own
    accuracy = None if own is None or own.accuracy is None else own.accuracy.value
    scored, expected = reading.panel.scored, reading.panel.expected
    return HierarchyStat(
        key="accuracy",
        label="Accuracy",
        value=level_text("rate", accuracy),
        sub=None
        if scored is None
        else f"{scored}{'' if expected is None else f' of {expected}'} scored",
    )


def _candidate_stats(reading: ArmReading, elects_on: DisplayMetric | None) -> list[HierarchyStat]:
    on_theta = elects_on == "ability"
    theta = _theta(reading, elected=on_theta)
    level = [_accuracy(reading), *([] if theta is None else [theta])]
    if on_theta:
        level.reverse()
    pair = reading.vs_reference
    lift = [] if pair is None else [lift_stat("lift", "Lift vs parent", pair, "parent", notes=[])]
    return [*level, *lift]


def _unplaced(spawned: SpawnedBy | None, *, asker_listed: bool) -> str:
    """Why no candidate holds a run, in the words of the pass its record names."""
    asked = (
        "it carries no spawn record"
        if spawned is None
        else "it was spawned outside any measured pass (the noise-floor diagnostic)"
        if spawned.role is None
        else "the cycle its record names is not among this campaign's courses"
        if not asker_listed
        else f"the {spawned.role.value} arm its record names is not on that course as read"
        if spawned.role in ARM_ROLES
        else f"the pass that spawned it ({spawned.role.value}) is not one of a round's arms"
    )
    return f"No candidate holds it: {asked}, so it hangs under the course."


def _fields(entry: FrozenModel) -> dict[str, Any]:
    return {name: getattr(entry, name) for name in type(entry).model_fields}


def _lede(c: _Candidate, *, stood_on: bool) -> str:
    match c.status:
        case _Retired():
            return (
                "Retired: the run branched and continued in the course that took its place. It "
                "stays as the record of what ran, its line as it ran."
            )
        case "rewound":
            return (
                "Rewound: its course re-ran this round in place after a fork was cut from it. It "
                "stays as the record of what ran"
                + (", and a fork's line stands on it." if stood_on else ".")
            )
    if c.round == 0:
        return "This course's ORIGIN: the specification it started from, measured."
    return "A candidate this course proposed and measured."


class _Build:
    """Per-request and thrown away: one subtree read, every course read once per offset."""

    def __init__(self, moment: Moment | None) -> None:
        self.moment = moment

    def campaign(
        self, stores: Stores, inside: CyclePath, campaign: Campaign, *, depth: int
    ) -> SubtreeCampaign:
        held = campaign_courses(stores, campaign, moment=self.moment)
        entry = campaign_entry(stores, inside, campaign, held, depth=1, moment=self.moment)
        index_of = {hop.cycle_id: index for hop, index, _ in held}
        courses = [
            _Course(listed, hop, index_of[hop.cycle_id])
            for listed in entry.children
            for hop in (decode_cycle_path(listed.address)[-1],)
        ]
        self._place(stores, courses)
        for course in courses:
            sandbox = inner_sandbox_store(stores, course.hop.campaign_id, course.hop.cycle_id)
            if sandbox is not None and depth > 0:
                self._inner(sandbox, (*inside, course.hop), course, courses, depth=depth - 1)
        return SubtreeCampaign(
            **_fields(entry)
            | {
                "children": [self._course(course, courses) for course in courses],
                "facts": SubtreeCampaignFacts(
                    **_fields(entry.facts),
                    deletable_stubs=len(stores.campaigns.deletable_stubs(campaign.root_hop)),
                ),
            }
        )

    def _place(self, stores: Stores, courses: list[_Course]) -> None:
        """Every line first, off the recorded spawn alone — a read pulls its parent's as of its
        cut — then every candidate's status as a function of those lines: no order of courses,
        and no fork placed before another, can change one."""
        by_address = {c.address: c for c in courses}
        for course in courses:
            spawn = course.entry.facts.spawn
            course.parent = None if spawn is None else by_address.get(spawn.parent)
        for course in courses:
            self._read(stores, course, None)
        found: dict[str, _Candidate] = {}
        for course in courses:
            for read in course.reads:
                for key, arm in read.arms.items():
                    if key not in course.ran:
                        c = _Candidate(candidate_address(course.address, *key), arm, read)
                        course.ran[key] = found[c.address] = c
                        course.candidates.append(c)
        for course in courses:
            for read in course.reads:
                for key, ref in read.rereads.items():
                    if (held := found.get(read.address(ref) or "")) is not None:
                        course.ran.setdefault(key, held)
            superseding = _superseding(course, courses)
            for c in course.candidates:
                c.status = _status(c, superseding)

    def _read(self, stores: Stores, course: _Course, offset: int | None) -> _Read:
        """*course* at its head, or as its ledger stood at a fork's cut — the mint record's offset,
        read as the moment a step at that offset names — over its parent as of ITS cut."""
        if (read := course.at.get(offset)) is None:
            moment = (
                self.moment
                if offset is None
                else Moment(stores.campaigns.ledger(course.hop), offset - 1)
            )
            got = read_course(stores, course.hop, course.index, moment=moment)
            cut, parent = course.index.forked_at_offset, course.parent
            below = None if parent is None or cut is None else self._read(stores, parent, cut)
            read = course.at[offset] = _Read(
                course, got.rounds, {(a.round, a.key): a for a in got.arms}, got.rereads, below
            )
        return read

    def _inner(
        self,
        sandbox: Stores,
        inside: CyclePath,
        course: _Course,
        siblings: list[_Course],
        *,
        depth: int,
    ) -> None:
        """Each run hangs under the candidate its spawn record names — the asking course, the
        round and the arm — looked up, never searched for: a fork asks into the sandbox it was
        cut from."""
        by_cycle = {s.hop.cycle_id: s for s in siblings}
        for listed in sandbox.campaigns.list_campaigns(None, lifecycle="all"):
            index = read_cycle_index(
                sandbox.campaigns.cycle_dir(listed.root_hop), moment=self.moment
            )
            if index is None:
                continue
            spawned = index.spawned_by
            asker = None if spawned is None else by_cycle.get(spawned.cycle_id or "")
            arm = None if spawned is None else spawned.arm
            host = None if asker is None or arm is None else asker.ran.get((arm.round, arm.arm))
            nested = self.campaign(sandbox, inside, listed, depth=depth)
            if host is not None:
                host.inner.append(nested.model_copy(update={"parent": host.address}))
                continue
            under = asker or course
            caveat = " ".join(
                text
                for text in (nested.caveat, _unplaced(spawned, asker_listed=asker is not None))
                if text
            )
            under.unplaced.append(
                nested.model_copy(update={"parent": under.address, "caveat": caveat})
            )

    def _course(self, course: _Course, siblings: list[_Course]) -> SubtreeCourse:
        head = course.head
        crowned = [
            c for c in course.candidates if c.status == "live" and c.arm.reading.election.selected
        ]
        stands = crowned[-1].address if crowned else None
        latest = next((c.address for c in reversed(course.candidates) if c.read_in is head), None)
        cohorts: dict[tuple[int, bool], list[_Candidate]] = {}
        for c in course.candidates:
            cohorts.setdefault((c.round, c.status == "live"), []).append(c)
        cut: dict[str, bool] = {}
        for cohort in cohorts.values():
            flags = panel_cuts(
                [(c.arm.reading.panel.scored, c.arm.reading.panel.expected) for c in cohort]
            )
            cut |= {c.address: flag for c, flag in zip(cohort, flags, strict=True)}
        rounds: list[HierarchyRound] = []
        # A round re-run in place short of where a fork's cut stood still lists what ran there.
        for round_ in sorted(head.elections.keys() | {c.round for c in course.candidates}):
            election = head.elections.get(round_)
            elects_on = None if election is None else election.elects_on
            members = [
                self._candidate(
                    course,
                    c,
                    elects_on,
                    cut=cut[c.address],
                    stands=stands,
                    latest=latest,
                    stood_on=any(s.head.holds(c) for s in siblings),
                )
                for c in course.candidates
                if c.round == round_
            ]
            rounds.append(
                HierarchyRound(
                    address=round_address(course.address, round_),
                    parent=course.address,
                    children=members,
                    facts=RoundFacts(
                        round=round_,
                        elects_on=elects_on,
                        reference=None
                        if election is None or election.reference is None
                        else head.address(election.reference),
                    ),
                )
            )
        spec, then = course.index.fork, head.below
        return SubtreeCourse(
            **_fields(course.entry)
            | {
                "children": [*rounds, *course.unplaced],
                "facts": SubtreeCourseFacts(
                    **_fields(course.entry.facts),
                    origin=head.crown(0) if 0 in head.owner else None,
                    from_candidate=None
                    if spec is None or then is None
                    else then.address(spec.from_candidate),
                    lens_criterion=None,
                    lens_shift=None,
                ),
            }
        )

    def _candidate(
        self,
        course: _Course,
        c: _Candidate,
        elects_on: DisplayMetric | None,
        *,
        cut: bool,
        stands: str | None,
        latest: str | None,
        stood_on: bool,
    ) -> HierarchyCandidate:
        retired = c.status != "live"
        reading = c.arm.reading
        election = reading.election
        if retired:
            election = election.model_copy(update={"selected": False, "crown": None})
        reading = reading.model_copy(
            update={"election": election, "panel": reading.panel.model_copy(update={"cut": cut})}
        )
        verdict = arm_verdict(reading, retired=retired)
        tags = [HierarchyTag(kind="verdict", value=verdict, label=ARM_VERDICT_LABELS[verdict])]
        if retired:
            by = c.status.by if isinstance(c.status, _Retired) else course.address
            tags.append(superseded_tag(by))
        caveat = None if reading.ability is None else reading.ability.caveat
        lineage = c.arm.state.individual.lineage
        facts = CandidateFacts(
            individual=c.arm.state.individual.id,
            parent_ids=list(lineage.parent_ids),
            variations=list(lineage.variations),
            reading=reading,
            stands=c.address == stands,
            course_latest=c.address == latest,
            answers_for_id=False,
            main_line=_main_line(c),
            lens_value=None,
            lens_rank_move=None,
            sample_set_accuracy=None,
            sample_set_n=None,
            divergence=None,
            divergent=False,
        )
        return HierarchyCandidate(
            address=c.address,
            parent=round_address(course.address, c.round),
            label=reading.arm.label,
            lede=_lede(c, stood_on=stood_on),
            caveat=None
            if caveat is None
            else f"{THETA_CAVEAT_INFO[caveat].head}. {THETA_CAVEAT_INFO[caveat].body}",
            tags=tags,
            stats=_candidate_stats(reading, elects_on),
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


def _answering(root: SubtreeCampaign) -> SubtreeCampaign:
    """The ONE candidate a remembered id resolves to: the first live measured one, else the first."""

    def rank(c: HierarchyCandidate) -> tuple[bool, bool]:
        return c.tag("superseded") is not None, c.facts.reading.end == "invalid"

    chosen: dict[str, HierarchyCandidate] = {}
    for entry in entries_under(root):
        if not isinstance(entry, HierarchyCandidate):
            continue
        held = chosen.get(entry.facts.individual)
        if held is None or rank(entry) < rank(held):
            chosen[entry.facts.individual] = entry
    answering = {c.address for c in chosen.values()}
    return _restamped(
        root,
        lambda e: (
            {"facts": e.facts.model_copy(update={"answers_for_id": e.address in answering})}
            if isinstance(e, HierarchyCandidate)
            else {}
        ),
    )


def _read_under(
    root: SubtreeCampaign,
    lens: Lens | None,
    samples: frozenset[int] | None,
    moment: Moment | None,
) -> SubtreeCampaign:
    """Every course says what re-read its stats, so a frozen copy never passes as the record."""
    samples = read_samples(lens, samples)
    # Every course is cut at the moment's wall clock; its offset is only the viewed leaf's line.
    instant = None if moment is None else moment.instant
    under = [
        *([f"the {lens.spelling} lens"] if lens is not None else []),
        *([f"a {len(samples)}-sample set"] if samples else []),
        *(
            []
            if instant is None
            else [
                f"the moment {datetime.fromtimestamp(instant, UTC).isoformat(timespec='seconds')}"
                if math.isfinite(instant)
                else "a moment before its ledger's first timestamp"
            ]
        ),
    ]
    if not under:
        return root
    said = f"Read under {' and '.join(under)}."
    return _restamped(
        root,
        lambda e: (
            {"caveat": " ".join(t for t in (e.caveat, said) if t)}
            if isinstance(e, SubtreeCourse)
            else {}
        ),
    )


def hierarchy_subtree(
    stores: Stores,
    address: str,
    lens: Lens | None,
    samples: frozenset[int] | None,
    *,
    moment: Moment | None,
) -> HierarchyEntry:
    """The entry at *address* with everything under it; *moment* replays the ledgers only.

    Built from the top-level campaign down, so an inner entry's ``parent`` is the candidate that
    spawned it on every read, never re-derived for a direct one.
    """
    campaign = owned_campaign(stores, _top_campaign(address))
    root = _answering(_Build(moment).campaign(stores, (), campaign, depth=MAX_COURSE_DEPTH))
    if lens is not None or samples:
        root = lensed(stores, root, lens, samples)
    root = _read_under(root, lens, samples, moment)
    found = next((entry for entry in entries_under(root) if entry.address == address), None)
    if found is None:
        raise NotFoundError(f"No hierarchy entry at {address!r}")
    return found


def _root_path(stores: Stores, address: str) -> CyclePath:
    inside, campaign_id = _campaign_of(address)
    return (*inside, owned_campaign(descend_store(stores, inside), campaign_id).root_hop)


def subtree_moment(
    stores: Stores, address: str, at: int | None, at_cycle: CyclePath
) -> Moment | None:
    """``at`` is a line of *at_cycle*'s own ledger; no cycle named, the campaign root's."""
    if at is None:
        return None
    path = at_cycle or _root_path(stores, address)
    leaf_store, leaf = resolve_cycle_path(stores, path)
    layout = leaf_store.campaigns.layout(leaf)
    if not layout.cycle_dir.is_dir():
        raise NotFoundError(f"Cycle '{leaf.campaign_id}/{leaf.cycle_id}' not found")
    return Moment(layout.ledger, at)


def subtree_version(stores: Stores, address: str) -> tuple[object, ...]:
    """Every store the TOP campaign's family spans: the body is built from all of them."""
    top = (owned_campaign(stores, _top_campaign(address)).root_hop,)
    return entries_version(c.store for c in iter_family_courses(stores, top))


def subtree_course(stores: Stores, path: CyclePath) -> SubtreeCourse:
    """One course as the subtree serves it, unlensed and at the head."""
    course = hierarchy_subtree(stores, encode_cycle_path(path), None, None, moment=None)
    assert isinstance(course, SubtreeCourse)
    return course
