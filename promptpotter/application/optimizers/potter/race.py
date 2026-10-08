"""PoBB as the measurement drives it — potter's eliminator: the round's prior pool seeded with the
parent, the catch-up calls pairing each prior with the arm on turn, the stop rule, the ledger decision."""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from functools import partial
from typing import TYPE_CHECKING, Any, cast

from promptpotter.application.bench.resume_and_fork.decisions import record_decision
from promptpotter.application.optimizers.nodes import EliminationReading
from promptpotter.application.optimizers.potter.knobs import PoBBKnobs
from promptpotter.application.optimizers.potter.pobb.checks import (
    EliminationContext,
    EliminationGate,
    PoBBCheck,
    PoBBStop,
)
from promptpotter.application.optimizers.potter.records import PotterCheckpointKind

if TYPE_CHECKING:
    import asyncio

    from promptpotter.application.optimizers.nodes import (
        CatchUp,
        CatchUpFn,
        Panel,
        RoundContext,
    )
    from promptpotter.application.scoring.query_loop import Walk
    from promptpotter.domain.connector import MeasuredUnit
    from promptpotter.domain.sample import Sample
    from promptpotter.domain.scoring import QueryMeasurement
    from promptpotter.domain.search_point import JobSearchPoint
    from promptpotter.domain.validators import StopSignal

__all__ = ["CatchUpPool", "PoBBRace"]


def _reason(ctx: EliminationContext, unit: MeasuredUnit) -> str:
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
        f"vs {ctx['leader_label']} {priors}"
    )


@dataclass
class _PriorsAhead:
    """The round's PoBB check as one candidate's walk reads it: the candidates before it that are
    still being walked may yet become priors, so its horizon allows for them, as far as their
    returned cells say."""

    pobb: PoBBCheck
    ahead: Sequence[tuple[str, Walk | None]]
    name: str = field(init=False)

    def __post_init__(self) -> None:
        self.name = self.pobb.name

    def check(self, results: list[QueryMeasurement]) -> StopSignal | None:
        return self.pobb.check(results)

    def earliest_stop(
        self,
        results: list[QueryMeasurement],
        upcoming: Sequence[tuple[Sample, QueryMeasurement | None]],
    ) -> int | None:
        unresolved = {
            pid: walk.rows()
            for pid, walk in self.ahead
            if walk is not None and walk.outcome is None
        }
        return self.pobb.earliest_stop(results, upcoming, unresolved=unresolved)


class CatchUpPool:
    """The priors of one round and the calls that catch each up on a cell it lacks, so every
    comparison the stop rule makes is on identical cells. One measurement per (prior, cell)."""

    def __init__(self, check: PoBBCheck, measure: CatchUpFn) -> None:
        self._check = check
        self._measure = measure
        self._sps: dict[str, JobSearchPoint] = {}
        self._pending: dict[tuple[str, int], CatchUp] = {}
        self._on_catch_up: Callable[[int, list[str]], None] | None = None

    def admit(self, candidate_id: str, results: list[QueryMeasurement], sp: JobSearchPoint) -> None:
        self._check.register_completed(results, candidate_id=candidate_id)
        self._sps[candidate_id] = sp

    def _unstarted(self, sample: Sample) -> list[str]:
        return [
            cid
            for cid, held in self._check.priors_by_sample.items()
            if str(sample.id) not in held and (cid, sample.id) not in self._pending
        ]

    def start_backfill(self, sample: Sample, room: int) -> list[asyncio.Future[Any]]:
        """Start measuring up to ``room`` of the priors that lack ``sample``, so the calls overlap
        the cell's own. Nothing is written or graded until :meth:`commit_backfills` takes them."""
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
        """The catch-up calls started for ``sample`` and not yet committed."""
        return [call for (_, sid), (call, *_) in self._pending.items() if sid == sample.id]

    def commit_backfills(self, sample: Sample) -> None:
        """Take every started catch-up on ``sample``, in prior order — the moment a serial round
        would have measured them, so what reaches disk, and in what order, is the serial round's."""
        fresh: list[str] = []
        for cid, held in self._check.priors_by_sample.items():
            backfill = self._pending.pop((cid, sample.id), None)
            if backfill is None:
                continue
            self._check.extend_prior(cid, backfill[1]())
            if str(sample.id) in held:
                fresh.append(cid)
        if fresh and self._on_catch_up is not None:
            self._on_catch_up(sample.id, fresh)

    def bank_backfills(self, samples: Sequence[Sample]) -> None:
        """Write, ungraded, the catch-ups back for *samples* — cells a stopped round's walks were
        sure to take, so the resumed round replays these rather than paying for them again."""
        wanted = {s.id for s in samples}
        for key, (call, commit, _) in list(self._pending.items()):
            landed = call.done() and not call.cancelled() and call.exception() is None
            if key[1] in wanted and landed:
                del self._pending[key]
                commit()

    def discard_backfills(self) -> None:
        """Drop every measurement no walk took — paid, and never written, as a serial round would
        never have made it — save what :meth:`bank_backfills` kept."""
        for _call, _commit, discard in self._pending.values():
            discard()
        self._pending.clear()


class PoBBRace(CatchUpPool):
    """One round's PoBB. The parent is its first prior, so candidate #1 has a comparator —
    without one PoBB short-circuits on an empty pool and the round's first arm is uneliminable."""

    def __init__(self, ctx: RoundContext, panel: Panel, catch_up: CatchUpFn, *, node: str) -> None:
        cycle = ctx.cycle
        super().__init__(
            PoBBCheck(
                cast("PoBBKnobs", cycle.optimizer.knobs("pobb")),
                n_min=cycle.config.optimization.elimination_n_min,
                n_samples=len(panel.cells),
                ruler=cycle.difficulty.ruler,
            ),
            catch_up,
        )
        self._ctx = ctx
        self._node = node
        # The one prior no arm of this round labels, so its label is the race's to hold.
        self._parent_label: dict[str, str] = {}
        # `current_results` = best-so-far per-sample history; `current_sp` is the leader,
        # backfill-able on the candidate's hard samples.
        parent_results = cycle.tracking.current_results
        parent_sp = cycle.tracking.current_sp
        if parent_results and parent_sp is not None:
            # Named for the round that ELECTED it: a held round keeps the parent it had.
            elected_in = next((rr.round for rr in reversed(cycle.rounds) if rr.improved), 0)
            parent = f"R{elected_in}_winner"
            self._parent_label[parent] = parent
            self.admit(parent, cast("list[QueryMeasurement]", parent_results), parent_sp)

    @property
    def n_priors(self) -> int:
        return len(self._check.priors_by_sample)

    @property
    def blocks(self) -> None:
        return None

    def rule(self, ahead: Sequence[tuple[str, Walk | None]]) -> _PriorsAhead:
        return _PriorsAhead(self._check, ahead)

    def open_turn(self, candidate_id: str, idx: int, n: int) -> None:
        # Binds the per-sample snapshot so it rides the telemetry stream tagged with this arm.
        callbacks, round_num = self._ctx.callbacks, self._ctx.round_num
        self._check.set_current(
            candidate_id,
            on_snapshot=partial(callbacks.on_race_standing, self._node, round_num, idx, n),
        )
        self._on_catch_up = partial(callbacks.on_race_catch_up, self._node, round_num, idx, n)

    def judge(
        self,
        signal: StopSignal | None,
        *,
        candidate_id: str,
        results: list[QueryMeasurement],
        labels: dict[str, str],
    ) -> EliminationReading | None:
        """This arm's elimination context, and the decision its cut or lock-in leaves — read off
        the priors BEFORE the arm joins them, which is the pool the stop rule tested against."""
        if not isinstance(signal, PoBBStop):
            return None
        context, fit = signal.check_result, signal.fit
        gate = context["gate"]
        round_num = self._ctx.round_num
        inputs: dict[str, Any] = {
            "candidate_id": candidate_id,
            "prior_candidate_ids": self._check.prior_ids,
            "queries_scored": context["queries_scored"],
            "round_num": round_num,
        }
        data: dict[str, Any] = {}
        if fit is not None:
            context = {
                **context,
                "leader_label": {**self._parent_label, **labels}[context["leader_id"]],
            }
            inputs["recorded_p_best"] = context["p_best"]
            # The cells the posterior was fit on and each paired prior's grade on exactly those:
            # a replay re-fits from these, with no cross-round crawl and no catch-up of its own.
            data = {
                "p_best": context["p_best"],
                "leader_id": context["leader_id"],
                "paired_breakdown": signal.paired_breakdown,
                "candidate_sample_ids": fit.cells,
                "prior_histories": {
                    pid: dict(zip(fit.cells, grades, strict=True))
                    for pid, grades in fit.priors.items()
                },
            }
        check = self._check
        if gate is EliminationGate.LOCK_IN:
            kind = PotterCheckpointKind.LEADER_LOCK_IN
            inputs |= {"lock_in": float(check.lock_in), "lock_in_n_min": int(check.lock_in_n_min)}
        else:
            kind = PotterCheckpointKind.ELIMINATION_CUT
            # WHICH gate cut it, so the replayer re-derives the rule that fired.
            inputs |= {"gate": gate, "n_min": int(check.n_min)}
            if gate is EliminationGate.EPSILON:
                inputs["epsilon"] = context["epsilon"]
        record_decision(
            self._ctx.cycle.pending_decisions,
            kind,
            inputs,
            True,
            node=self._node,
            data=data,
            round=round_num,
        )
        unit = self._ctx.cycle.session.backend_client.measured_unit
        return EliminationReading(reason=_reason(context, unit), context=context)
