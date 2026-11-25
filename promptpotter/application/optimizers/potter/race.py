from __future__ import annotations

import math
import sys
from collections.abc import Mapping, Sequence
from statistics import NormalDist
from typing import Any, NamedTuple

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
    race_sheet,
)
from promptpotter.domain.arms import Elimination
from promptpotter.domain.paired_reading import ArmKey
from promptpotter.domain.phase_acts import RowsRan
from promptpotter.domain.ruler import RulerEntry
from promptpotter.domain.sample import ReadOrder, Sample, Stretch, Untaken
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


class _Walking(NamedTuple):
    """A live walk as a decision hands it calls: the θ its rows are read at, the rows its budget
    still takes in the order it takes them, the first ``owed`` of them out, and its depth."""

    theta: float
    rows: Sequence[Sample]
    owed: int
    depth: int


def _hand_out(
    walks: Mapping[ArmKey, _Walking],
    read: FieldRead | None,
    calls: int | None,
    ruler: Mapping[str, RulerEntry] | None,
    n_min: int,
) -> dict[ArmKey, int]:
    """How many calls each walk answers, the calls it has out among them, off the rows it would
    take next at its θ. Each goes to the walk whose next answer tells the cut most: the Fisher
    information it carries about its θ, times the posterior variance it then removes
    (``variance²``), times how near the walk's P(best) sits to its ε(n) on the probit scale
    (`candidate-elimination.md` § Who answers a block's calls). A walk's variance shrinks with
    each call out or handed it; a tie goes to the walk handed fewer, then to walk order. Before
    PoBB first fits, every walk's variance and nearness are 1: the information alone. *calls*
    ``None`` is a ``rolling`` decision's: it hands while a walk's calls out cannot settle the cut
    on it (`_settles`), none where they all can, and each walk at most its budget."""

    def info(key: ArmKey, at: int) -> float:
        walk = walks[key]
        return expected_information(walk.theta, (walk.rows[at].key,), ruler)

    handed = {key: walk.owed for key, walk in walks.items()}
    informed = {key: sum(info(key, at) for at in range(walk.owed)) for key, walk in walks.items()}
    variance = {
        key: read.variance[key] if read is not None and key in read.variance else 1.0
        for key in walks
    }
    for key, told in informed.items():
        if told:
            variance[key] = 1.0 / (1.0 / variance[key] + told)
    near = {key: _nearness(read, key) for key in walks}
    while calls is None or sum(handed.values()) < calls:
        best: tuple[float, int, ArmKey, float] | None = None
        for key, walk in walks.items():
            if handed[key] >= len(walk.rows):
                continue
            if calls is None and _settles(read, key, walk.depth, handed[key], informed[key], n_min):
                continue
            gain = info(key, handed[key])
            rank = (gain * variance[key] ** 2 * near[key], -handed[key])
            if best is None or rank > best[:2]:
                best = (*rank, key, gain)
        if best is None:
            break
        *_, key, gain = best
        handed[key] += 1
        informed[key] += gain
        variance[key] = 1.0 / (1.0 / variance[key] + gain)
    return handed


def _settles(
    read: FieldRead | None, key: ArmKey, depth: int, out: int, informed: float, n_min: int
) -> bool:
    """Whether the *out* calls a walk has out can settle the cut's decision on it, so a decision
    sends it none: they reach the depth the cut first reads it at, and its probit distance z from
    the bar lies within one preposterior sd of where their information *informed* moves it,
    z ≤ √(v·I / (1 + v·I)) (`candidate-elimination.md` § How many calls a rolling decision sends)."""
    if out == 0 or depth + out < n_min:
        return False
    if read is not None and key in read.p_best and key not in read.bar:
        # The parent stands no bar: the cut never decides it, so its calls settle nothing.
        return False
    if read is None or key not in read.p_best:
        return True
    reach = read.variance[key] * informed
    return _gap(read, key) <= math.sqrt(reach / (1.0 + reach))


_PROBIT = NormalDist()


def _gap(read: FieldRead, key: ArmKey) -> float:
    """The probit distance z from a walk's P(best) down to its ε(n)."""
    clip = 1e-4
    p = min(max(read.p_best[key], clip), 1.0 - clip)
    return _PROBIT.inv_cdf(p) - _PROBIT.inv_cdf(read.bar[key])


def _nearness(read: FieldRead | None, key: ArmKey) -> float:
    """The normal density at the probit gap between a walk's P(best) and its ε(n), over the
    density at no gap: 1 on the bar, and falling as the cut's decision on it settles."""
    if read is None or key not in read.bar or key not in read.p_best:
        return 1.0
    return _PROBIT.pdf(_gap(read, key)) / _PROBIT.pdf(0.0)


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
        self._row_choice, self._refit, self._shape = knobs.row_choice, knobs.refit, knobs.shape
        self._rolling = None if knobs.dispatch == "batch" else knobs.read_order
        # Each walk's budget at the last decision: its rows, a top-up's the prior's it extends.
        self._budgets: dict[ArmKey, int] = {}
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
    def rolling(self) -> ReadOrder | None:
        return self._rolling

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
        ahead: Mapping[ArmKey, Untaken],
    ) -> Mapping[ArmKey, Stretch]:
        """``per_candidate``: every draw walk a block's rows, ``informative`` the most informative
        at one set of θ, so the survivors answer the same next rows. ``per_round``: each its own
        rows at its own θ, and the block's calls to the walks whose answers tell the cut most
        (`_hand_out`). An admitted arm walking again is topped up on its own panel gaps, a
        block's rows in their order. Under ``rolling`` each walk is sent the calls its decision
        warrants, its calls out keeping their seats."""
        priors = self._check.priors
        self._budgets = {
            key: len(priors.get(key, NO_CELLS).cells) + len(taken[key]) + pool.left
            for key, pool in ahead.items()
        }
        drawn = [key for key in ahead if key not in priors]
        rows = {key: list(pool.rows) for key, pool in ahead.items()}
        if self._row_choice == "informative":
            read = self._check.read
            shared = (
                [read.theta[key] for key in drawn if key in read.theta]
                if self._refit == "per_decision" and read is not None
                else []
            ) or [self._round_theta]
            for key in drawn:
                at = [self._theta(key)] if self._shape == "per_round" else shared
                out = ahead[key].owed
                rows[key] = [
                    *rows[key][:out],
                    *most_informative(rows[key][out:], at, self._check.ruler),
                ]
        calls: dict[ArmKey, int] = {}
        if self._rolling is not None:
            calls = self._hand_out(ahead, rows, None)
        elif self._shape == "per_round" and drawn:
            calls = self._hand_out(
                {key: ahead[key] for key in drawn}, rows, self._block_size * len(drawn)
            )
        return {key: Stretch(rows[key], calls.get(key, self._block_size)) for key in ahead}

    def _hand_out(
        self,
        ahead: Mapping[ArmKey, Untaken],
        rows: Mapping[ArmKey, Sequence[Sample]],
        calls: int | None,
    ) -> dict[ArmKey, int]:
        ruler = self._check.ruler
        return _hand_out(
            {
                key: _Walking(
                    self._theta(key),
                    rows[key][: pool.left],
                    pool.owed,
                    self._budgets[key] - pool.left,
                )
                for key, pool in ahead.items()
            },
            self._check.read,
            calls,
            None if ruler is None else ruler.entries(),
            # A cut switched off settles nothing.
            self._check.n_min if self._check.epsilon_stop else sys.maxsize,
        )

    def close(self, rows: Mapping[ArmKey, Sequence[GradedCell]]) -> dict[ArmKey, StopSignal]:
        """An admitted arm walking again is topped up: its rows so far lead the stretch."""
        standing = {
            key: self._check.priors.get(key, NO_CELLS).merged(race_sheet(walked))
            for key, walked in rows.items()
        }
        budgets = {key: self._budgets[key] for key in standing}
        return dict(self._check.check({**self._check.priors, **standing}, budgets))

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
            "epsilon": context["epsilon"],
            # The arm last, as its field was fit: the draws are seeded in that order.
            "cells": {
                **{pid: [c.key for c in prior.scoreable] for pid, prior in signal.priors.items()},
                key: signal.cells,
            },
        }
        self._replay.decide(EliminationCut(**raced), True)
        named = {**labels, self._check.parent: "parent"}
        return Elimination(reason=_reason(context, named), context=dict(context))
