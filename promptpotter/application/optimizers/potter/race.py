from __future__ import annotations

from collections.abc import Mapping, Sequence
from statistics import NormalDist
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
    expected_information,
    fit_theta,
    most_informative,
    responses_binary,
    responses_of,
)
from promptpotter.application.optimizers.nodes import Race, RoundSamples
from promptpotter.application.optimizers.potter.knobs import PoBBKnobs
from promptpotter.application.optimizers.potter.pobb import (
    EliminationContext,
    FieldRead,
    PoBBCheck,
    PoBBStop,
    field_p_best,
    graded_sheet,
    race_sheet,
    wire_sheet,
)
from promptpotter.domain.arms import Elimination
from promptpotter.domain.paired_reading import ArmKey
from promptpotter.domain.phase_acts import RowsRan
from promptpotter.domain.ruler import RulerEntry
from promptpotter.domain.sample import Sample, Stretch
from promptpotter.domain.scoring import NO_CELLS, CellSheet, GradedCell
from promptpotter.domain.validators import StopSignal


class _RaceDecision(Replayed, kind=None):
    arm: ArmKey
    parent: ArmKey
    queries_scored: int
    p_best: float
    leader: ArmKey
    paired_breakdown: dict[ArmKey, float]
    # The arm's own cells the field was read on by `Sample.key`, and each rival's grades on the
    # cells read: their union is what the field was read on, so a replay re-draws from these.
    arm_cells: list[str]
    prior_histories: dict[ArmKey, dict[str, dict[str, float | None]]]

    @property
    def cells(self) -> list[str]:
        return list(
            dict.fromkeys([*self.arm_cells, *(k for g in self.prior_histories.values() for k in g)])
        )

    def _read(self, ctx: ReplayContext) -> tuple[CellSheet, dict[ArmKey, CellSheet]] | None:
        """``None`` where a cell the live check read is no longer among the arm's rescored rows."""
        # A cut in the top-up read the arm's origin-panel rows too.
        rows = ctx.round_data.raced(self.arm).by_key()

        if any(cell not in rows for cell in self.arm_cells):
            return None
        priors = {pid: graded_sheet(grades) for pid, grades in self.prior_histories.items()}
        return race_sheet(rows[cell] for cell in self.arm_cells), priors


class EliminationCut(_RaceDecision, kind="elimination_cut"):
    epsilon: float

    def replay(self, ctx: ReplayContext) -> bool:
        read = self._read(ctx)
        if read is None:
            return False
        sheet, rivals = read
        p_best, _, _ = field_p_best({**rivals, self.arm: sheet}, self.cells)
        return self.arm in p_best and p_best[self.arm] < self.epsilon


def _reason(ctx: EliminationContext, labels: Mapping[ArmKey, str]) -> str:
    q = f"q{ctx['queries_scored']}/{ctx['total_queries']}"
    n = ctx["n_priors"]
    priors = f"(of {n} prior{'' if n == 1 else 's'})"
    return (
        f"eliminated {q}  p_best={ctx['p_best']:.1%} < eps={ctx['epsilon']:.0%}  "
        f"vs {labels[ctx['leader']]} {priors}"
    )


def _hand_out(
    walks: Mapping[ArmKey, tuple[float, Sequence[Sample]]],
    read: FieldRead | None,
    calls: int,
    ruler: Mapping[str, RulerEntry] | None,
) -> dict[ArmKey, int]:
    """How many of a block's *calls* each walk answers, off the rows it would take next at its θ.
    Each goes to the walk whose next answer tells the cut most: the Fisher information it carries
    about its θ, times the posterior variance it then removes (``variance²``), times how near
    the walk's P(best) sits to its ε(n) on the probit scale (`candidate-elimination.md`
    § Who answers a block's calls). A walk's variance shrinks with each call handed it; a tie
    goes to the walk handed fewer, then to walk order. Before PoBB first fits, every walk's
    variance and nearness are 1: the information alone."""
    variance = {
        key: read.variance[key] if read is not None and key in read.variance else 1.0
        for key in walks
    }
    near = {key: _nearness(read, key) for key in walks}
    handed = dict.fromkeys(walks, 0)
    for _ in range(calls):
        best: tuple[float, int, ArmKey, float] | None = None
        for key, (theta, rows) in walks.items():
            if handed[key] >= len(rows):
                continue
            info = expected_information(theta, (rows[handed[key]].key,), ruler)
            told = (info * variance[key] ** 2 * near[key], -handed[key])
            if best is None or told > best[:2]:
                best = (*told, key, info)
        if best is None:
            break
        *_, key, info = best
        handed[key] += 1
        variance[key] = 1.0 / (1.0 / variance[key] + info)
    return handed


_PROBIT = NormalDist()


def _nearness(read: FieldRead | None, key: ArmKey) -> float:
    """The normal density at the probit gap between a walk's P(best) and its ε(n), over the
    density at no gap: 1 on the bar, and falling as the cut's decision on it settles."""
    if read is None or key not in read.p_best:
        return 1.0
    clip = 1e-4
    p = min(max(read.p_best[key], clip), 1.0 - clip)
    gap = _PROBIT.inv_cdf(p) - _PROBIT.inv_cdf(read.bar[key])
    return _PROBIT.pdf(gap) / _PROBIT.pdf(0.0)


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
        self._row_choice, self._refit, self._shape = knobs.row_choice, knobs.refit, knobs.shape
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

    @property
    def rows_ran(self) -> RowsRan:
        """A cold ruler tells every row alike, so ``informative`` there runs the sampler's order."""
        if self._row_choice == "random":
            return "random"
        return "cold" if self._check.ruler is None else "informative"

    def _theta(self, key: ArmKey) -> float:
        """The θ a walk's rows are chosen on: its own in PoBB's last fit under ``per_decision``,
        else the round's."""
        read = self._check.read
        if self._refit == "per_decision" and read is not None and key in read.theta:
            return read.theta[key]
        return self._round_theta

    def choose_rows(
        self,
        taken: Mapping[ArmKey, Sequence[GradedCell]],
        ahead: Mapping[ArmKey, Sequence[Sample]],
    ) -> Mapping[ArmKey, Stretch]:
        """``per_candidate``: every draw walk a block's rows, ``informative`` the most informative
        at one set of θ, so the survivors answer the same next rows. ``per_round``: each its own
        rows at its own θ, and the block's calls to the walks whose answers tell the cut most
        (`_hand_out`). An admitted arm walking again is topped up on its own panel gaps, a
        block's rows in their order."""
        drawn = [key for key in ahead if key not in self._check.priors]
        if self._shape == "per_round" and drawn:
            return self._per_round(taken, ahead, drawn)
        rows = dict(ahead)
        if self._row_choice == "informative":
            read = self._check.read
            thetas = (
                [read.theta[key] for key in drawn if key in read.theta]
                if self._refit == "per_decision" and read is not None
                else []
            ) or [self._round_theta]
            for key in drawn:
                rows[key] = most_informative(ahead[key], thetas, self._check.ruler)
        return {key: Stretch(order, self._block_size) for key, order in rows.items()}

    def _per_round(
        self,
        taken: Mapping[ArmKey, Sequence[GradedCell]],
        ahead: Mapping[ArmKey, Sequence[Sample]],
        drawn: Sequence[ArmKey],
    ) -> dict[ArmKey, Stretch]:
        ruler = self._check.ruler
        rows = {
            key: most_informative(ahead[key], [self._theta(key)], ruler)
            if self._row_choice == "informative"
            else ahead[key]
            for key in drawn
        }
        budget = {
            key: min(len(rows[key]), self._check.n_samples - len(taken[key])) for key in drawn
        }
        calls = _hand_out(
            {key: (self._theta(key), rows[key][: budget[key]]) for key in drawn},
            self._check.read,
            self._block_size * len(drawn),
            None if ruler is None else ruler.entries(),
        )
        return {
            key: Stretch(rows[key], calls[key]) if key in rows else Stretch(order, self._block_size)
            for key, order in ahead.items()
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
            "arm_cells": signal.cells,
            "prior_histories": {pid: wire_sheet(prior) for pid, prior in signal.priors.items()},
        }
        self._replay.decide(EliminationCut(**raced, epsilon=context["epsilon"]), True)
        named = {**labels, self._check.parent: "parent"}
        return Elimination(reason=_reason(context, named), context=dict(context))
