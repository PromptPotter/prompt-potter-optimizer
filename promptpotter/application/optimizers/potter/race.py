from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from promptpotter.application.bench.node_context import (
    Bounds,
    Frontier,
    Lineage,
    NodeContext,
    Progress,
    Replay,
    Ruler,
)
from promptpotter.application.bench.resume_and_fork.replayers import ReplayContext, Replayed
from promptpotter.application.optimizers.nodes import Panel
from promptpotter.application.optimizers.potter.knobs import PoBBKnobs
from promptpotter.application.optimizers.potter.pobb import (
    EliminationContext,
    PoBBCheck,
    PoBBStop,
    field_p_best,
    graded_sheet,
    race_sheet,
    wire_sheet,
)
from promptpotter.domain.arms import Elimination
from promptpotter.domain.paired_reading import ArmKey
from promptpotter.domain.scoring import NO_CELLS, CellSheet, GradedCell
from promptpotter.domain.validators import StopSignal


class _RaceDecision(Replayed, kind=None):
    arm: ArmKey
    parent: ArmKey
    queries_scored: int
    p_best: float
    leader: ArmKey
    paired_breakdown: dict[ArmKey, float]
    # The cells the field was read on by `Sample.key`, each rival's grades as read: a replay re-draws from these.
    candidate_cells: list[str]
    prior_histories: dict[ArmKey, dict[str, dict[str, float | None]]]

    def _read(self, ctx: ReplayContext) -> tuple[CellSheet, dict[ArmKey, CellSheet]] | None:
        """``None`` where a cell the live check read is no longer among the arm's rescored rows."""
        # A cut in the top-up read the arm's origin-panel rows too.
        rows = ctx.round_data.raced(self.arm).by_key()

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
        sheet, rivals = read
        p_best, _ = field_p_best({**rivals, self.arm: sheet}, self.candidate_cells)
        return self.arm in p_best and p_best[self.arm] < self.epsilon


def _reason(ctx: EliminationContext, labels: Mapping[ArmKey, str]) -> str:
    q = f"q{ctx['queries_scored']}/{ctx['total_queries']}"
    n = ctx["n_priors"]
    priors = f"(of {n} prior{'' if n == 1 else 's'})"
    return (
        f"eliminated {q}  p_best={ctx['p_best']:.1%} < eps={ctx['epsilon']:.0%}  "
        f"vs {labels[ctx['leader']]} {priors}"
    )


class PoBBRace:
    """A block race cut on the family-wise p_best (`PoBBCheck.check`) at each checkpoint the arms share;
    its top-up onto the origin panel is one more stretch of it (`runner/round.py::measure_population`)."""

    def __init__(self, ctx: NodeContext, panel: Panel) -> None:
        # The one prior no arm of this round labels; a cell it never answered is caught up, never borrowed.
        lineage = ctx.read(Lineage)
        parent = ArmKey(lineage.parent.id)
        self._progress, self._replay = ctx.read(Progress), ctx.read(Replay)
        self._check = PoBBCheck(
            ctx.knobs(PoBBKnobs),
            n_min=ctx.read(Bounds).elimination_n_min,
            n_samples=len(panel.cells),
            ruler=ctx.read(Ruler).difficulty.ruler,
            parent=parent,
            on_snapshot=self._progress.race_standing,
        )
        self._block_size = panel.block_size
        if parent_results := ctx.read(Frontier).parent_own_rows:
            self._check.register_completed(parent_results, key=parent)

    @property
    def n_priors(self) -> int:
        return len(self._check.priors)

    @property
    def priors(self) -> Mapping[ArmKey, CellSheet]:
        return self._check.priors

    def admit(self, key: ArmKey, results: Sequence[GradedCell]) -> None:
        self._check.register_completed(results, key=key)

    def catch_up(self, key: ArmKey, cells: Sequence[GradedCell]) -> None:
        self._check.extend_prior(key, cells)

    @property
    def blocks(self) -> PoBBRace:
        return self

    @property
    def top_up(self) -> PoBBRace:
        return self

    @property
    def block_size(self) -> int:
        return self._block_size

    def close(self, rows: Mapping[ArmKey, Sequence[GradedCell]]) -> dict[ArmKey, StopSignal]:
        """An admitted arm walking again is topped up: its rows so far lead the stretch."""
        standing = {
            key: self._check.priors.get(key, NO_CELLS).merged(race_sheet(walked))
            for key, walked in rows.items()
        }
        return dict(self._check.check({**self._check.priors, **standing}, standing))

    def open_turn(self, _key: ArmKey) -> None:
        return None

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
            "queries_scored": context["queries_scored"],
            "p_best": context["p_best"],
            "leader": context["leader"],
            "paired_breakdown": signal.paired_breakdown,
            "candidate_cells": signal.cells,
            "prior_histories": {pid: wire_sheet(prior) for pid, prior in signal.priors.items()},
        }
        self._replay.decide(EliminationCut(**raced, epsilon=context["epsilon"]), True)
        named = {**labels, self._check.parent: "parent"}
        return Elimination(reason=_reason(context, named), context=dict(context))
