from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from promptpotter.application.bench.node_context import Bounds, Frontier, Lineage, Ruler
from promptpotter.application.optimizers.potter.pobb.checks import (
    EliminationContext,
    EliminationGate,
    PoBBCheck,
    PoBBStop,
)
from promptpotter.application.optimizers.potter.records import PotterCheckpointKind
from promptpotter.domain.paired_reading import ArmKey
from promptpotter.domain.results import Elimination
from promptpotter.domain.validators import CaughtUp

if TYPE_CHECKING:
    import asyncio

    from promptpotter.application.bench.node_context import NodeContext
    from promptpotter.application.optimizers.nodes import CatchUp, CatchUpFn, Panel
    from promptpotter.application.optimizers.potter.knobs import PoBBKnobs
    from promptpotter.application.scoring.query_loop import Walk
    from promptpotter.domain.connector import MeasuredUnit
    from promptpotter.domain.sample import Sample
    from promptpotter.domain.scoring import GradedCell
    from promptpotter.domain.search_point import JobSearchPoint
    from promptpotter.domain.validators import StopSignal


def _reason(ctx: EliminationContext, unit: MeasuredUnit, labels: Mapping[ArmKey, str]) -> str:
    q = f"q{ctx['queries_scored']}/{ctx['total_queries']}"
    if ctx["gate"] is EliminationGate.COLLAPSED:
        return (
            f"answer collapsed {q}  one label for every {unit} — no measurement of ability to score"
        )
    n = ctx["n_priors"]
    priors = f"(of {n} prior{'' if n == 1 else 's'})"
    if ctx["gate"] is EliminationGate.LOCK_IN:
        return f"leader locked {q}  p_best={ctx['p_best']:.1%} {priors}"
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


class CatchUpPool:
    """Every comparison the stop rule makes is on identical cells; one measurement per (prior, cell)."""

    def __init__(self, check: PoBBCheck, measure: CatchUpFn, member: str) -> None:
        self._check = check
        self._measure = measure
        self._member = member
        self._sps: dict[ArmKey, JobSearchPoint] = {}
        self._pending: dict[tuple[ArmKey, int], CatchUp] = {}

    def admit(self, key: ArmKey, results: Sequence[GradedCell], sp: JobSearchPoint) -> None:
        self._check.register_completed(results, key=key)
        self._sps[key] = sp

    def _unstarted(self, sample: Sample) -> list[ArmKey]:
        return [
            key
            for key, held in self._check.priors_by_sample.items()
            if sample.key not in held and (key, sample.id) not in self._pending
        ]

    def start_backfill(self, sample: Sample, room: int) -> list[asyncio.Future[Any]]:
        """Nothing is written or graded until :meth:`commit_backfills` takes them."""
        started: list[asyncio.Future[Any]] = []
        for cid in self._unstarted(sample)[: max(room, 0)]:
            backfill = self._measure(self._sps[cid], sample, cid)
            self._pending[(cid, sample.id)] = backfill
            started.append(backfill[0])
        return started

    def owed_backfills(self, sample: Sample) -> int:
        return len(self._unstarted(sample))

    def backfills_in_flight(self) -> list[asyncio.Future[Any]]:
        return [call for call, *_ in self._pending.values() if not call.done()]

    def backfills_for(self, sample: Sample) -> list[asyncio.Future[Any]]:
        return [call for (_, sid), (call, *_) in self._pending.items() if sid == sample.id]

    def commit_backfills(self, sample: Sample) -> CaughtUp | None:
        """In prior order, when a serial round would have measured them, so what reaches disk is the serial round's."""
        taken: dict[ArmKey, Sequence[GradedCell]] = {}
        for cid in self._check.priors_by_sample:
            backfill = self._pending.pop((cid, sample.id), None)
            if backfill is not None:
                taken[cid] = backfill[1]()
                self._check.extend_prior(cid, taken[cid])
        return CaughtUp(self._member, taken) if taken else None

    def bank_backfills(self, samples: Sequence[Sample]) -> CaughtUp | None:
        """Written UNGRADED: cells a stopped round's walks were sure to take, so the resume replays them unpaid."""
        wanted = {s.id for s in samples}
        taken: dict[ArmKey, list[GradedCell]] = {}
        for key, (call, commit, _) in list(self._pending.items()):
            landed = call.done() and not call.cancelled() and call.exception() is None
            if key[1] in wanted and landed:
                del self._pending[key]
                taken.setdefault(key[0], []).extend(commit())
        return CaughtUp(self._member, taken) if taken else None

    def discard_backfills(self) -> None:
        """Paid and never written: a serial round would never have made these."""
        for _call, _commit, discard in self._pending.values():
            discard()
        self._pending.clear()


class PoBBRace(CatchUpPool):
    """The parent is the first prior: on an empty pool PoBB short-circuits and the round's first arm is uneliminable."""

    def __init__(
        self,
        ctx: NodeContext[PoBBKnobs],
        panel: Panel,
        catch_up: CatchUpFn,
        *,
        measured_unit: MeasuredUnit,
    ) -> None:
        # The one prior no arm of this round labels; a cell it never answered is caught up, never borrowed.
        parent = ArmKey(ctx.read(Lineage).parent.id)
        super().__init__(
            PoBBCheck(
                ctx.knobs,
                n_min=ctx.read(Bounds).elimination_n_min,
                n_samples=len(panel.cells),
                ruler=ctx.read(Ruler).difficulty.ruler,
                parent=parent,
            ),
            catch_up,
            ctx.node,
        )
        self._ctx = ctx
        self._measured_unit = measured_unit
        frontier = ctx.read(Frontier)
        if parent_results := frontier.parent_own_rows:
            self._check.register_completed(parent_results, key=parent)
            self._sps[parent] = frontier.parent_point

    @property
    def n_priors(self) -> int:
        return len(self._check.priors_by_sample)

    @property
    def blocks(self) -> None:
        return None

    def rule(self, ahead: Sequence[tuple[ArmKey, Walk | None]]) -> _PriorsAhead:
        return _PriorsAhead(self._check, ahead)

    def open_turn(self, key: ArmKey) -> None:
        self._check.set_current(key, on_snapshot=self._ctx.race_standing)

    def judge(
        self,
        signal: StopSignal | None,
        *,
        key: ArmKey,
        results: Sequence[GradedCell],
        labels: Mapping[ArmKey, str],
    ) -> Elimination | None:
        """Read off the priors BEFORE the arm joins them: the pool the stop rule tested against."""
        if not isinstance(signal, PoBBStop):
            return None
        context, fit = signal.check_result, signal.fit
        gate = context["gate"]
        round_num = self._ctx.round_num
        inputs: dict[str, Any] = {
            "arm": key,
            "prior_arms": self._check.prior_keys,
            "queries_scored": context["queries_scored"],
            "round_num": round_num,
            # The fewest shared cells a prior is read on, which the replay's fit needs too.
            "n_min": int(self._check.n_min),
        }
        data: dict[str, Any] = {}
        if fit is not None:
            inputs["recorded_p_best"] = context["p_best"]
            # Each cell by its `Sample.key`: a replay re-fits from this JSON record.
            data = {
                "p_best": context["p_best"],
                "leader": context["leader"],
                "paired_breakdown": signal.paired_breakdown,
                "candidate_cells": fit.cells,
                "prior_histories": {pid: dict(grades) for pid, grades in fit.priors.items()},
            }
        check = self._check
        if gate is EliminationGate.LOCK_IN:
            kind = PotterCheckpointKind.LEADER_LOCK_IN
            inputs |= {"lock_in": float(check.lock_in), "lock_in_n_min": int(check.lock_in_n_min)}
        else:
            kind = PotterCheckpointKind.ELIMINATION_CUT
            # WHICH gate cut it, so the replayer re-derives the rule that fired.
            inputs["gate"] = gate
            if gate is EliminationGate.EPSILON:
                inputs["epsilon"] = context["epsilon"]
        self._ctx.decide(kind, inputs, True, data=data)
        named = {**labels, check.parent: "parent"}
        return Elimination(
            reason=_reason(context, self._measured_unit, named), context=dict(context)
        )
