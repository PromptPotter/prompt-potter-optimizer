"""The lineage tree — ONE served shape, one owner, read at any depth.

Nodes alternate, forever::

    Course -> Candidate -> (Course | Sample) -> ...

A **course** is a cycle (a campaign root, a fork, or an L4 inner run). A **candidate**
is C0 / C1.1 / … — a searchpoint the loop minted. At L4 a candidate's children ARE
courses; that is the recursion, and it is why L5+ needs no new tier.

Three collapses make it one thing rather than five:

1. **A fork and an inner run are the same edge.** ``index.json::fork.from_candidate_id``
   and ``index.json::spawned_by`` both mean "a course hanging off candidate X". One
   resolver reads both, so the fork-lane geometry (``round_column_offset``,
   post-divergence round filtering) has nothing left to compute — a course hangs off a
   node; it does not need a lane.
2. **Rounds are not a tier.** ``CandidateMintedRecord.parent_id`` is the real
   candidate→candidate edge and it already spans rounds AND cycles: a fork's C0 points at
   the candidate the fork was cut from. ``round`` survives as a column hint.
3. **The label is minted, not derived.** It rides the ledger; nothing here re-derives
   ``C{r}.{n}`` from a list position.

**Why the ledger.** A round that never closed writes no round file and no ``dashboard.json``
summary, but it *minted* its candidates — and at L4 their inner campaigns sit finished on
disk under a parent that, read positionally, does not exist. The ledger is the only witness
independent of round close, which is the entire reason IDENTITY comes from it.
``rounds/round_NNNN.json`` stays what it always was: the measurement record. It is not a
lineage source.

**And why the dashboard, still.** Election, θ and ``cumulative_accuracy`` are round-CLOSE
facts that the ledger genuinely does not carry (see :func:`_close_facts`), so they ride
``dashboard.json::rounds[]`` — the declared projection for exactly that. This is not the
old five-witness problem returning: identity has ONE owner (the ledger), measurement has
ONE owner (the round close), and no surface re-derives either. A round that never closed
simply has no winner here, which is the honest answer and the one the old surfaces refused
to give.

**The decision genealogy is NOT this one and must not become it.** ``mask/backprop.py``,
``mask/divergence.py`` and the resume replayers ride the positional ``(cycle_id, round)``
+ ``parent_cycle_id`` system, deliberately: it is hardened, moving it has no functional
gain, and it cannot be ``ab``-validated. This module is a READ MODEL for display. It
decides nothing.
"""

from __future__ import annotations

from typing import Literal, NamedTuple

from pydantic import BaseModel, ConfigDict, Field

from promptpotter.domain.cycle_paths import CycleHop, CyclePath
from promptpotter.domain.run_records import LedgerCandidate
from promptpotter.infrastructure.store.campaign_store.ledger_scan import scan_ledger_candidates
from promptpotter.infrastructure.store.io import read_json_optional
from promptpotter.infrastructure.store.layout import cycle_dir_for, sibling_kind
from promptpotter.infrastructure.store.stores import Stores, inner_sandbox_store, resolve_cycle_path

__all__ = ["LineageDivergence", "LineageNode", "build_lineage_tree"]


NodeKind = Literal["course", "candidate"]
CourseKind = Literal["root", "fork", "diag", "sweep", "inner"]


class LineageDivergence(BaseModel):
    """Where an alternative criterion would have elected someone else.

    It rides the node it describes (``LineageNode.divergence``) rather than a parallel list
    keyed ``"{cycle_id}::r{round}"``: the client had to re-join two flat arrays onto the tree
    by a hand-rolled string, and a tree that knows its own nodes can simply carry the fact.
    """

    model_config = ConfigDict(frozen=True)

    alternative_candidate_id: str | None = Field(
        default=None,
        description="The candidate the masked criterion would have elected instead "
        "(measured, so nameable); null when the round would simply have held on origin.",
    )


class LineageNode(BaseModel):
    """One node of the served tree. The same shape at every depth — that is the point."""

    model_config = ConfigDict(frozen=True)

    kind: NodeKind = Field(description="course | candidate — they strictly alternate")
    id: str = Field(
        description="Course: the cycle_id. Candidate: the searchpoint id minted at L1/origin."
    )
    parent_id: str | None = Field(
        default=None,
        description="The candidate this node descends from; null only at the true root. A "
        "course carries the same edge its own C0 carries — a course descends from nothing "
        "its origin does not.",
    )
    label: str = Field(description="Minted, never derived from list position.")
    path: list[CycleHop] = Field(
        default_factory=list,
        description="The addressable CyclePath of the course this node belongs to, root → "
        "leaf. What lets a client select an inner node without re-deriving how to reach it.",
    )
    children: list[LineageNode] = Field(default_factory=list)
    children_available: bool = Field(
        default=False,
        description="True with `children` empty = the lazy boundary: this node HAS children "
        "the request's `depth` did not expand. False + empty = it genuinely has none. A "
        "client must never read an empty list as proof of a leaf.",
    )

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
        description="Elected this round. False throughout a round that never closed: election "
        "is stamped at close, so such a round HAS no winner and this tree declines to invent "
        "one for a lone candidate.",
    )
    round_closed: bool = Field(
        default=False,
        description="Did this candidate's round CLOSE? It is what separates the two ways a "
        "round can crown nobody: closed with no winner = HELD (it ran, nothing beat the "
        "incumbent — render it held); not closed = the round never finished, so there is no "
        "election to report. `is_winner` alone collapses those two into one false, which is "
        "how a never-closed round's lone candidate got promoted to a fake winner.",
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
    cumulative_accuracy: float | None = Field(
        default=None,
        description="The adopted lineage rescored over EVERY sample probed so far — the "
        "cross-round-comparable frontier the trend plots. Carried by the round's WINNER only: "
        "it is a property of the advancing spine, and a losing sibling never joined it.",
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
        description="Set when the request's lens would have FORKED the record at this node — "
        "the marker. It rides the node itself; there is no parallel list to re-join by a "
        "hand-rolled key. Only ever set on a closed round's node.",
    )
    divergent: bool = Field(
        default=False,
        description="This node is inside the counterfactual subtree below a divergence — the "
        "client dims it.",
    )

    # -- course scalars -------------------------------------------------------
    course_kind: CourseKind | None = Field(default=None, description="Courses only.")
    dataset_name: str = ""
    trigger: str = Field(default="", description="Fork trigger; empty for roots and inner runs.")
    steered_by: str | None = Field(default=None, description="Operator who cut this fork.")
    task: str | None = Field(
        default=None,
        description="An inner run's benchmark task. Load-bearing: every task runs for every "
        "candidate, so the candidate edge alone does not identify an inner run.",
    )
    best_accuracy: float | None = None
    hearts: int | None = None
    lives_cap: int | None = None


class _Course(NamedTuple):
    """A child course and how to reach it — the recursion's unit of work."""

    store: Stores
    path: CyclePath
    # NOT `index` — a NamedTuple field by that name shadows `tuple.index`.
    manifest: dict[str, object]


def _in_sandbox(store: Stores) -> bool:
    """Is this store rooted in an L4 sandbox? ``build_stores`` roots one at
    ``.inner/<cycle_id>``, so the answer is in the root path — no flag to thread."""
    return ".inner" in store.projects_root.parts


def _read_index(store: Stores, hop: CycleHop) -> dict[str, object]:
    index = read_json_optional(
        cycle_dir_for(store.base_dir, hop.campaign_id, hop.cycle_id) / "index.json"
    )
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
    """What only a round CLOSE knows about a candidate. Presence in the map IS
    ``round_closed`` — a round that never closed has no entry at all."""

    is_winner: bool
    theta: float | None
    theta_se: float | None
    cumulative_accuracy: float | None


def _close_facts(store: Stores, hop: CycleHop) -> dict[str, _CloseFacts]:
    """``candidate_id -> _CloseFacts`` off the cycle's ``dashboard.json::rounds[]``.

    **The election and θ are not on the ledger and cannot be folded from it.** The
    ``ROUND_WINNER`` decision is appended to a plain list that lands in the round file
    (``record_decision``'s sink is polymorphic — only some call sites pass the
    ``CycleEventLog``), and θ is stamped onto the candidate at election time, *after* its
    ``candidate_scored`` snapshot was emitted. ``cumulative_accuracy`` is the same story:
    ``on_round_complete`` persists a lean 3-scalar ``round_result`` and the full
    ``RoundResult`` rides an in-memory-only field.

    So these three ride ``dashboard.json::rounds[]``, the declared round-close projection —
    the same file this course already opens for ♥, asked one more question. Identity stays
    the ledger's; this is measurement, and measurement is what a closed round writes down.

    A round that never closed has no entry, so its candidates get no winner and no θ — which
    is the honest answer, and the one this tree exists to give.
    """
    dash = read_json_optional(
        cycle_dir_for(store.base_dir, hop.campaign_id, hop.cycle_id) / "dashboard.json"
    )
    rounds = dash.get("rounds") if isinstance(dash, dict) else None
    out: dict[str, _CloseFacts] = {}
    for r in rounds if isinstance(rounds, list) else []:
        if not isinstance(r, dict):
            continue
        cum = r.get("cumulative_accuracy")
        cands = r.get("candidates")
        for c in cands if isinstance(cands, list) else []:
            if not isinstance(c, dict) or not isinstance(cid := c.get("candidate_id"), str):
                continue
            won = bool(c.get("is_winner"))
            out[cid] = _CloseFacts(
                is_winner=won,
                theta=_float_or_none(c.get("theta")),
                theta_se=_float_or_none(c.get("theta_se")),
                # The frontier belongs to the spine: only the elected candidate advanced it.
                cumulative_accuracy=_float_or_none(cum) if won else None,
            )
    return out


def _float_or_none(value: object) -> float | None:
    return float(value) if isinstance(value, int | float) and not isinstance(value, bool) else None


def _course_scalars(store: Stores, hop: CycleHop, index: dict[str, object]) -> dict[str, object]:
    """The course's own facts: topology from ``index.json``, live ♥ from the dashboard."""
    cdir = cycle_dir_for(store.base_dir, hop.campaign_id, hop.cycle_id)
    dash = read_json_optional(cdir / "dashboard.json")
    dash = dash if isinstance(dash, dict) else {}

    fork, spawned = _block(index, "fork"), _block(index, "spawned_by")
    limits = dash.get("run_limits") if isinstance(dash.get("run_limits"), dict) else {}
    best, hearts = index.get("best_accuracy"), dash.get("hearts")
    cap = limits.get("lives_cap") if isinstance(limits, dict) else None
    campaign = store.campaigns.load_campaign(hop.campaign_id)

    # A course is INNER because of where it lives, not because it remembered to say so:
    # the rebase pair on disk carries no `spawned_by` yet sits in the sandbox, and calling
    # those "root" would put two roots in one tree. A fork of an inner run stays a fork —
    # only the sandbox's own roots are the recursion's entry point.
    kind: CourseKind = sibling_kind(hop.cycle_id)
    if kind == "root" and (spawned or _in_sandbox(store)):
        kind = "inner"

    return {
        "course_kind": kind,
        "state": str(index.get("status") or ""),
        "trigger": str(fork.get("trigger") or ""),
        "steered_by": _str_or_none(fork.get("issued_by")),
        "task": _str_or_none(spawned.get("task")),
        "dataset_name": campaign.dataset_name if campaign else "",
        "best_accuracy": float(best) if isinstance(best, int | float) else None,
        "hearts": hearts if isinstance(hearts, int) else None,
        "lives_cap": cap if isinstance(cap, int) else None,
    }


def _candidates_of(store: Stores, hop: CycleHop) -> list[LedgerCandidate]:
    ledger = (
        cycle_dir_for(store.base_dir, hop.campaign_id, hop.cycle_id) / ".runtime" / "ledger.jsonl"
    )
    return scan_ledger_candidates(ledger)


def _child_courses(store: Stores, path: CyclePath) -> list[_Course]:
    """Every course hanging off the course at *path* — forks AND inner runs, one list.

    Collapse #1 in code: a fork is a sibling cycle in the SAME store whose
    ``parent_cycle_id`` is ours (so its path replaces our leaf); an inner run is a campaign
    ROOT inside our ``.inner/<cycle_id>`` sandbox (so its path extends ours by one hop). Two
    directories, one kind of thing — callers never branch on which.

    Only sandbox ROOTS are taken: an inner run's own forks are that course's children, and
    lifting them here would attach a grandchild to us and draw it twice.
    """
    leaf = path[-1]
    out: list[_Course] = []
    for entry in store.campaigns.enumerate_cycles():
        if entry.get("parent_cycle_id") != leaf.cycle_id:
            continue
        hop = CycleHop(campaign_id=str(entry["campaign_id"]), cycle_id=str(entry["cycle_id"]))
        out.append(_Course(store=store, path=(*path[:-1], hop), manifest=_read_index(store, hop)))

    sandbox = inner_sandbox_store(store, leaf.cycle_id)
    if sandbox is not None:
        for entry in sandbox.campaigns.enumerate_cycles():
            if entry.get("parent_cycle_id"):
                continue
            hop = CycleHop(campaign_id=str(entry["campaign_id"]), cycle_id=str(entry["cycle_id"]))
            out.append(
                _Course(store=sandbox, path=(*path, hop), manifest=_read_index(sandbox, hop))
            )
    return out


def _bucket_by_parent(
    courses: list[_Course], candidates: list[LedgerCandidate]
) -> dict[str, list[_Course]]:
    """Attach each child course to the candidate it descends from.

    Resolution order — id, then label, then the origin. The label join is scoped to THIS
    course's candidates, so it is a join on a minted key within one namespace, not a guess.
    A course that resolves to nothing still descends from something: C0 is the one candidate
    every course is guaranteed to have, and attaching there beats floating loose — which is
    exactly the bug this tree exists to end.
    """
    by_label = {c.label: c.candidate_id for c in candidates}
    known = {c.candidate_id for c in candidates}
    origin = candidates[0].candidate_id if candidates else ""
    out: dict[str, list[_Course]] = {}
    for course in courses:
        cid, label = _course_edge(course.manifest)
        target = cid if cid in known else by_label.get(label or "", origin)
        if target:
            out.setdefault(target, []).append(course)
    return out


def _build(store: Stores, path: CyclePath, *, depth: int) -> LineageNode:
    leaf = path[-1]
    index = _read_index(store, leaf)
    candidates = _candidates_of(store, leaf)
    buckets = _bucket_by_parent(_child_courses(store, path), candidates)
    hops = list(path)

    closed = _close_facts(store, leaf)
    no_close = _CloseFacts(is_winner=False, theta=None, theta_se=None, cumulative_accuracy=None)

    kids: list[LineageNode] = []
    for cand in candidates:
        under = buckets.get(cand.candidate_id, [])
        close = closed.get(cand.candidate_id, no_close)
        kids.append(
            LineageNode(
                kind="candidate",
                id=cand.candidate_id,
                parent_id=cand.parent_id,
                label=cand.label,
                path=hops,
                round=cand.round,
                accuracy=cand.accuracy,
                composite_fitness=cand.composite_fitness,
                state=cand.state,
                evaluators=cand.evaluators,
                composite_ci_lo=cand.composite_ci_lo,
                composite_ci_hi=cand.composite_ci_hi,
                scored_samples=cand.scored_samples,
                expected_samples=cand.expected_samples,
                is_winner=close.is_winner,
                round_closed=cand.candidate_id in closed,
                theta=close.theta,
                theta_se=close.theta_se,
                cumulative_accuracy=close.cumulative_accuracy,
                children=[_build(c.store, c.path, depth=depth - 1) for c in under if depth > 0],
                children_available=bool(under),
            )
        )

    edge_id, _ = _course_edge(index)
    return LineageNode(
        kind="course",
        id=leaf.cycle_id,
        parent_id=edge_id or (candidates[0].parent_id if candidates else None),
        label=leaf.cycle_id,
        path=hops,
        children=kids,
        children_available=bool(candidates),
        **_course_scalars(store, leaf, index),
    )


def build_lineage_tree(store: Stores, path: CyclePath, *, depth: int = 1) -> LineageNode:
    """The course at *path* and its subtree, expanded *depth* course-levels down.

    ``depth=0`` returns the course with its candidates and ``children_available`` set
    wherever a candidate has courses beneath it — the lazy boundary a sidebar expands into.
    Each level costs one ledger scan plus two small JSON reads per course, so depth is the
    caller's cost dial and never a hidden recursion.
    """
    store_at, _ = resolve_cycle_path(store, path)
    return _build(store_at, path, depth=depth)
