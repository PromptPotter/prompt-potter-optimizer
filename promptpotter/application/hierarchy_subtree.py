"""The deep read: a campaign's courses, their rounds and candidates, and the inner campaigns under them."""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass, field

from promptpotter.application.hierarchy import campaign_entry, lift_stat
from promptpotter.application.mask.record import Lens
from promptpotter.application.mask.tree_lens import lensed
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
    HierarchyCampaign,
    HierarchyCandidate,
    HierarchyCourse,
    HierarchyEntry,
    HierarchyRound,
    HierarchyStat,
    HierarchyTag,
    MainLineStep,
    RoundFacts,
)
from promptpotter.domain.results import (
    ARM_VERDICT_LABELS,
    ArmReading,
    DisplayMetric,
    arm_verdict,
    panel_cuts,
)
from promptpotter.domain.ruler import THETA_CAVEAT_INFO
from promptpotter.domain.run_records import ForkDirection
from promptpotter.infrastructure.projections.cycle_index import read_cycle_index
from promptpotter.infrastructure.store.dataset_access import definition_version
from promptpotter.infrastructure.store.layout import CycleLayout, cycle_dir_for
from promptpotter.infrastructure.store.lineage_queries import (
    MAX_COURSE_DEPTH,
    CourseArm,
    CourseRead,
    iter_family_courses,
    read_course,
)
from promptpotter.infrastructure.store.read_model import Moment, file_sig
from promptpotter.infrastructure.store.stores import (
    Stores,
    descend_store,
    inner_sandbox_store,
    owned_campaign,
    resolve_cycle_path,
)
from promptpotter.shared.errors import NotFoundError


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


@dataclass
class _Candidate:
    address: str
    course: str
    arm: CourseArm
    retired_by: str | None = None
    inner: list[HierarchyCampaign] = field(default_factory=list)

    @property
    def round(self) -> int:
        return self.arm.round

    @property
    def individual(self) -> str:
        return self.arm.state.individual.id


@dataclass
class _Course:
    entry: HierarchyCourse
    hop: CycleHop
    index: CycleIndex
    read: CourseRead
    candidates: list[_Candidate]
    parent: _Course | None = None
    origin: str | None = None
    from_candidate: str | None = None
    prefix: list[MainLineStep] = field(default_factory=list)

    def newest(self, key: str, *, before: int | None = None) -> _Candidate | None:
        held = [
            c for c in self.candidates if c.arm.key == key and (before is None or c.round < before)
        ]
        return held[-1] if held else None

    def reference(self, key: str, round_: int) -> str | None:
        """The candidate an individual a round re-read IS: here, else up the spawn chain."""
        held = self.newest(key, before=round_)
        if held is not None:
            return held.address
        return None if self.parent is None else self.parent.reference(key, round_)


def _retired(fork: _Course, parent: _Course) -> dict[str, str]:
    """A cut retires only as far as the branch actually GOT: a branch that died retires nothing."""
    spec = fork.index.fork
    if spec is None or spec.resolved_direction is not ForkDirection.SUPERSEDE:
        return {}
    reach = max((c.round for c in fork.candidates), default=None)
    if reach is None:
        return {}
    arms = parent.candidates
    cut = next((i + 1 for i, c in enumerate(arms) if c.arm.key == spec.from_candidate_id), None)
    if cut is None:
        if spec.from_round is None:
            return {}
        cut = next((i for i, c in enumerate(arms) if c.round >= spec.from_round), len(arms))
    return {c.address: fork.entry.address for c in arms[cut:] if c.round <= reach}


def _main_line(course: _Course, head: _Candidate) -> list[MainLineStep]:
    crowned: dict[int, list[str]] = {}
    elected: set[int] = set()
    for c in course.candidates:
        election = c.arm.reading.election
        if not election.held:
            continue
        elected.add(c.round)
        if election.selected and c.retired_by is None:
            crowned.setdefault(c.round, []).append(c.address)
    steps = list(course.prefix)
    for round_ in sorted(elected | {head.round}):
        picks = crowned.get(round_)
        if round_ == head.round:
            steps.append(MainLineStep(round=round_, candidates=[head.address]))
        elif round_ < head.round and picks:
            steps.append(MainLineStep(round=round_, candidates=picks))
        elif round_ > head.round and picks:
            break
        else:
            steps.append(MainLineStep(round=round_, candidates=[]))
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
    return HierarchyStat(
        key="ability", label="Ability θ", value=value, sub=sub, amount=ability.theta
    )


def _accuracy(reading: ArmReading) -> HierarchyStat:
    own = reading.own
    accuracy = None if own is None or own.accuracy is None else own.accuracy.value
    scored, expected = reading.panel.scored, reading.panel.expected
    return HierarchyStat(
        key="accuracy",
        label="Accuracy",
        value="—" if accuracy is None else f"{accuracy:.0%}",
        sub=None
        if scored is None
        else f"{scored}{'' if expected is None else f' of {expected}'} scored",
        amount=accuracy,
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


def _lede(c: _Candidate) -> str:
    if c.retired_by is not None:
        return (
            "Retired: the run branched and continued in the course that took its place. It stays "
            "as the record of what ran; nothing after it is on the line."
        )
    if c.round == 0:
        return "This course's ORIGIN: the specification it started from, measured."
    return "A candidate this course proposed and measured."


class _Build:
    """Per-request and thrown away: one subtree read, every course read once."""

    def __init__(self, moment: Moment | None) -> None:
        self.moment = moment

    def campaign(
        self, stores: Stores, inside: CyclePath, campaign: Campaign, *, depth: int
    ) -> HierarchyCampaign:
        entry = campaign_entry(stores, inside, campaign, depth=1, moment=self.moment)
        courses: list[_Course] = []
        for listed in entry.children:
            assert isinstance(listed, HierarchyCourse)
            hop = decode_cycle_path(listed.address)[-1]
            index = read_cycle_index(stores.campaigns.cycle_dir(hop), moment=self.moment)
            assert index is not None
            read = read_course(stores, hop, index, moment=self.moment)
            courses.append(
                _Course(
                    listed,
                    hop,
                    index,
                    read,
                    [
                        _Candidate(
                            candidate_address(listed.address, a.round, a.key), listed.address, a
                        )
                        for a in read.arms
                    ],
                )
            )
        self._place(courses)
        for course in courses:
            sandbox = inner_sandbox_store(stores, course.hop.campaign_id, course.hop.cycle_id)
            if sandbox is not None and depth > 0:
                self._inner(sandbox, (*inside, course.hop), course, courses, depth=depth - 1)
        built = [self._course(course) for course in courses]
        return entry.model_copy(
            update={
                "children": built,
                "facts": entry.facts.model_copy(
                    update={
                        "deletable_stubs": len(stores.campaigns.deletable_stubs(campaign.root_hop))
                    }
                ),
            }
        )

    def _place(self, courses: list[_Course]) -> None:
        """Root first, forks by mint time: a parent is placed before every course cut from it."""
        by_address = {c.entry.address: c for c in courses}
        for course in courses:
            spawn = course.entry.facts.spawn
            parent = None if spawn is None else by_address.get(spawn.parent)
            own_origin = next(
                (c.address for c in course.candidates if c.round == 0 and c.retired_by is None),
                None,
            )
            if parent is None:
                course.origin = own_origin
                continue
            course.parent = parent
            for address, successor in _retired(course, parent).items():
                next(c for c in parent.candidates if c.address == address).retired_by = successor
            first = min((c.round for c in course.candidates), default=None)
            spec = course.index.fork
            named = None if spec is None or not spec.from_candidate_id else spec.from_candidate_id
            reference = next(
                (r.reference for r in course.read.rounds if r.reference is not None), None
            )
            anchor = next(
                (
                    held
                    for key in (named, reference)
                    if key is not None and (held := parent.newest(key)) is not None
                ),
                None,
            )
            course.from_candidate = anchor.address if anchor is not None else parent.origin
            course.origin = own_origin or parent.origin
            if anchor is not None:
                course.prefix = [
                    step
                    for step in _main_line(parent, anchor)
                    if first is None or step.round < first
                ]

    def _inner(
        self,
        sandbox: Stores,
        inside: CyclePath,
        course: _Course,
        siblings: list[_Course],
        *,
        depth: int,
    ) -> None:
        for campaign in sandbox.campaigns.list_campaigns(None, lifecycle="all"):
            root = read_cycle_index(
                sandbox.campaigns.cycle_dir(campaign.root_hop), moment=self.moment
            )
            if root is None:
                continue
            spawned = root.spawned_by
            host = (
                None
                if spawned is None
                else self._host(spawned.round, spawned.individual_id, course, siblings)
            )
            if host is None:
                host = next((c for c in course.candidates if c.address == course.origin), None)
            if host is None:
                continue
            nested = self.campaign(sandbox, inside, campaign, depth=depth)
            host.inner.append(nested.model_copy(update={"parent": host.address}))

    @staticmethod
    def _host(
        round_: int | None, individual: str | None, course: _Course, siblings: list[_Course]
    ) -> _Candidate | None:
        """The course whose sandbox holds a run first, then its campaign's other courses."""
        pool = [*course.candidates, *(c for s in siblings if s is not course for c in s.candidates)]
        if individual:
            exact = next(
                (c for c in pool if c.individual == individual and c.round == round_), None
            )
            return exact or next((c for c in pool if c.individual == individual), None)
        return None

    def _course(self, course: _Course) -> HierarchyCourse:
        live = [c for c in course.candidates if c.retired_by is None]
        crowned = [c for c in live if c.arm.reading.election.selected]
        stands = crowned[-1].address if crowned else None
        latest = course.candidates[-1].address if course.candidates else None
        cohorts: dict[tuple[int, bool], list[_Candidate]] = {}
        for c in course.candidates:
            cohorts.setdefault((c.round, c.retired_by is None), []).append(c)
        cut: dict[str, bool] = {}
        for cohort in cohorts.values():
            flags = panel_cuts(
                [(c.arm.reading.panel.scored, c.arm.reading.panel.expected) for c in cohort]
            )
            cut |= {c.address: flag for c, flag in zip(cohort, flags, strict=True)}
        rounds: list[HierarchyRound] = []
        for rnd in course.read.rounds:
            elects_on = None if rnd.election is None else rnd.election.elects_on
            members = [
                self._candidate(
                    course, c, elects_on, cut=cut[c.address], stands=stands, latest=latest
                )
                for c in course.candidates
                if c.round == rnd.round
            ]
            rounds.append(
                HierarchyRound(
                    address=round_address(course.entry.address, rnd.round),
                    parent=course.entry.address,
                    label=f"Round {rnd.round}",
                    lede=None if rnd.election is None else rnd.election.verdict_reason,
                    caveat=None,
                    tags=[],
                    stats=[],
                    children=list(members),
                    facts=RoundFacts(
                        round=rnd.round,
                        elects_on=elects_on,
                        reference=None
                        if rnd.reference is None or rnd.round == 0
                        else course.reference(rnd.reference, rnd.round),
                    ),
                )
            )
        spawn = course.entry.facts.spawn
        return course.entry.model_copy(
            update={
                "children": rounds,
                "facts": course.entry.facts.model_copy(
                    update={
                        "origin": course.origin,
                        "spawn": None
                        if spawn is None
                        else spawn.model_copy(update={"from_candidate": course.from_candidate}),
                    }
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
    ) -> HierarchyCandidate:
        retired = c.retired_by is not None
        reading = c.arm.reading
        election = reading.election
        if retired:
            election = election.model_copy(update={"selected": False, "crown": None})
        reading = reading.model_copy(
            update={"election": election, "panel": reading.panel.model_copy(update={"cut": cut})}
        )
        verdict = arm_verdict(reading, retired=retired)
        tags = [HierarchyTag(kind="verdict", value=verdict, label=ARM_VERDICT_LABELS[verdict])]
        if c.retired_by is not None:
            tags.append(
                HierarchyTag(
                    kind="superseded",
                    value=c.retired_by,
                    label=f"retired → {c.retired_by.rpartition(UNIT_SEP)[2].rpartition('_')[2][:6]}",
                )
            )
        caveat = None if reading.ability is None else reading.ability.caveat
        lineage = c.arm.state.individual.lineage
        facts = CandidateFacts(
            individual=c.individual,
            parent_ids=list(lineage.parent_ids),
            variations=list(lineage.variations),
            reading=reading,
            stands=c.address == stands,
            course_latest=c.address == latest,
            answers_for_id=False,
            main_line=_main_line(course, c),
            lens_value=None,
            lens_rank_move=None,
            sample_set_accuracy=None,
            sample_set_n=None,
            divergence=None,
            divergent=False,
        )
        return HierarchyCandidate(
            address=c.address,
            parent=round_address(course.entry.address, c.round),
            label=reading.arm.label,
            lede=_lede(c),
            caveat=None
            if caveat is None
            else f"{THETA_CAVEAT_INFO[caveat].head}. {THETA_CAVEAT_INFO[caveat].body}",
            tags=tags,
            stats=_candidate_stats(reading, elects_on),
            children=list(c.inner),
            facts=facts,
        )


def entries_under(entry: HierarchyEntry) -> Iterator[HierarchyEntry]:
    yield entry
    for child in entry.children:
        yield from entries_under(child)


def _answering(root: HierarchyCampaign) -> HierarchyCampaign:
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

    def stamp(entry: HierarchyEntry) -> HierarchyEntry:
        children = [stamp(child) for child in entry.children]
        if isinstance(entry, HierarchyCandidate):
            return entry.model_copy(
                update={
                    "children": children,
                    "facts": entry.facts.model_copy(
                        update={"answers_for_id": entry.address in answering}
                    ),
                }
            )
        return entry.model_copy(update={"children": children})

    stamped = stamp(root)
    assert isinstance(stamped, HierarchyCampaign)
    return stamped


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
    cycle_dir = cycle_dir_for(leaf_store.base_dir, leaf)
    if not cycle_dir.is_dir():
        raise NotFoundError(f"Cycle '{leaf.campaign_id}/{leaf.cycle_id}' not found")
    return Moment(CycleLayout(cycle_dir).ledger, at)


def subtree_version(stores: Stores, address: str, *, masked: bool) -> tuple[object, ...]:
    """Stats only: every store the TOP campaign's family spans — what the body is built from —
    and, under a mask, what it grades from."""
    top = (owned_campaign(stores, _top_campaign(address)).root_hop,)
    family = {c.store.base_dir: c.store for c in iter_family_courses(stores, top)}.values()
    if not masked:
        return tuple(held.campaigns.version() for held in family)
    names = {
        c.dataset_name for held in family for c in held.campaigns.list_campaigns(lifecycle="all")
    }
    archives = {held.archive.base_dir: held.archive for held in family}.values()
    return (
        *(held.campaigns.version() for held in family),
        *(archive.version() for archive in archives),
        *(file_sig(held.ruler.path) for held in family),
        *(definition_version(stores, name) for name in sorted(names)),
    )


def subtree_course(stores: Stores, path: CyclePath) -> HierarchyCourse:
    """One course as the subtree serves it, unlensed and at the head."""
    course = hierarchy_subtree(stores, encode_cycle_path(path), None, None, moment=None)
    assert isinstance(course, HierarchyCourse)
    return course
