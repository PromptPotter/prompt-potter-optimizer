"""PoBB as the measurement drives it — potter's eliminator: the round's prior pool, seeded with the
parent, the stop rule each walk reads, and the ledger decision each cut or lock-in leaves."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from functools import partial
from typing import TYPE_CHECKING, Any, cast

from promptpotter.application.bench.resume_and_fork.decisions import record_decision
from promptpotter.application.optimizers.potter.knobs import PoBBKnobs
from promptpotter.application.optimizers.potter.pobb.checks import (
    EliminationContext,
    EliminationGate,
    PoBBCheck,
)
from promptpotter.application.optimizers.potter.records import PotterCheckpointKind
from promptpotter.domain.results import ArmOutcome

if TYPE_CHECKING:
    import asyncio

    from promptpotter.application.optimizers.nodes import CatchUpFn, Panel, RoundContext
    from promptpotter.application.scoring.query_loop import Walk
    from promptpotter.domain.sample import Sample
    from promptpotter.domain.scoring import QueryMeasurement
    from promptpotter.domain.search_point import JobSearchPoint
    from promptpotter.domain.validators import StopSignal

__all__ = ["PoBBRace", "pobb_decision_data"]


def pobb_decision_data(
    candidate_score: dict[str, Any],
    *,
    candidate_sample_ids: list[str] | None = None,
    prior_histories: dict[str, dict[str, float]] | None = None,
) -> dict[str, Any]:
    """Archival data for PoBB decisions — the per-prior per-sample GRADED responses ARE the snapshot at decision time,
    so replay re-fits θ from exactly these without crawling prior rounds."""
    return {
        "p_best": float(candidate_score.get("p_best", 0.0)),
        "leader_id": str(candidate_score.get("leader_id", "")),
        "paired_breakdown": dict(candidate_score.get("paired_breakdown") or {}),
        "candidate_sample_ids": list(candidate_sample_ids or []),
        "prior_histories": dict(prior_histories or {}),
    }


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


class PoBBRace:
    """One round's PoBB. The parent is its first prior, so candidate #1 has a comparator —
    without one PoBB short-circuits on an empty pool and the round's first arm is uneliminable."""

    def __init__(self, ctx: RoundContext, panel: Panel, catch_up: CatchUpFn, *, node: str) -> None:
        cycle = ctx.cycle
        self._ctx = ctx
        self._node = node
        self._check = PoBBCheck(
            cast("PoBBKnobs", cycle.optimizer.knobs("pobb")),
            n_min=cycle.config.optimization.elimination_n_min,
            n_samples=len(panel.cells),
            ruler=cycle.ruler,
            backfill_fn=catch_up,
        )
        # `current_results` = best-so-far per-sample history; `current_sp` is the leader,
        # backfill-able on the candidate's hard samples.
        parent_results = cycle.tracking.current_results
        parent_sp = cycle.tracking.current_sp
        if parent_results and parent_sp is not None:
            # Named for the round that ELECTED it: a held round keeps the parent it had.
            elected_in = next((rr.round for rr in reversed(cycle.rounds) if rr.improved), 0)
            self._check.register_completed(
                cast("list[QueryMeasurement]", parent_results),
                candidate_id=f"R{elected_in}_winner",
                sp=parent_sp,
            )

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
            on_backfill=partial(callbacks.on_race_catch_up, self._node, round_num, idx, n),
        )

    def judge(
        self,
        signal: StopSignal | None,
        *,
        candidate_id: str,
        results: list[QueryMeasurement],
        labels: dict[str, str],
    ) -> EliminationContext | None:
        """This arm's elimination context, and the decision its cut or lock-in leaves — read off
        the priors BEFORE the arm joins them, which is the pool the stop rule tested against."""
        if signal is None:
            return None
        check = self._check
        cr = signal.check_result
        if signal.check_name != check.name:
            return None
        priors_at_test = list(check.prior_ids)
        elim_ctx: EliminationContext | None = None
        if signal.outcome in (ArmOutcome.ELIMINATED, ArmOutcome.LOCKED_IN):
            leader_id = str(cr.get("leader_id", ""))
            gate = EliminationGate(cr["gate"])
            elim_ctx = {"gate": gate, "p_best": float(cr.get("p_best", 0.0))}
            if gate is EliminationGate.EPSILON:
                elim_ctx["epsilon"] = float(cr["epsilon"])
            elim_ctx["leader_id"] = leader_id
            elim_ctx["queries_scored"] = int(cr.get("queries_scored", len(results)))
            elim_ctx["total_queries"] = int(cr.get("total_samples", check.n_samples))
            elim_ctx["n_priors"] = int(cr.get("n_priors", 0))
            # Seeded priors (``R{N}_winner``) carry operator-readable ids; this round's resolve
            # through the labels already scored (``C2.3``).
            if leader_id in priors_at_test:
                leader_label = labels.get(leader_id) or (
                    leader_id if leader_id.startswith("R") and leader_id.endswith("_winner") else ""
                )
                if leader_label:
                    elim_ctx["leader_label"] = leader_label

        queries_scored = int(cr.get("queries_scored", len(results)))
        recorded_p_best = float(cr.get("p_best", 0.0))
        # The sample IDs in play at decision time + each prior's grade on exactly those samples:
        # replay reads these directly, with no cross-round crawl and no backfill replay.
        candidate_sample_ids = [
            str(r.get("sample_id", ""))
            for r in results[:queries_scored]
            if r.get("sample_id") is not None
        ]
        prior_histories = check.snapshot_priors(candidate_sample_ids)
        decisions = self._ctx.cycle.pending_decisions
        round_num = self._ctx.round_num
        if signal.outcome is ArmOutcome.ELIMINATED:
            record_decision(
                decisions,
                PotterCheckpointKind.ELIMINATION_CUT,
                {
                    "candidate_id": candidate_id,
                    # WHICH gate cut it, so the replayer re-derives the rule that fired. Only ε
                    # computed a posterior, so `epsilon`/`recorded_p_best` mean something only there.
                    "gate": cr["gate"],
                    "prior_candidate_ids": priors_at_test,
                    "queries_scored": queries_scored,
                    "epsilon": float(cr.get("epsilon", check.epsilon)),
                    "n_min": int(check.n_min),
                    "round_num": round_num,
                    "recorded_p_best": recorded_p_best,
                },
                True,
                node=self._node,
                data=pobb_decision_data(
                    cr,
                    candidate_sample_ids=candidate_sample_ids,
                    prior_histories=prior_histories,
                ),
                round=round_num,
            )
        if signal.outcome is ArmOutcome.LOCKED_IN:
            record_decision(
                decisions,
                PotterCheckpointKind.LEADER_LOCK_IN,
                {
                    "candidate_id": candidate_id,
                    "prior_candidate_ids": priors_at_test,
                    "queries_scored": queries_scored,
                    "lock_in": float(check.lock_in),
                    "lock_in_n_min": int(check.lock_in_n_min),
                    "round_num": round_num,
                    "recorded_p_best": recorded_p_best,
                },
                True,
                node=self._node,
                data=pobb_decision_data(
                    cr,
                    candidate_sample_ids=candidate_sample_ids,
                    prior_histories=prior_histories,
                ),
                round=round_num,
            )
        return elim_ctx

    def admit(self, candidate_id: str, results: list[QueryMeasurement], sp: JobSearchPoint) -> None:
        self._check.register_completed(results, candidate_id=candidate_id, sp=sp)

    def start_backfill(self, sample: Sample, room: int) -> list[asyncio.Future[Any]]:
        return self._check.start_backfill(sample, room)

    def owed_backfills(self, sample: Sample) -> int:
        return self._check.owed_backfills(sample)

    def backfills_in_flight(self) -> list[asyncio.Future[Any]]:
        return self._check.backfills_in_flight()

    def backfills_for(self, sample: Sample) -> list[asyncio.Future[Any]]:
        return self._check.backfills_for(sample)

    def commit_backfills(self, sample: Sample) -> None:
        self._check.commit_backfills(sample)

    def bank_backfills(self, samples: Sequence[Sample]) -> None:
        self._check.bank_backfills(samples)

    def discard_backfills(self) -> None:
        self._check.discard_backfills()
