from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from promptpotter.application.bench.node_context import (
    Bounds,
    Frontier,
    Lineage,
    Progress,
    Replay,
    Ruler,
)
from promptpotter.application.bench.resume_and_fork.replayers import ReplayContext, Replayed
from promptpotter.application.optimizers.potter.knobs import PoBBKnobs
from promptpotter.application.optimizers.potter.pobb import (
    EliminationContext,
    EliminationGate,
    PoBBCheck,
    PoBBStop,
    graded_sheet,
    locks_in,
    prior_reads,
    race_sheet,
    wire_sheet,
)
from promptpotter.application.scoring.selection import crowning_cells
from promptpotter.domain.arms import Elimination
from promptpotter.domain.paired_reading import ArmKey, PriorStanding
from promptpotter.domain.scoring import CellSheet

if TYPE_CHECKING:
    from promptpotter.application.bench.node_context import NodeContext
    from promptpotter.application.optimizers.nodes import Panel
    from promptpotter.application.scoring.query_loop import Walk
    from promptpotter.domain.sample import Sample
    from promptpotter.domain.scoring import GradedCell
    from promptpotter.domain.validators import StopSignal


class _RaceDecision(Replayed, kind=None):
    arm: ArmKey
    parent: ArmKey
    prior_arms: list[ArmKey]
    queries_scored: int
    # The fewest shared cells a prior is read on, which the replay's fit needs too.
    n_min: int
    p_best: float
    leader: ArmKey
    paired_breakdown: dict[ArmKey, PriorStanding]
    # Each cell by its `Sample.key`, each prior's grades as read: a replay re-fits from these.
    candidate_cells: list[str]
    crowned: list[str]
    prior_histories: dict[ArmKey, dict[str, dict[str, float | None]]]

    def _read(self, ctx: ReplayContext) -> tuple[CellSheet, dict[ArmKey, CellSheet]] | None:
        """``None`` where a cell the live check read is no longer among the arm's rescored rows."""
        rows = ctx.round_data.rows(self.arm).by_key()
        if any(cell not in rows for cell in self.candidate_cells):
            return None
        priors = {pid: graded_sheet(grades) for pid, grades in self.prior_histories.items()}
        return race_sheet(rows[cell] for cell in self.candidate_cells), priors


class EliminationCut(_RaceDecision, kind="elimination_cut"):
    epsilon: float

    def replay(self, ctx: ReplayContext) -> bool:
        read = self._read(ctx)
        if read is None:
            return False
        reads = prior_reads(*read, ctx.ruler, n_min=self.n_min)
        return bool(reads) and min(r.p_better for r in reads.values()) < self.epsilon


class LeaderLockIn(_RaceDecision, kind="leader_lock_in"):
    admission: float

    def replay(self, ctx: ReplayContext) -> bool:
        read = self._read(ctx)
        if read is None:
            return False
        return locks_in(
            self.arm,
            *read,
            self.parent,
            crowned=frozenset(self.crowned),
            n_min=self.n_min,
            ruler=ctx.ruler,
            admission=self.admission,
        )


def _reason(ctx: EliminationContext, labels: Mapping[ArmKey, str]) -> str:
    q = f"q{ctx['queries_scored']}/{ctx['total_queries']}"
    n = ctx["n_priors"]
    priors = f"(of {n} prior{'' if n == 1 else 's'})"
    if ctx["gate"] is EliminationGate.LOCK_IN:
        return (
            f"leader locked {q}  p_best={ctx['p_best']:.1%} >= admission={ctx['admission']:.0%}  "
            f"{priors}"
        )
    return (
        f"eliminated {q}  p_best={ctx['p_best']:.1%} < eps={ctx['epsilon']:.0%}  "
        f"vs {labels[ctx['leader']]} {priors}"
    )


@dataclass
class _PriorsAhead:
    """Candidates ahead that are still being walked may yet become priors, so the horizon allows for them."""

    pobb: PoBBCheck
    ahead: Sequence[tuple[ArmKey, Walk | None]]
    name: str = field(init=False)

    def __post_init__(self) -> None:
        self.name = self.pobb.name

    def check(self, results: Sequence[GradedCell]) -> StopSignal | None:
        return self.pobb.check(results)

    def earliest_stop(
        self,
        results: Sequence[GradedCell],
        upcoming: Sequence[tuple[Sample, GradedCell | None]],
    ) -> int | None:
        unresolved: dict[ArmKey, list[GradedCell]] = {
            pid: walk.rows()
            for pid, walk in self.ahead
            if walk is not None and walk.outcome is None
        }
        return self.pobb.earliest_stop(results, upcoming, unresolved=unresolved)


class PoBBRace:
    """The parent is the first prior: on an empty pool PoBB short-circuits and the round's first arm is uneliminable."""

    def __init__(self, ctx: NodeContext, panel: Panel) -> None:
        # The one prior no arm of this round labels; a cell it never answered is caught up, never borrowed.
        lineage = ctx.read(Lineage)
        parent = ArmKey(lineage.parent.id)
        self._check = PoBBCheck(
            ctx.knobs(PoBBKnobs),
            admission=ctx.read(Bounds).admission,
            n_min=ctx.read(Bounds).elimination_n_min,
            n_samples=len(panel.cells),
            ruler=ctx.read(Ruler).difficulty.ruler,
            parent=parent,
            # The lock-in asks the election, which reads no cell that crowned the parent.
            crowned=crowning_cells(lineage.rounds, lineage.parent.id),
        )
        self._progress, self._replay = ctx.read(Progress), ctx.read(Replay)
        if parent_results := ctx.read(Frontier).parent_own_rows:
            self._check.register_completed(parent_results, key=parent)

    @property
    def n_priors(self) -> int:
        return len(self._check.priors)

    @property
    def priors(self) -> Mapping[ArmKey, CellSheet]:
        return self._check.priors

    def admit(self, key: ArmKey, results: Sequence[GradedCell]) -> None:
        if key in self._check.priors:
            self._check.extend_prior(key, results)
        else:
            self._check.register_completed(results, key=key)

    @property
    def blocks(self) -> None:
        return None

    def rule(self, ahead: Sequence[tuple[ArmKey, Walk | None]]) -> _PriorsAhead:
        return _PriorsAhead(self._check, ahead)

    def open_turn(self, key: ArmKey) -> None:
        self._check.set_current(key, on_snapshot=self._progress.race_standing)

    def judge(
        self, signal: StopSignal | None, key: ArmKey, labels: Mapping[ArmKey, str]
    ) -> Elimination | None:
        """Read off the priors BEFORE the arm joins them: the pool the stop rule tested against."""
        if not isinstance(signal, PoBBStop):
            return None
        context = signal.check_result
        raced: dict[str, Any] = {
            "arm": key,
            "parent": self._check.parent,
            "prior_arms": list(self._check.priors),
            "queries_scored": context["queries_scored"],
            "n_min": self._check.n_min,
            "p_best": context["p_best"],
            "leader": context["leader"],
            "paired_breakdown": signal.paired_breakdown,
            "candidate_cells": signal.cells,
            "crowned": sorted(self._check.crowned),
            "prior_histories": {pid: wire_sheet(prior) for pid, prior in signal.priors.items()},
        }
        inputs = (
            LeaderLockIn(**raced, admission=context["admission"])
            if context["gate"] is EliminationGate.LOCK_IN
            else EliminationCut(**raced, epsilon=context["epsilon"])
        )
        self._replay.decide(inputs, True)
        named = {**labels, self._check.parent: "parent"}
        return Elimination(reason=_reason(context, named), context=dict(context))
