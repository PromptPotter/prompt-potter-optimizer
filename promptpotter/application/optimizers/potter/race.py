from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from promptpotter.application.bench.node_context import (
    Bounds,
    Lineage,
    NodeContext,
    Parent,
    Progress,
    Replay,
    Ruler,
)
from promptpotter.application.bench.resume_and_fork.replayers import ReplayContext, Replayed
from promptpotter.application.intelligence.rasch import (
    fit_theta,
    responses_binary,
    responses_of,
)
from promptpotter.application.optimizers.nodes import Race, RoundSamples
from promptpotter.application.optimizers.potter.knobs import PoBBKnobs
from promptpotter.application.optimizers.potter.pobb import (
    EliminationContext,
    PoBBCheck,
    PoBBStop,
    field_p_best,
    race_sheet,
)
from promptpotter.application.scoring.race_protocol import Decision, Trigger, View, WalkView
from promptpotter.application.scoring.row_policy import RowGround, send_rows
from promptpotter.domain.arms import Elimination
from promptpotter.domain.paired_reading import ArmKey
from promptpotter.domain.phase_acts import RowsRan
from promptpotter.domain.scoring import NO_CELLS, CellSheet, GradedCell
from promptpotter.domain.validators import StopSignal


class EliminationCut(Replayed, kind="elimination_cut"):
    arm: ArmKey
    parent: ArmKey
    queries_scored: int
    p_best: float
    leader: ArmKey
    paired_breakdown: dict[ArmKey, float]
    epsilon: float
    # Each side's cells the field was read on by `Sample.key`, the arm's and every rival's: the
    # round banks every side (the parent re-read on its cells), so a replay re-reads them all.
    cells: dict[ArmKey, list[str]]

    def replay(self, ctx: ReplayContext) -> bool:
        """No cut where a cell the live check read is no longer among its side's rescored rows."""
        field: dict[ArmKey, CellSheet] = {}
        for side, cells in self.cells.items():
            # A cut in the top-up read the origin-panel rows too.
            rows = ctx.round_data.raced(side).by_key()
            if any(cell not in rows for cell in cells):
                return False
            field[side] = race_sheet(rows[cell] for cell in cells)
        read = dict.fromkeys(cell for cells in self.cells.values() for cell in cells)
        p_best, _, _ = field_p_best(field, read)
        return self.arm in p_best and p_best[self.arm] < self.epsilon


def _reason(ctx: EliminationContext, labels: Mapping[ArmKey, str]) -> str:
    q = f"q{ctx['queries_scored']}/{ctx['total_queries']}"
    n = ctx["n_priors"]
    priors = f"(of {n} prior{'' if n == 1 else 's'})"
    return (
        f"eliminated {q}  p_best={ctx['p_best']:.1%} < eps={ctx['epsilon']:.0%}  "
        f"vs {labels[ctx['leader']]} {priors}"
    )


class PoBBRace(Race):
    """A block race cut on the family-wise p_best (`PoBBCheck.check`) at each checkpoint the arms share;
    its top-up onto the origin panel is one more stretch of it (`runner/round.py::measure_population`)."""

    def __init__(self, ctx: NodeContext, panel: RoundSamples) -> None:
        # The one prior no arm of this round labels; a cell it never answered is caught up, never borrowed.
        lineage = ctx.read(Lineage)
        parent = ArmKey(lineage.parent.id)
        self._progress, self._replay = ctx.read(Progress), ctx.read(Replay)
        knobs = ctx.knobs(PoBBKnobs)
        ruler = ctx.read(Ruler).difficulty.ruler
        self._check = PoBBCheck(
            knobs,
            n_min=ctx.read(Bounds).elimination_n_min,
            ruler=ruler,
            parent=parent,
            on_snapshot=self._progress.race_standing,
        )
        self._block_size = panel.block_size
        self._knobs = knobs
        own_rows = ctx.read(Parent).own_rows
        # The round's θ: the parent's on the ruler seated at its start; its centre where it reads none.
        seated = (
            None
            if ruler is None
            else fit_theta(
                responses_of(own_rows), ruler.entries(), binary=responses_binary(own_rows)
            )
        )
        self._round_theta = 0.0 if seated is None else seated[0]
        if own_rows:
            self._check.register_completed(own_rows, key=parent)

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
    def top_up(self) -> PoBBRace:
        return self

    @property
    def trigger(self) -> Trigger:
        return self._knobs.trigger

    @property
    def rows_ran(self) -> RowsRan:
        """A cold ruler tells every row alike, so ``informative`` there runs the sampler's order."""
        if self._knobs.row_choice == "random":
            return "random"
        return "cold" if self._check.ruler is None else "informative"

    def decide(self, view: View[ArmKey]) -> Decision[ArmKey]:
        stops = {} if view.opening else self._cut(view.walks)
        ground = RowGround(
            self._check.ruler,
            self._round_theta,
            self._check.n_min if self._check.epsilon_stop else None,
            self._block_size,
            {key: self._depth(key) for key in self._check.priors},
        )
        return send_rows(view, stops, self._check.read, self._knobs, ground)

    def _depth(self, key: ArmKey) -> int:
        """The rows a walk stands on before its own: a top-up's, the prior's it extends."""
        return len(self._check.priors.get(key, NO_CELLS).cells)

    def _cut(self, walks: Mapping[ArmKey, WalkView]) -> dict[ArmKey, StopSignal]:
        """An admitted arm walking again is topped up: its rows so far lead the stretch."""
        standing = {
            key: self._check.priors.get(key, NO_CELLS).merged(race_sheet(walk.taken))
            for key, walk in walks.items()
        }
        budgets = {key: self._depth(key) + walk.budget for key, walk in walks.items()}
        return dict(self._check.check({**self._check.priors, **standing}, budgets))

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
            "epsilon": context["epsilon"],
            "cells": signal.cells,
        }
        self._replay.decide(EliminationCut(**raced), True)
        named = {**labels, self._check.parent: "parent"}
        return Elimination(reason=_reason(context, named), context=dict(context))
