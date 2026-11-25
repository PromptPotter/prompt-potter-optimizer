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
    most_informative,
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
    graded_sheet,
    race_sheet,
    wire_sheet,
)
from promptpotter.domain.arms import Elimination
from promptpotter.domain.paired_reading import ArmKey
from promptpotter.domain.sample import Sample
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
        p_best, _, _ = field_p_best({**rivals, self.arm: sheet}, self.candidate_cells)
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
            # The budget: a walk takes as many rows as its draw holds, from wherever the race chose them.
            n_samples=len(panel.order),
            ruler=ruler,
            parent=parent,
            on_snapshot=self._progress.race_standing,
        )
        self._block_size = panel.block_size
        self._row_choice, self._refit = knobs.row_choice, knobs.refit
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
    def blocks(self) -> PoBBRace:
        return self

    @property
    def top_up(self) -> PoBBRace:
        return self

    @property
    def block_size(self) -> int:
        return self._block_size

    def choose_rows(
        self,
        _taken: Mapping[ArmKey, Sequence[GradedCell]],
        ahead: Mapping[ArmKey, Sequence[Sample]],
    ) -> Mapping[ArmKey, Sequence[Sample]]:
        """``informative``: every draw walk its rows most informative at one set of θ, so the
        survivors answer the same next rows. An admitted arm walking again is topped up on its own
        panel gaps and keeps their order."""
        if self._row_choice == "random":
            return ahead
        drawn = [key for key in ahead if key not in self._check.priors]
        refit = self._check.refit
        thetas = (
            [refit[key] for key in drawn if key in refit] if self._refit == "per_decision" else []
        ) or [self._round_theta]
        ruler = self._check.ruler
        return {
            key: most_informative(rows, thetas, ruler) if key in drawn else rows
            for key, rows in ahead.items()
        }

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
