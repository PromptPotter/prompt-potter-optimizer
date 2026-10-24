from __future__ import annotations

from typing import Literal

from pydantic import Field

from promptpotter.domain.cycle_listing import RunStatus
from promptpotter.domain.cycle_paths import CycleHop
from promptpotter.domain.hierarchy import HierarchyCourse
from promptpotter.domain.opt_search_point import Variation
from promptpotter.domain.results import ArmReading, ArmVerdict, DisplayMetric
from promptpotter.domain.run_records import ForkDirection
from promptpotter.domain.strict_model import FrozenModel

CourseKind = Literal["root", "fork", "diag", "inner"]


class LineageDivergence(FrozenModel):
    """Where an alternative criterion would have elected someone else, on the node it describes."""

    alternative_candidate_id: str | None = Field(
        default=None,
        description="The candidate the masked criterion would have elected instead "
        "(measured, so nameable); null when the round would simply have held on origin.",
    )


class ForkStamp(FrozenModel):
    """What marks an attempt the operator cut: a fork is NOT a node, so its identity rides them."""

    kind: CourseKind
    trigger: str
    direction: ForkDirection | None = Field(
        description="Which side of the cut the run CONTINUES on (`FORK_TRAITS`): `offshoot` "
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


class LensShift(FrozenModel):
    """What the request's lens did to one course's sibling ordering, over the arms on its timeline."""

    top_composite: str | None = Field(
        description="The timeline label of the arm ranked first by composite; null where that "
        "arm is on the retired side of a supersede cut."
    )
    top_lens: str | None = Field(description="The same, ranked by `lens_value`.")
    top_changed: bool = Field(description="The lens ranks another arm first.")
    moved_up: int
    moved_down: int
    unchanged: int


class MainLineStep(FrozenModel):
    """One round on the main line to an arm."""

    round: int
    rows: list[int] = Field(
        description="The arms the round's election crowned, each by its `row`; at the head's "
        "round, the head. Empty: the election crowned nobody."
    )


class _Node(FrozenModel):
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


class ArmNode(_Node):
    """One arm on a course's timeline; everything measured or decided about it is its `reading`."""

    kind: Literal["candidate"] = "candidate"
    elects_on: DisplayMetric | None = Field(
        default=None,
        description="The column the selector elects on: the declaration its round's election "
        "carried (`Election.elects_on`), null on a round that never elected. `ability`: the "
        "rounds are won on theta, not on accuracy.",
    )
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
    children: list[CourseLineage] = Field(
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


class CourseLineage(_Node):
    """One run, whose children are the arms on its ONE timeline, forks folded in."""

    kind: Literal["course"] = "course"
    children: list[ArmNode] = Field(default_factory=list)
    course: HierarchyCourse = Field(
        description="The course as the hierarchy serves it, minted by the one course builder: a "
        "timeline a supersede folded onto its branch carries the branch's."
    )
    deletable_stubs: int = Field(
        description="How many cycles `cleanup-empty-cycles` addressed at this course deletes "
        "now: its forks that closed no round of their own, hold no producer and no descendant, "
        "and are not the active cycle (`CampaignStore.deletable_stubs`, the command's own list). "
        "A fork is no node of this tree, so no count over its nodes can stand in for it.",
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
