"""The lineage tree — the served genealogy, read at any depth. A fork is not a node; its
candidates mount onto the parent's ONE timeline. The law: `infrastructure/CLAUDE.md`."""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from typing import Any, Literal, NamedTuple

from pydantic import ConfigDict, Field

from promptpotter.domain.campaign import Campaign
from promptpotter.domain.cycle_paths import CycleHop, CyclePath
from promptpotter.domain.phases import RunPhase
from promptpotter.domain.ruler import ThetaCaveat
from promptpotter.domain.run_records import (
    FORK_DIRECTION,
    ForkDirection,
    ForkTrigger,
    LedgerAbility,
    LedgerCandidate,
    LedgerFit,
)
from promptpotter.domain.strict_model import StrictModel
from promptpotter.infrastructure.runtime_flags import derive_run_phase
from promptpotter.infrastructure.store.campaign_store.ledger_scan import (
    scan_ledger_candidates,
    scan_ledger_elections,
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

# A COST BOUND on `.inner/` NESTING, never a caller's dial: one served tree per campaign, and
# sandboxes nest re-entrantly, so an unbounded walk is unbounded on disk. 3 covers L4.
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
        "free index of its round, by mint time — UNLESS the cut superseded, where it keeps "
        "its own label because it replaced that position rather than joining it, and the "
        "candidate it replaced carries `superseded_by`. So one label can appear twice in a "
        "round: at most once LIVE, the other retired.",
    )
    course_label: str = Field(
        description="This candidate's label in the course that MINTED it. Equal to `label` "
        "for a candidate this course minted itself; a fork-contributed attempt keeps the "
        "fork's private `C{round}.{n}` here while `label` carries its renumbered position "
        "on this course's timeline. JOIN ON THIS, never on `candidate_id`, when matching a "
        "node against a per-cycle projection: `dashboard.json` is per-cycle and speaks the "
        "minting course's private counter, while `candidate_id` is re-minted per run (see "
        "`_round_facts`), so an id join silently misses.",
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
    status: str = Field(
        default="",
        description="Candidate: minted | measured | invalid — never 'winner' (that rides "
        "`is_winner`). `invalid` was rejected before it cost a sample, so it carries no "
        "accuracy: its stored 0.0 is synthetic and reads as getting every answer wrong. "
        "Course: `index.json::status`, the same StopReason value `/cycles` serves under this "
        "same name. Not `dashboard.json::state`, which names the fine-grained ACTIVITY "
        "phase — a different axis, and one word may not serve both.",
    )
    election_held: bool = Field(
        default=False,
        description="This candidate's ROUND has held its election. The complement `is_winner` "
        "cannot supply: a round that HELD crowned nobody, so every bar in it reads "
        "`is_winner: false` exactly as a round still scoring does — and only this says whether "
        "an uncrowned bar lost or has not been judged yet. False on a course, which is not a "
        "round, and on a round halted before it stood (a holed panel).",
    )
    is_winner: bool = Field(
        default=False,
        description="Elected this round. Stamped at the ELECTION, which is the last thing "
        "scoring does — so it lands a whole `l1_critique` call before the round closes, and a "
        "round still running its optimizer calls already reports its winner. False where no "
        "election has been held (still scoring, or halted on a holed panel) and on a round that "
        "held: those two are told apart by the election record, not by this flag.",
    )
    theta: float | None = Field(
        default=None,
        description="Difficulty-adjusted Rasch ability the election ranked on — what explains "
        "a lower-accuracy winner. Null outside the round's election fit.",
    )
    theta_se: float | None = None
    theta_caveat: ThetaCaveat | None = Field(
        default=None,
        description="Why the theta above is not this arm's ability. Only ever `floor_pinned` — "
        "the arm scored 0.0 on every cell it answered, so the fit had no response to separate "
        "ability from the prior and every lift against it reads 0.000. The other three caveats "
        "are properties of the round's scale and ride the round's own reading.",
    )
    evaluators: dict[str, float] = Field(
        default_factory=dict,
        description="The candidate's stored evaluator namespace — the measurement a `score:` "
        "lens re-scores against.",
    )
    mean_fitness_ci_lo: float | None = None
    mean_fitness_ci_hi: float | None = None
    matched_parent_lift: float | None = Field(
        default=None,
        description="The candidate's blocked lift over the floor it was JUDGED against — the "
        "origin restricted to the cells this candidate actually measured — with its 95% "
        "interval. The election's own verdict, and the only comparable answer to 'by how "
        "much': under `per_round_resubset` a bare difference of two accuracies is the "
        "luckiest draw minus the fullest one. An interval spanning 0 means the round could "
        "not separate this candidate from its parent. `None` below two shared cells, outside "
        "the election fit, and on any round that has not elected yet.",
    )
    matched_parent_lift_ci_lo: float | None = None
    matched_parent_lift_ci_hi: float | None = None
    scored_samples: int | None = None
    expected_samples: int | None = None
    cached_samples: int | None = Field(
        default=None,
        description="Of `scored_samples`, how many were replayed from the MeasurementArchive "
        "rather than measured. `None` on a course and on any candidate never measured.",
    )
    lens_value: float | None = Field(
        default=None,
        description="This candidate's fitness under the request's `score:` lens, re-scored "
        "server-side from its stored evaluator namespace. Null without a lens, or when the "
        "namespace can't satisfy the formula.",
    )
    composite_rank: int | None = Field(
        default=None,
        description="1-based position by `composite_fitness` descending among THIS node's "
        "siblings — the bars one chart draws. Null where the value is. An ordering is a "
        "score, so it is served rather than sorted client-side; the rank-shift read-out "
        "against `lens_rank` is then a comparison of two served numbers.",
    )
    lens_rank: int | None = Field(
        default=None,
        description="The same sibling ordering under `lens_value`. Null without a lens. Read "
        "against `composite_rank` to see which candidates the alternative formula moves.",
    )
    sample_set_accuracy: float | None = Field(
        default=None,
        description="Scorer-faithful accuracy over the request's `samples=` subset. Null "
        "without a `samples=` mask, or when this candidate never ran any selected sample.",
    )
    sample_set_n: int | None = Field(
        default=None,
        description="How many of the `samples=` subset this candidate carries a SCOREABLE verdict "
        "for — the denominator `sample_set_accuracy` is the mean over. Below the subset size, the "
        "candidate sat a different exam from one that answered all of it.",
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
    fork_direction: ForkDirection | None = Field(
        default=None,
        description="Which side of this cut the run CONTINUES on, derived from `trigger` "
        "(`FORK_DIRECTION`). `offshoot` = this branch hangs off a line that keeps running; "
        "`supersede` = this branch IS the line and the PARENT is what was left behind. Null "
        "for roots and inner runs, which were not cut from anything. Served, never derived "
        "in the client — the two read identically on disk and only this says them apart.",
    )
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
    """A course in the family and how to reach it — the recursion's unit of work. Public because
    ``family_ray_views.py`` walks the same family, and a second walk is a second answer."""

    store: Stores
    path: CyclePath
    # NOT `index` — a NamedTuple field by that name shadows `tuple.index`.
    manifest: dict[str, object]
    # A sandbox root vs a fork/sweep/diag: only a fork contributes attempts to this timeline.
    inner: bool
    # Hops off the family root, stamped by `iter_family_courses`; a fork costs no depth.
    depth: int = 0

    @property
    def created_at(self) -> str:
        """When the course was minted — the campaign timeline's ordering key."""
        value = self.manifest.get("created_at")
        return value if isinstance(value, str) else ""


class _Reads:
    """Every store this tree touches, read ONCE (``enumerate_cycles`` answers a WHOLE store).
    Per-build and thrown away: a memo outliving the request serves a round that has closed."""

    def __init__(self) -> None:
        self._cycles: dict[Path, list[dict[str, object]]] = {}
        self._campaigns: dict[tuple[Path, str], Campaign | None] = {}
        # Dirs visited this build, keyed on the FULL identity — a cycle_id alone is not one
        # (it is a content hash, so campaigns and inner cells repeat it). Terminates both
        # walkers against a `parent_cycle_id` that points back up the chain.
        self.seen: set[tuple[Path, str, str]] = set()

    def cycles(self, stores: Stores) -> list[dict[str, object]]:
        if (key := stores.base_dir) not in self._cycles:
            self._cycles[key] = stores.campaigns.enumerate_cycles()
        return self._cycles[key]

    def campaign(self, stores: Stores, campaign_id: str) -> Campaign | None:
        if (key := (stores.base_dir, campaign_id)) not in self._campaigns:
            self._campaigns[key] = stores.campaigns.load_campaign(campaign_id)
        return self._campaigns[key]


def _layout(stores: Stores, hop: CycleHop) -> CycleLayout:
    """This hop's cycle paths — ``CycleLayout`` owns every filename in the dir."""
    return CycleLayout(cycle_dir_for(stores.base_dir, hop))


def _read_index(stores: Stores, hop: CycleHop) -> dict[str, object]:
    index = read_json_optional(_layout(stores, hop).manifest)
    return index if isinstance(index, dict) else {}


def _block(index: dict[str, object], key: str) -> dict[str, object]:
    block = index.get(key)
    return block if isinstance(block, dict) else {}


def _float_or_none(value: object) -> float | None:
    return float(value) if isinstance(value, int | float) else None


def _str_or_none(value: object) -> str | None:
    return value if isinstance(value, str) and value else None


def _course_edge(index: dict[str, object]) -> tuple[str | None, str | None]:
    """``(candidate_id, candidate_label)`` this course hangs off — the ONE resolver. The label is a
    full-strength fallback; both null = a campaign root or a rebase fork, attached to the origin."""
    fork = _block(index, "fork")
    if cid := _str_or_none(fork.get("from_candidate_id")):
        return cid, None
    spawned = _block(index, "spawned_by")
    if spawned:
        return _str_or_none(spawned.get("candidate_id")), _str_or_none(
            spawned.get("candidate_label")
        )
    return None, None


def _fork_direction(fork: Mapping[str, Any]) -> ForkDirection | None:
    """A cut's direction: the MEASURED one it recorded, else the one its trigger implies. An
    unrecognised trigger returns ``None`` — drawing a supersession as an offshoot is the lie."""
    measured = fork.get("direction")
    if isinstance(measured, str):
        try:
            return ForkDirection(measured)
        except ValueError:
            return None
    trigger = fork.get("trigger")
    if not isinstance(trigger, str) or not trigger:
        return None
    try:
        return FORK_DIRECTION[ForkTrigger(trigger)]
    except (ValueError, KeyError):
        return None


class _RoundFacts(NamedTuple):
    """What a candidate learns from its ROUND, folded from the two records that hold it.

    The ELECTION carries everything it stamps at the end of scoring — the crown, θ, and the
    matched-parent floor the arm was judged against. The CLOSE carries the frontier, and RE-READS
    θ, which is how a warm ruler's restamp reaches round 0; where it answers, it wins. Neither
    implies the other — a round elects an ``l1_critique`` call before it closes.

    The whisker is in neither: it is the candidate's own, and a round-scoped copy drew two
    quantities as one band."""

    election_held: bool = False
    is_winner: bool = False
    theta: float | None = None
    theta_se: float | None = None
    theta_caveat: ThetaCaveat | None = None
    matched_parent_lift: float | None = None
    matched_parent_lift_ci_lo: float | None = None
    matched_parent_lift_ci_hi: float | None = None


def _round_facts(ledger_path: Path, candidates: list[LedgerCandidate]) -> dict[str, _RoundFacts]:
    """``candidate_id -> _RoundFacts``, folded from the cycle's OWN ledger — the whole fold, so the
    tree is no longer a projection of another projection. **The join stays on ``label``**:
    ``candidate_id`` is a fresh uuid per construction, and a resume re-mints it.

    The lift used to be read out of ``dashboard.json::rounds[]`` because the election stamped it
    after the ``candidate_scored`` snapshot and the ledger's candidate tier served an all-null
    column. It rides ``ElectionRecord`` now, at the moment it is stamped."""
    elections = scan_ledger_elections(ledger_path)
    closes = scan_ledger_round_closes(ledger_path)
    out: dict[str, _RoundFacts] = {}
    for cand in candidates:
        election = elections.get(cand.round)
        close = closes.get(cand.round)
        if election is None and close is None:
            continue
        # A HELD round adopted the parent, which is not among these — so nobody is crowned.
        won = (
            election is not None
            and bool(election.winner_label)
            and cand.label == election.winner_label
        )
        fit = (election.fit.get(cand.label) if election is not None else None) or LedgerFit()
        # The close WINS where it answers: it re-reads θ on every close, which is the channel
        # round 0's warm-ruler restamp arrives on. Everywhere else the two agree — same
        # `candidate_scores`, read twice — so the election's copy is simply the earlier one.
        ability = (close.abilities.get(cand.label) if close is not None else None) or LedgerAbility(
            theta=fit.theta, theta_se=fit.theta_se, theta_caveat=fit.theta_caveat
        )
        out[cand.candidate_id] = _RoundFacts(
            election_held=election is not None,
            is_winner=won,
            theta=ability.theta,
            theta_se=ability.theta_se,
            theta_caveat=ability.theta_caveat,
            matched_parent_lift=fit.matched_parent_lift,
            matched_parent_lift_ci_lo=fit.matched_parent_lift_ci_lo,
            matched_parent_lift_ci_hi=fit.matched_parent_lift_ci_hi,
        )
    return out


def _course_scalars(
    stores: Stores,
    hop: CycleHop,
    index: dict[str, object],
    reads: _Reads,
    dash: dict[str, object],
) -> dict[str, object]:
    """The course's own facts: topology from ``index.json``, live ♥ from the dashboard."""
    layout = _layout(stores, hop)

    fork, spawned = _block(index, "fork"), _block(index, "spawned_by")
    limits = dash.get("run_limits") if isinstance(dash.get("run_limits"), dict) else {}
    best, hearts = index.get("best_accuracy"), dash.get("hearts")
    cap = limits.get("lives_cap") if isinstance(limits, dict) else None
    campaign = reads.campaign(stores, hop.campaign_id)

    # INNER by where it LIVES, not by saying so: a rebase pair in the sandbox has no
    # `spawned_by`, and calling it "root" puts two roots in one tree.
    kind: CourseKind = sibling_kind(hop.cycle_id)
    if kind == "root" and (spawned or ".inner" in stores.projects_root.parts):
        kind = "inner"

    return {
        "course_kind": kind,
        "status": str(index.get("status") or ""),
        # The ONE run-phase derivation, the same call `/cycles` makes.
        "run_phase": str(
            derive_run_phase(layout.cycle_dir, is_terminal=bool(index.get("finished_at")))
        ),
        "trigger": str(fork.get("trigger") or ""),
        "fork_direction": _fork_direction(fork),
        "steered_by": _str_or_none(fork.get("issued_by")),
        "task": _str_or_none(spawned.get("task")),
        "dataset_name": campaign.dataset_name if campaign else "",
        "best_accuracy": float(best) if isinstance(best, int | float) else None,
        # The SAME derivation `/cycles` uses — no stored copy to drift from.
        "origin_accuracy": origin_accuracy_of(index),
        "hearts": hearts if isinstance(hearts, int) else None,
        "lives_cap": cap if isinstance(cap, int) else None,
    }


def _child_courses(stores: Stores, path: CyclePath, reads: _Reads) -> list[FamilyCourse]:
    """Every course hanging off *path* — forks AND inner runs in one list, so callers never branch.
    Sandbox ROOTS only, matched on the full ``(campaign_id, cycle_id)``: a bare id reaches out."""
    leaf = path[-1]
    reads.seen.add((stores.base_dir, leaf.campaign_id, leaf.cycle_id))
    out: list[FamilyCourse] = []
    for entry in reads.cycles(stores):
        if entry.get("parent_cycle_id") != leaf.cycle_id:
            continue
        if entry.get("campaign_id") != leaf.campaign_id:
            continue
        hop = CycleHop(campaign_id=str(entry["campaign_id"]), cycle_id=str(entry["cycle_id"]))
        if (key := (stores.base_dir, hop.campaign_id, hop.cycle_id)) in reads.seen:
            continue
        reads.seen.add(key)
        out.append(
            FamilyCourse(
                store=stores,
                path=(*path[:-1], hop),
                manifest=_read_index(stores, hop),
                inner=False,
            )
        )

    sandbox = inner_sandbox_store(stores, leaf.campaign_id, leaf.cycle_id)
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


def iter_family_courses(stores: Stores, path: CyclePath) -> list[FamilyCourse]:
    """The whole family rooted at *path* — the FLAT view of what :func:`_build` walks recursively,
    same helpers. Order is stable so the time-ray's ETag holds across identical requests."""
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
        level.sort(key=lambda c: (c.created_at, c.path[-1].campaign_id, c.path[-1].cycle_id))
        out.extend(level)
        frontier = level
    return out


def _parent_candidate_of(course: FamilyCourse, candidates: list[LedgerCandidate]) -> str:
    """The candidate this course descends from: id, then label, then the origin. The label join is
    scoped to THIS course's candidates, so it is a minted key rather than a guess."""
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
    """``candidate_id -> the branch that replaced it``, for the tail a SUPERSEDE cut retired.
    Bounded by BOTH the cut and *reach* — retirement is a replacement, not a position."""
    spec = _block(fork.manifest, "fork")
    if _fork_direction(spec) is not ForkDirection.SUPERSEDE or reach is None:
        return {}
    edge = _str_or_none(spec.get("from_candidate_id"))
    cut = next((i + 1 for i, c in enumerate(candidates) if c.candidate_id == edge), None)
    if cut is None:
        cut_round = spec.get("from_round")
        if not isinstance(cut_round, int) or isinstance(cut_round, bool):
            return {}
        cut = next((i for i, c in enumerate(candidates) if c.round >= cut_round), len(candidates))
    branch = fork.path[-1].cycle_id
    return {c.candidate_id: branch for c in candidates[cut:] if c.round <= reach}


def _is_replay(node: LineageNode) -> bool:
    """A ``C0`` that descends from a candidate IS that candidate re-run, so it merges into what it
    replays. Structural, not a convention: only a fork's origin has a parent, because it borrows."""
    return node.label == "C0" and node.parent_id is not None


def _empty_attempt(course: LineageNode, *, cut_from: str, round_: int) -> LineageNode:
    """A fork that put no candidate on the timeline still holds its row. ``accuracy`` is None
    on purpose: its ``best_accuracy`` is seeded from the PARENT."""
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

    attempts: list[LineageNode]  # never empty — a fork with none gets `_empty_attempt`
    replayed_runs: list[LineageNode]  # they measured the candidate the fork's C0 replays
    supersedes: bool  # retired what it replaced → `superseded_by` + the label rule
    takes_the_line: bool  # the RUN moved here (all but offshoot) → delegation + the ⑂ strip
    reach: int | None  # last round this fork minted for; bounds the retirement
    course: LineageNode  # held, not re-derived — `_build` reads the campaign's liveness off it


def _contributions(
    fork: FamilyCourse, *, cut_from: str, cut_round: int, depth: int, reads: _Reads
) -> _Contribution:
    """A fork, resolved onto the timeline of the course it was cut in. ``depth`` passes straight
    through — a fork is not a course node, so its candidates sit at this course's depth."""
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
                    # The replay is gone, so an attempt off it would dangle.
                    "parent_id": cut_from if cand.parent_id in replay_ids else cand.parent_id,
                    # The ⑂ stamp — a fork is not a node, so its identity lives here.
                    "course_kind": course.course_kind,
                    "trigger": course.trigger,
                    "fork_direction": course.fork_direction,
                    "steered_by": course.steered_by,
                }
            )
        )

    # BEFORE `_empty_attempt` fabricates a row: a stand-in is no evidence of reach.
    reach = max((c.round or 0 for c in course.children), default=None)
    if not attempts:
        attempts = [_empty_attempt(course, cut_from=cut_from, round_=cut_round + 1)]
    return _Contribution(
        attempts,
        [k for c in replays for k in c.children],
        course.fork_direction is ForkDirection.SUPERSEDE,
        course.fork_direction is not None and course.fork_direction is not ForkDirection.OFFSHOOT,
        reach,
        course,
    )


def _fold_contributions(
    kids: list[LineageNode], contributions: list[_Contribution]
) -> list[LineageNode]:
    """Mount every fork's attempts onto this course's ONE timeline, three cases in the order asked.
    ``course_label`` survives untouched — it is the fork's private position."""
    by_round: dict[int, int] = {}
    for k in kids:
        by_round[k.round or 0] = by_round.get(k.round or 0, 0) + 1
    at_id = {k.id: i for i, k in enumerate(kids)}
    positions = {k.label for k in kids}
    for contribution in contributions:
        for attempt in contribution.attempts:
            if contribution.takes_the_line and contribution.reach is not None:
                attempt = attempt.model_copy(
                    update={
                        "course_kind": None,
                        "trigger": "",
                        "fork_direction": None,
                        "steered_by": None,
                    }
                )
            twin = at_id.get(attempt.id)
            if twin is not None and not contribution.supersedes:
                kids[twin] = attempt.model_copy(
                    update={
                        "label": kids[twin].label,
                        "course_label": kids[twin].course_label,
                        "children": [*kids[twin].children, *attempt.children],
                    }
                )
                continue
            if twin is not None:
                attempt = attempt.model_copy(
                    update={"children": [*kids[twin].children, *attempt.children]}
                )
                kids[twin] = kids[twin].model_copy(update={"children": []})
            round_ = attempt.round or 0
            # Keeping a label claims a position this timeline HAS — `_empty_attempt` carries
            # a cycle_id and replaced nothing, so it renumbers like any other attempt.
            if not (contribution.supersedes and (twin is not None or attempt.label in positions)):
                by_round[round_] = by_round.get(round_, 0) + 1
                attempt = attempt.model_copy(update={"label": f"C{round_}.{by_round[round_]}"})
            positions.add(attempt.label)
            at_id[attempt.id] = len(kids)
            kids.append(attempt)
    # Stable: arrival order within a round, which is the order the labels were assigned in.
    kids.sort(key=lambda k: k.round or 0)
    return kids


_NO_ROUND_FACTS = _RoundFacts()


def _candidate_node(
    cand: LedgerCandidate,
    *,
    close: _RoundFacts,
    children: list[LineageNode],
    retired_by: str | None,
    hops: list[CycleHop],
) -> LineageNode:
    """One candidate as the tree serves it — ledger identity plus what its round decided about
    it, appends to one ledger that identity joins (:func:`_round_facts`)."""
    return LineageNode(
        kind="candidate",
        id=cand.candidate_id,
        parent_id=cand.parent_id,
        label=cand.label,
        # Equal here; they diverge only where the fold renumbers a contribution.
        course_label=cand.label,
        path=hops,
        round=cand.round,
        accuracy=cand.accuracy,
        composite_fitness=cand.composite_fitness,
        status=cand.state,
        evaluators=cand.evaluators,
        # The candidate's own band, and only ever that: the round close no longer carries a
        # second one to prefer over it.
        mean_fitness_ci_lo=cand.mean_fitness_ci_lo,
        mean_fitness_ci_hi=cand.mean_fitness_ci_hi,
        matched_parent_lift=close.matched_parent_lift,
        matched_parent_lift_ci_lo=close.matched_parent_lift_ci_lo,
        matched_parent_lift_ci_hi=close.matched_parent_lift_ci_hi,
        scored_samples=cand.scored_samples,
        expected_samples=cand.expected_samples,
        cached_samples=cand.cached_samples,
        election_held=close.election_held,
        # A RETIRED candidate wears no crown — the branch re-asks that election.
        is_winner=close.is_winner and retired_by is None,
        theta=close.theta,
        theta_se=close.theta_se,
        theta_caveat=close.theta_caveat,
        superseded_by=retired_by,
        children=children,
    )


def rank_by_composite(kids: list[LineageNode]) -> list[LineageNode]:
    """Stamp `composite_rank` across one course's candidate children — the bars one chart
    draws. Served because an ordering IS a score: a client sorting its own re-answers the
    question under its guess at the formula. Courses take none — a course is not a round and
    holds no election. Ties break on id so N bars read 1..N."""
    scored = {
        k.id: k.composite_fitness
        for k in kids
        if k.kind == "candidate" and k.composite_fitness is not None
    }
    if not scored:
        return kids
    position = {
        cid: i + 1
        for i, (cid, _) in enumerate(sorted(scored.items(), key=lambda kv: (-kv[1], kv[0])))
    }
    return [
        k.model_copy(update={"composite_rank": position.get(k.id)}) if k.kind == "candidate" else k
        for k in kids
    ]


def _build(stores: Stores, path: CyclePath, *, depth: int, reads: _Reads) -> LineageNode:
    leaf = path[-1]
    index = _read_index(stores, leaf)
    layout = _layout(stores, leaf)
    ledger_path = layout.ledger
    dash = read_json_optional(layout.dashboard)
    dash = dash if isinstance(dash, dict) else {}
    candidates = scan_ledger_candidates(ledger_path)
    children = _child_courses(stores, path, reads)
    inner = [c for c in children if c.inner]
    # Mint order IS the campaign's timeline, so it is what positions a fork on it.
    forks = sorted(
        (c for c in children if not c.inner), key=lambda c: (c.created_at, c.path[-1].cycle_id)
    )
    buckets = _bucket_by_parent(inner, candidates)
    hops = list(path)

    decided = _round_facts(ledger_path, candidates)

    # Forks resolve FIRST: a replayed origin grafts its runs onto the candidate it replays.
    by_id = {c.candidate_id: c for c in candidates}
    contributions: list[_Contribution] = []
    grafts: dict[str, list[LineageNode]] = {}
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

    kids = rank_by_composite(
        _fold_contributions(
            [
                _candidate_node(
                    cand,
                    close=decided.get(cand.candidate_id, _NO_ROUND_FACTS),
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
    )

    scalars = _course_scalars(stores, leaf, index, reads, dash)
    # A course whose line MOVED does not answer for run-state; the LAST such cut speaks, and
    # each branch delegates onward, so a chain resolves to its tip. `origin_accuracy` stays
    # OURS — round 0 is the shared prefix, not the cut.
    branch = next((c.course for c in reversed(contributions) if c.takes_the_line), None)
    if branch is not None:
        scalars |= {
            "run_phase": branch.run_phase,
            "status": branch.status,
            "best_accuracy": branch.best_accuracy,
        }

    edge_id, _ = _course_edge(index)
    return LineageNode(
        kind="course",
        id=leaf.cycle_id,
        parent_id=edge_id or (candidates[0].parent_id if candidates else None),
        label=leaf.cycle_id,
        # Nothing folds a course onto another timeline, so its two labels are one fact.
        course_label=leaf.cycle_id,
        path=hops,
        children=kids,
        **scalars,
    )


def build_lineage_tree(stores: Stores, path: CyclePath) -> LineageNode:
    """The course at *path* and its subtree, expanded to :data:`_MAX_COURSE_DEPTH`. Each level
    costs one ledger scan plus two small JSON reads per course."""
    store_at, _ = resolve_cycle_path(stores, path)
    return _build(store_at, path, depth=_MAX_COURSE_DEPTH, reads=_Reads())
