"""The lineage tree — the served genealogy, read at any depth.

Nodes alternate ``Course -> Candidate -> Course`` forever — at L4 a candidate's children
ARE courses, which is why L5+ needs no new tier. A course's children are its TIMELINE:
the candidates it minted plus every attempt its forks contributed, **renumbered here**,
because ``C{round}.{n}`` is a position in one course's private sequence and a fork's own
counter names nothing campaign-wide. **A fork is not a node** — it survives as a
provenance stamp on the candidates it contributed. Identity comes from the ledger,
election/θ/CI from ``dashboard.json::rounds[]`` (:func:`_close_facts` says why); the two
owners cannot be collapsed. **This is a READ MODEL and decides nothing** — the decision
genealogy (``mask/backprop.py``, ``mask/divergence.py``, the resume replayers) rides
positional ``(cycle_id, round)`` and must not be moved onto it.
"""

from __future__ import annotations

from pathlib import Path
from typing import Literal, NamedTuple

from pydantic import ConfigDict, Field

from promptpotter.domain.campaign import Campaign
from promptpotter.domain.cycle_paths import CycleHop, CyclePath
from promptpotter.domain.phases import RunPhase
from promptpotter.domain.run_records import LedgerCandidate
from promptpotter.domain.strict_model import StrictModel
from promptpotter.infrastructure.runtime_flags import derive_run_phase
from promptpotter.infrastructure.store.campaign_store.ledger_scan import (
    scan_ledger_candidates,
    scan_ledger_round_closes,
)
from promptpotter.infrastructure.store.campaign_store.store import origin_accuracy_of
from promptpotter.infrastructure.store.io import read_json_optional
from promptpotter.infrastructure.store.layout import CycleLayout, cycle_dir_for, sibling_kind
from promptpotter.infrastructure.store.stores import Stores, inner_sandbox_store, resolve_cycle_path

__all__ = [
    "FamilyCourse",
    "LineageDivergence",
    "LineageNode",
    "build_lineage_tree",
    "iter_family_courses",
]


NodeKind = Literal["course", "candidate"]
CourseKind = Literal["root", "fork", "diag", "sweep", "inner"]

# How many COURSE levels a family walk expands. A COST BOUND, not a caller's dial — there is
# one served tree per campaign and every consumer reads the same one, so a per-caller depth is
# just two clients disagreeing about the same object (which is exactly what it was: the
# sidebar asked for 1 and the overlay for 3, measured byte-identical only because today's L4
# inner runs spawn no inner runs of their own).
#
# It stays a bound rather than being removed: `_child_courses` walks `.inner/` sandboxes,
# which nest re-entrantly (`stores.py::inner_sandbox_store`), so an unbounded walk is
# unbounded on disk too. 3 covers L4 (depth 1) with room for L5+ before the bound is the
# thing that has to change.
_MAX_COURSE_DEPTH = 3


class LineageDivergence(StrictModel):
    """Where an alternative criterion would have elected someone else. Rides the node it
    describes (``LineageNode.divergence``) — there is no parallel list to re-join."""

    model_config = ConfigDict(frozen=True)

    alternative_candidate_id: str | None = Field(
        default=None,
        description="The candidate the masked criterion would have elected instead "
        "(measured, so nameable); null when the round would simply have held on origin.",
    )


class LineageNode(StrictModel):
    """One node of the served tree. The same shape at every depth — that is the point."""

    model_config = ConfigDict(frozen=True)

    kind: NodeKind = Field(description="course | candidate — they strictly alternate")
    id: str = Field(
        description="Course: the cycle_id. Candidate: the searchpoint id minted at L1/origin."
    )
    parent_id: str | None = Field(
        default=None,
        description="The candidate this node descends from; null only at the true root. A "
        "course carries the same edge its own C0 carries.",
    )
    label: str = Field(
        description="`C{round}.{n}` on the campaign's ONE timeline: this course's own "
        "candidates keep their minted label; an attempt a fork contributed takes the next "
        "free index of its round, by mint time.",
    )
    course_label: str = Field(
        description="This candidate's label in the course that MINTED it. Equal to `label` "
        "for a candidate this course minted itself; a fork-contributed attempt keeps the "
        "fork's private `C{round}.{n}` here while `label` carries its renumbered position "
        "on this course's timeline. JOIN ON THIS, never on `candidate_id`, when matching a "
        "node against a per-cycle projection: `dashboard.json` is per-cycle and speaks the "
        "minting course's private counter, while `candidate_id` is re-minted per run (see "
        "`_close_facts`), so an id join silently misses.",
    )
    path: list[CycleHop] = Field(
        default_factory=list,
        description="THE address, root → leaf: the course this node belongs to. A candidate "
        "a fork contributed carries the FORK's path, so selecting it re-roots onto that fork.",
    )
    children: list[LineageNode] = Field(default_factory=list)

    # -- candidate scalars ----------------------------------------------------
    round: int | None = Field(default=None, description="Column hint. Candidates only.")
    accuracy: float | None = None
    composite_fitness: float | None = None
    state: str = Field(
        default="",
        description="Candidate: minted | measured — never 'winner' (that rides `is_winner`). "
        "Course: the cycle status.",
    )
    is_winner: bool = Field(
        default=False,
        description="Elected this round. False throughout a round that never CLOSED — "
        "election is stamped at close, so such a round has no winner to report.",
    )
    theta: float | None = Field(
        default=None,
        description="Difficulty-adjusted Rasch ability the election ranked on — what explains "
        "a lower-accuracy winner. Null outside the round's election fit.",
    )
    theta_se: float | None = None
    evaluators: dict[str, float] = Field(
        default_factory=dict,
        description="The candidate's stored evaluator namespace — the measurement a `score:` "
        "lens re-scores against.",
    )
    composite_ci_lo: float | None = None
    composite_ci_hi: float | None = None
    scored_samples: int | None = None
    expected_samples: int | None = None
    cumulative_theta: float | None = Field(
        default=None,
        description="Ability of the adopted lineage on the cycle's fixed δ ruler — the "
        "subset-invariant, cross-round-comparable series the trend plots. Carried by the "
        "round's WINNER only: it is a property of the advancing spine, and a losing sibling "
        "never joined it. Its accuracy-space predecessor was a mean over rows measured by "
        "different configurations and is gone; `accuracy` is what this node MEASURED.",
    )
    lens_value: float | None = Field(
        default=None,
        description="This candidate's fitness under the request's `score:` lens, re-scored "
        "server-side from its stored evaluator namespace. Null without a lens, or when the "
        "namespace can't satisfy the formula.",
    )
    sample_set_accuracy: float | None = Field(
        default=None,
        description="Scorer-faithful accuracy over the request's `samples=` subset. Null "
        "without a `samples=` mask, or when this candidate never ran any selected sample.",
    )
    sample_set_n: int | None = Field(
        default=None, description="How many of the `samples=` subset this candidate ran."
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

    # -- course scalars -------------------------------------------------------
    # Also set on a FORK-candidate: a fork is served as a candidate (it IS the attempt), and
    # these carry the provenance a surface marks it by — ⑂, who steered it, what stopped it.
    course_kind: CourseKind | None = Field(
        default=None,
        description="Courses, and the candidates a fork contributed here — on those it is "
        "the ⑂ stamp marking an attempt the operator cut.",
    )
    run_phase: RunPhase | None = Field(
        default=None,
        description="Courses only — the ONE server-owned run-state (`derive_run_phase`), the "
        "same value `/cycles` serves. Null on a candidate, which has no run of its own.",
    )
    dataset_name: str = ""
    trigger: str = Field(default="", description="Fork trigger; empty for roots and inner runs.")
    steered_by: str | None = Field(default=None, description="Operator who cut this fork.")
    task: str | None = Field(
        default=None,
        description="An inner run's benchmark task. Load-bearing: every task runs for every "
        "candidate, so the candidate edge alone does not identify an inner run.",
    )
    best_accuracy: float | None = None
    origin_accuracy: float | None = Field(
        default=None,
        description="This course's round-0 score. A course that has only run its origin has "
        "this and no `best_accuracy`, so reading only `best` blanks its bar.",
    )
    hearts: int | None = None
    lives_cap: int | None = None


class FamilyCourse(NamedTuple):
    """A course in the family and how to reach it — the recursion's unit of work.

    Public because the tree is not the only reader of the family: the time-ray
    (``store/family_ray_views.py``) merges the same set of cycles into one chronology, via
    :func:`iter_family_courses` — a second family walk would be a second answer to "who
    hangs off whom", and the fork-vs-inner distinction is exactly the part that would drift.
    """

    store: Stores
    path: CyclePath
    # NOT `index` — a NamedTuple field by that name shadows `tuple.index`.
    manifest: dict[str, object]
    # An L4 inner run (a sandbox root) vs a fork/sweep/diag of THIS course. Both hang off a
    # candidate by the same edge; only a fork contributes attempts to this course's timeline.
    inner: bool
    # Hop count off the family root — stamped by `iter_family_courses` (a fork replaces the
    # leaf hop, so it costs no depth; an inner run extends the path and costs one). Left 0
    # by `_build`'s recursion, which tracks depth itself.
    depth: int = 0

    @property
    def created_at(self) -> str:
        """When the course was minted — the campaign timeline's ordering key."""
        value = self.manifest.get("created_at")
        return value if isinstance(value, str) else ""


class _Reads:
    """Every store this tree touches, read ONCE. ``enumerate_cycles`` answers a WHOLE store,
    so asking it per course is the tree's O(n²).

    Per-build and thrown away after: the file tree IS the dashboard, so a memo outliving the
    request would serve a round that has since closed.
    """

    def __init__(self) -> None:
        self._cycles: dict[Path, list[dict[str, object]]] = {}
        self._campaigns: dict[tuple[Path, str], Campaign | None] = {}
        # Cycle DIRS already visited this build — `(base_dir, campaign_id, cycle_id)`, the
        # full identity, because a cycle_id alone is not one: it is a content hash of the
        # origin, so every campaign on a declaration shares it, and inside one L4 sandbox
        # every candidate that ran the same benchmark cell mints it again. Keyed on the id
        # alone this dropped the SECOND inner run of a cell and its candidate then reported
        # a panel it never measured. The guard it exists for survives untouched: a corrupt
        # `parent_cycle_id` pointing back up the chain revisits the same dir, so both walkers
        # (`_build`'s recursion and `iter_family_courses`) still terminate.
        self.seen: set[tuple[Path, str, str]] = set()

    def cycles(self, store: Stores) -> list[dict[str, object]]:
        if (key := store.base_dir) not in self._cycles:
            self._cycles[key] = store.campaigns.enumerate_cycles()
        return self._cycles[key]

    def campaign(self, store: Stores, campaign_id: str) -> Campaign | None:
        if (key := (store.base_dir, campaign_id)) not in self._campaigns:
            self._campaigns[key] = store.campaigns.load_campaign(campaign_id)
        return self._campaigns[key]


def _layout(store: Stores, hop: CycleHop) -> CycleLayout:
    """This hop's cycle paths. Three readers here wanted three different files out of
    the same dir and each spelled its own name; ``CycleLayout`` owns all three."""
    return CycleLayout(cycle_dir_for(store.base_dir, hop.campaign_id, hop.cycle_id))


def _read_index(store: Stores, hop: CycleHop) -> dict[str, object]:
    index = read_json_optional(_layout(store, hop).manifest)
    return index if isinstance(index, dict) else {}


def _block(index: dict[str, object], key: str) -> dict[str, object]:
    block = index.get(key)
    return block if isinstance(block, dict) else {}


def _str_or_none(value: object) -> str | None:
    return value if isinstance(value, str) and value else None


def _course_edge(index: dict[str, object]) -> tuple[str | None, str | None]:
    """``(candidate_id, candidate_label)`` this course hangs off — the ONE resolver, collapse #1.

    A fork records it as ``fork.from_candidate_id``; an L4 inner run as ``spawned_by``, which
    carries BOTH an id and a label. The label is the fallback and not a lesser one now that
    labels are minted facts rather than read-time positions: every inner run on disk today
    stamped ``candidate_label`` and left ``candidate_id`` null, so the label is the only edge
    those runs have. Both null = a campaign root, or a fork from the rebase family whose
    minting path stamps neither; the caller attaches those to the course's origin.
    """
    fork = _block(index, "fork")
    if cid := _str_or_none(fork.get("from_candidate_id")):
        return cid, None
    spawned = _block(index, "spawned_by")
    if spawned:
        return _str_or_none(spawned.get("candidate_id")), _str_or_none(
            spawned.get("candidate_label")
        )
    return None, None


class _CloseFacts(NamedTuple):
    """What only a round CLOSE knows about a candidate. A round that never closed has no
    entry at all, so its candidates never inherit a crown nobody awarded.

    Nothing a candidate can know alone belongs here — its accuracy, its evaluators, its own
    composite CI all ride its `candidate_scored` snapshot. What is left is the joint fit:
    the election, the ability it ranked on, the θ-implied band that OVERRIDES the candidate's
    own whisker where the ruler was warm, and the frontier the round advanced.
    """

    is_winner: bool
    theta: float | None
    theta_se: float | None
    cumulative_theta: float | None
    composite_ci_lo: float | None
    composite_ci_hi: float | None


def _close_facts(ledger_path: Path, candidates: list[LedgerCandidate]) -> dict[str, _CloseFacts]:
    """``candidate_id -> _CloseFacts``, folded from the cycle's OWN ledger.

    A round's close is a ledger fact now, so this reads the same file
    ``scan_ledger_candidates`` reads, one scan later. It used to open ``dashboard.json``,
    because ``l1_score`` appended the ``ROUND_WINNER`` record to a local list instead of the
    cycle's decision sink and ``on_round_complete`` persisted three scalars — a served view
    was reduced to reading another projection's output for facts the ingress should carry.

    **The join stays on ``label``, the positional identity.** Moving both sides onto one file
    does not make ``candidate_id`` safe: it is a fresh uuid per construction, and a resume
    re-scores the origin, so the ledger holds a NEW mint for `(0, 0)` beside a close written
    under the OLD id. Position is stable across exactly that churn.

    A round with no close record has no entry, so its candidates get no crown and no θ.
    """
    closes = scan_ledger_round_closes(ledger_path)
    out: dict[str, _CloseFacts] = {}
    for cand in candidates:
        close = closes.get(cand.round)
        if close is None:
            continue
        # Walking THIS round's candidates is what keeps a HELD round honest: its adopted
        # individual is the retained incumbent, which is not among them, so nobody is crowned
        # and the frontier stays with the round that earned it.
        won = cand.label == close.winner_label
        ability = close.abilities.get(cand.label, {})
        out[cand.candidate_id] = _CloseFacts(
            is_winner=won,
            theta=ability.get("theta"),
            theta_se=ability.get("theta_se"),
            # The frontier belongs to the spine: only the adopted candidate advanced it.
            cumulative_theta=close.cumulative_theta if won else None,
            # Present only where the warm ruler implied a band; otherwise the candidate's own
            # whisker stands, and the caller resolves that precedence.
            composite_ci_lo=ability.get("composite_ci_lo"),
            composite_ci_hi=ability.get("composite_ci_hi"),
        )
    return out


def _float_or_none(value: object) -> float | None:
    return float(value) if isinstance(value, int | float) and not isinstance(value, bool) else None


def _course_scalars(
    store: Stores, hop: CycleHop, index: dict[str, object], reads: _Reads
) -> dict[str, object]:
    """The course's own facts: topology from ``index.json``, live ♥ from the dashboard."""
    layout = _layout(store, hop)
    dash = read_json_optional(layout.dashboard)
    dash = dash if isinstance(dash, dict) else {}

    fork, spawned = _block(index, "fork"), _block(index, "spawned_by")
    limits = dash.get("run_limits") if isinstance(dash.get("run_limits"), dict) else {}
    best, hearts = index.get("best_accuracy"), dash.get("hearts")
    cap = limits.get("lives_cap") if isinstance(limits, dict) else None
    campaign = reads.campaign(store, hop.campaign_id)

    # A course is INNER because of where it lives, not because it remembered to say so:
    # the rebase pair on disk carries no `spawned_by` yet sits in the sandbox, and calling
    # those "root" would put two roots in one tree. A fork of an inner run stays a fork —
    # only the sandbox's own roots are the recursion's entry point.
    kind: CourseKind = sibling_kind(hop.cycle_id)
    # A sandbox store is rooted at its own `.inner/<key>`, so its own path is the answer.
    if kind == "root" and (spawned or ".inner" in store.projects_root.parts):
        kind = "inner"

    return {
        "course_kind": kind,
        "state": str(index.get("status") or ""),
        # The ONE run-phase derivation, the same call `/cycles` makes — never a second
        # "is it running?" computed from this module's own inputs.
        "run_phase": str(
            derive_run_phase(layout.cycle_dir, is_terminal=bool(index.get("finished_at")))
        ),
        "trigger": str(fork.get("trigger") or ""),
        "steered_by": _str_or_none(fork.get("issued_by")),
        "task": _str_or_none(spawned.get("task")),
        "dataset_name": campaign.dataset_name if campaign else "",
        "best_accuracy": float(best) if isinstance(best, int | float) else None,
        # The SAME derivation `/cycles` serves it by — round 0 IS the origin, and there is
        # no stored copy to drift from.
        "origin_accuracy": origin_accuracy_of(index),
        "hearts": hearts if isinstance(hearts, int) else None,
        "lives_cap": cap if isinstance(cap, int) else None,
    }


def _child_courses(store: Stores, path: CyclePath, reads: _Reads) -> list[FamilyCourse]:
    """Every course hanging off the course at *path* — forks AND inner runs, one list.

    Collapse #1 in code: a fork is a sibling cycle in the SAME store whose
    ``parent_cycle_id`` is ours (so its path replaces our leaf); an inner run is a campaign
    ROOT inside our ``.inner/<key>`` sandbox (so its path extends ours by one hop). Two
    directories, one kind of thing — callers never branch on which.

    Only sandbox ROOTS are taken: an inner run's own forks are that course's children, and
    lifting them here would attach a grandchild to us and draw it twice.

    Both matches are on the full ``(campaign_id, cycle_id)`` identity, never the cycle_id
    alone — see ``_Reads.seen``. A fork lives in its parent's OWN campaign dir, so a bare
    ``parent_cycle_id`` match reaches into every other campaign minted on the same
    declaration (they share the root cycle id) and hands us a branch we never cut.
    """
    leaf = path[-1]
    reads.seen.add((store.base_dir, leaf.campaign_id, leaf.cycle_id))
    out: list[FamilyCourse] = []
    for entry in reads.cycles(store):
        if entry.get("parent_cycle_id") != leaf.cycle_id:
            continue
        if entry.get("campaign_id") != leaf.campaign_id:
            continue
        hop = CycleHop(campaign_id=str(entry["campaign_id"]), cycle_id=str(entry["cycle_id"]))
        if (key := (store.base_dir, hop.campaign_id, hop.cycle_id)) in reads.seen:
            continue
        reads.seen.add(key)
        out.append(
            FamilyCourse(
                store=store,
                path=(*path[:-1], hop),
                manifest=_read_index(store, hop),
                inner=False,
            )
        )

    sandbox = inner_sandbox_store(store, leaf.campaign_id, leaf.cycle_id)
    if sandbox is not None:
        for entry in reads.cycles(sandbox):
            if entry.get("parent_cycle_id"):
                continue
            hop = CycleHop(campaign_id=str(entry["campaign_id"]), cycle_id=str(entry["cycle_id"]))
            if (key := (sandbox.base_dir, hop.campaign_id, hop.cycle_id)) in reads.seen:
                continue
            reads.seen.add(key)
            out.append(
                FamilyCourse(
                    store=sandbox,
                    path=(*path, hop),
                    manifest=_read_index(sandbox, hop),
                    inner=True,
                )
            )
    return out


def iter_family_courses(store: Stores, path: CyclePath) -> list[FamilyCourse]:
    """The whole family rooted at *path*, root first, then breadth-first below it.

    The FLAT view of what :func:`_build` walks recursively — same :func:`_child_courses`,
    same ``reads.seen`` guard, so "who belongs to this family" has exactly one answer. The
    time-ray needs the set without the tree's alternating shape (it merges ledgers, it does
    not nest them).

    Order is stable — root, then each level sorted by ``(created_at, campaign_id, cycle_id)``
    — so the ray's ETag, which folds the courses in walk order, holds across identical
    requests. The campaign is part of the key because the cycle_id is not unique among
    siblings: two inner runs of the same benchmark cell carry the same one.
    :data:`_MAX_COURSE_DEPTH` bounds ``.inner/`` NESTING exactly as ``_build`` does: a fork
    replaces its parent's leaf hop rather than extending the path, so it costs no depth.
    """
    reads = _Reads()
    root_store, _ = resolve_cycle_path(store, path)
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
        level.sort(key=lambda c: (c.created_at, c.path[-1].campaign_id, c.path[-1].cycle_id))
        out.extend(level)
        frontier = level
    return out


def _parent_candidate_of(course: FamilyCourse, candidates: list[LedgerCandidate]) -> str:
    """The candidate this course descends from.

    Resolution order — id, then label, then the origin. The label join is scoped to THIS
    course's candidates, so it is a join on a minted key within one namespace, not a guess.
    A course that resolves to nothing still descends from something: C0 is the one candidate
    every course is guaranteed to have, and attaching there beats floating loose — which is
    exactly the bug this tree exists to end.
    """
    by_label = {c.label: c.candidate_id for c in candidates}
    known = {c.candidate_id for c in candidates}
    origin = candidates[0].candidate_id if candidates else ""
    cid, label = _course_edge(course.manifest)
    return cid if cid in known else by_label.get(label or "", origin)


def _bucket_by_parent(
    courses: list[FamilyCourse], candidates: list[LedgerCandidate]
) -> dict[str, list[FamilyCourse]]:
    """``candidate_id -> the inner runs filed under it``."""
    out: dict[str, list[FamilyCourse]] = {}
    for course in courses:
        if target := _parent_candidate_of(course, candidates):
            out.setdefault(target, []).append(course)
    return out


def _is_replay(node: LineageNode) -> bool:
    """A ``C0`` that descends from a candidate IS that candidate, re-run.

    **Structural, not a convention:** a campaign root's origin has no parent; a fork's always
    names the candidate it was cut from, because a fork borrows an origin rather than deriving
    one. So it is not an attempt — it merges into what it replays, and the runs that measured
    it move there (:func:`_contributions`).
    """
    return node.label == "C0" and node.parent_id is not None


def _empty_attempt(course: LineageNode, *, cut_from: str, round_: int) -> LineageNode:
    """A fork that put no candidate on the timeline still holds its row — dropping it would
    hand its index to the next real attempt.

    ``accuracy`` is None on purpose: ``index.json::best_accuracy`` on such a fork is seeded
    from the parent, so painting it would report a number nothing here measured. Its
    ``course_label`` stays the fork's cycle_id (it minted no candidate, so no private
    position exists) — join-safe, because a cycle_id never matches a label in a per-cycle
    projection.
    """
    return course.model_copy(
        update={
            "kind": "candidate",
            "parent_id": cut_from,
            "round": round_,
            "accuracy": None,
            "children": [],
        }
    )


class _Contribution(NamedTuple):
    """What a fork puts on its parent's timeline, and what it hands back to the candidate it
    was cut from."""

    # The fork's own candidates, minus the replayed origin — each an attempt on the parent's
    # sequence. Never empty: a fork that produced none contributes `_empty_attempt`.
    attempts: list[LineageNode]
    # The courses that measured the fork's replayed C0. They measured the candidate it
    # replays, so they belong there.
    replayed_runs: list[LineageNode]


def _contributions(
    fork: FamilyCourse, *, cut_from: str, cut_round: int, depth: int, reads: _Reads
) -> _Contribution:
    """A fork, resolved onto the timeline of the course it was cut in.

    Labels are assigned by :func:`_build`, the only place that can: the index depends on every
    sibling. ``depth`` passes straight through rather than decrementing — a fork is not a
    course node, so its candidates are *this* course's timeline and their runs sit at this
    course's depth. A fork of a fork resolves transitively.
    """
    course = _build(fork.store, fork.path, depth=depth, reads=reads)
    replays = [c for c in course.children if _is_replay(c)]
    replay_ids = {c.id for c in replays}

    attempts: list[LineageNode] = []
    for cand in course.children:
        if _is_replay(cand):
            continue
        attempts.append(
            cand.model_copy(
                update={
                    # An attempt off the replayed origin descends from what that origin
                    # replays — the replay is gone, and a parent_id pointing at a node no
                    # longer in the tree is a dangling edge. An attempt off an EARLIER
                    # attempt of the same fork keeps its edge: that candidate is on the
                    # timeline too, under its new label but the same id.
                    "parent_id": cut_from if cand.parent_id in replay_ids else cand.parent_id,
                    # The ⑂ stamp: what marks an attempt the operator cut apart from one L1
                    # minted in this course. The fork is not a node, so this is where its
                    # identity lives on.
                    "course_kind": course.course_kind,
                    "trigger": course.trigger,
                    "steered_by": course.steered_by,
                }
            )
        )

    if not attempts:
        attempts = [_empty_attempt(course, cut_from=cut_from, round_=cut_round + 1)]
    return _Contribution(attempts, [k for c in replays for k in c.children])


def _build(store: Stores, path: CyclePath, *, depth: int, reads: _Reads) -> LineageNode:
    leaf = path[-1]
    index = _read_index(store, leaf)
    ledger_path = _layout(store, leaf).ledger
    candidates = scan_ledger_candidates(ledger_path)
    children = _child_courses(store, path, reads)
    inner = [c for c in children if c.inner]
    # Mint order IS the campaign's timeline, so it is what positions a fork on it.
    forks = sorted(
        (c for c in children if not c.inner), key=lambda c: (c.created_at, c.path[-1].cycle_id)
    )
    buckets = _bucket_by_parent(inner, candidates)
    hops = list(path)

    closed = _close_facts(ledger_path, candidates)
    no_close = _CloseFacts(
        is_winner=False,
        theta=None,
        theta_se=None,
        cumulative_theta=None,
        composite_ci_lo=None,
        composite_ci_hi=None,
    )

    # Every fork, resolved BEFORE our own candidates are built — a fork's replayed origin
    # grafts its runs onto the candidate it replays, so that candidate cannot be finished
    # until every fork cut from it has been asked what it hands back.
    by_id = {c.candidate_id: c for c in candidates}
    contributions: list[_Contribution] = []
    grafts: dict[str, list[LineageNode]] = {}
    for fork in forks:
        cut_from = _parent_candidate_of(fork, candidates)
        cut = by_id.get(cut_from)
        contribution = _contributions(
            fork, cut_from=cut_from, cut_round=cut.round if cut else 0, depth=depth, reads=reads
        )
        contributions.append(contribution)
        grafts.setdefault(cut_from, []).extend(contribution.replayed_runs)

    kids: list[LineageNode] = []
    for cand in candidates:
        under = buckets.get(cand.candidate_id, [])
        replayed = grafts.get(cand.candidate_id, [])
        # Both sides are appends to one ledger, so identity joins — see `_close_facts`.
        close = closed.get(cand.candidate_id, no_close)
        # The whisker is the candidate's OWN, stamped when it finished scoring; a warm-ruler
        # election overrides it with the tighter θ-implied band. Same precedence the engine
        # applies to `ScoredCandidate`, so a mid-round bar and a closed one differ in which
        # interval they show, never in whether they show one.
        ci = (
            (close.composite_ci_lo, close.composite_ci_hi)
            if close.composite_ci_lo is not None
            else (cand.composite_ci_lo, cand.composite_ci_hi)
        )
        kids.append(
            LineageNode(
                kind="candidate",
                id=cand.candidate_id,
                parent_id=cand.parent_id,
                label=cand.label,
                # This course minted it, so the two labels agree. They diverge only where
                # the renumber below rewrites `label` on a fork's contribution.
                course_label=cand.label,
                path=hops,
                round=cand.round,
                accuracy=cand.accuracy,
                composite_fitness=cand.composite_fitness,
                state=cand.state,
                evaluators=cand.evaluators,
                composite_ci_lo=ci[0],
                composite_ci_hi=ci[1],
                scored_samples=cand.scored_samples,
                expected_samples=cand.expected_samples,
                is_winner=close.is_winner,
                theta=close.theta,
                theta_se=close.theta_se,
                cumulative_theta=close.cumulative_theta,
                children=[
                    _build(c.store, c.path, depth=depth - 1, reads=reads)
                    for c in under
                    if depth > 0
                ]
                + replayed,
            )
        )

    # THE TIMELINE. One sequence per campaign: the candidates L1 minted in this course, plus
    # every attempt its forks contributed, renumbered here — because `C{round}.{n}` is a
    # position in a course's PRIVATE sequence, so a fork's own counter names nothing
    # campaign-wide (four forks cut off C0 each minted a `C1.1`). Renumbering onto this
    # sequence is what makes the label mean something. Nothing renumbers a candidate this
    # course minted itself.
    #
    # `course_label` deliberately survives this untouched, and that is the whole point of the
    # field: the renumber is the ONLY place the fork's private position was ever lost, and a
    # consumer joining against the fork's own per-cycle `dashboard.json` needs it back. Do not
    # add `course_label` to the update dict below.
    by_round: dict[int, int] = {}
    for k in kids:
        by_round[k.round or 0] = by_round.get(k.round or 0, 0) + 1
    for contribution in contributions:
        for attempt in contribution.attempts:
            round_ = attempt.round or 0
            by_round[round_] = by_round.get(round_, 0) + 1
            kids.append(attempt.model_copy(update={"label": f"C{round_}.{by_round[round_]}"}))

    # Stable, so within a round the order is arrival order — own candidates in ledger order,
    # then the forks by mint time. That is exactly the order the labels were assigned in.
    kids.sort(key=lambda k: k.round or 0)

    edge_id, _ = _course_edge(index)
    return LineageNode(
        kind="course",
        id=leaf.cycle_id,
        parent_id=edge_id or (candidates[0].parent_id if candidates else None),
        label=leaf.cycle_id,
        # A course is never renumbered — nothing folds it onto another course's timeline —
        # so its two labels are the same fact. Set rather than defaulted: the field is
        # required, and a course that omitted it would be a hole a candidate join falls into.
        course_label=leaf.cycle_id,
        path=hops,
        children=kids,
        **_course_scalars(store, leaf, index, reads),
    )


def build_lineage_tree(store: Stores, path: CyclePath) -> LineageNode:
    """The course at *path* and its subtree, expanded to :data:`_MAX_COURSE_DEPTH`.

    Each level costs one ledger scan plus two small JSON reads per course.
    """
    store_at, _ = resolve_cycle_path(store, path)
    return _build(store_at, path, depth=_MAX_COURSE_DEPTH, reads=_Reads())
