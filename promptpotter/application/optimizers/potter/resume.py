"""Potter's half of resume and fork; the generic half, ``bench/resume_and_fork/``, reaches it only through ``OptimizerRuntime``."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from promptpotter.application.bench.node_context import NodeContext
from promptpotter.application.optimizers.nodes import RoundContext
from promptpotter.application.optimizers.potter.dispatch.facade import build_bundle, node_packages
from promptpotter.application.optimizers.potter.l1.critique import run_l1_critique
from promptpotter.application.optimizers.potter.pobb.checks import EliminationGate
from promptpotter.application.optimizers.potter.records import (
    PotterCheckpointKind,
    PotterRoundState,
)
from promptpotter.application.optimizers.potter.state import potter_state
from promptpotter.application.run_observers import RunCallbacks
from promptpotter.application.scoring.selection import (
    elect_round_winner,
    paired_p_best,
    recorded_parent,
)
from promptpotter.domain.scoring import NO_CELLS, is_answer_collapsed
from promptpotter.infrastructure.llm.telemetry import reset_current_round, set_current_round
from promptpotter.shared.errors import graceful

if TYPE_CHECKING:
    from promptpotter.application.bench.cycle import Cycle
    from promptpotter.application.bench.resume_and_fork.replayers import (
        ReplayContext,
        Replayer,
    )
    from promptpotter.application.optimizers.potter.state import PotterState
    from promptpotter.domain.cycle_paths import CycleHop
    from promptpotter.domain.results import RoundResult
    from promptpotter.infrastructure.store.campaign_store.store import CampaignStore

logger = logging.getLogger(__name__)

__all__ = ["POTTER_REPLAYERS", "rederive_critiques", "round_packages"]


def _replay_round_winner(
    ctx: ReplayContext, inputs_ref: dict[str, Any], data: dict[str, Any]
) -> str:
    """The parent is READ from the decision: a reconstructed panel re-elects differently under an unchanged scorer."""
    parent = data.get("parent_cells")
    if parent is None:
        raise ValueError(
            "this ROUND_WINNER decision carries no `parent_cells`, so the panel its election "
            "ranked against is unrecoverable and the winner cannot be re-derived"
        )
    all_results = ctx.round_data.all_candidate_results
    candidate_ids = [str(c) for c in (inputs_ref.get("candidate_ids") or [])]
    coverage_floor = int(inputs_ref["coverage_floor"])
    # `parent_bias` is read, never re-derived: a function of the round HISTORY this replay does not hold.
    winner_id, _ = elect_round_winner(
        candidate_ids,
        all_results,
        recorded_parent(parent),
        coverage_floor,
        ctx.ruler,
        parent_bias=float(inputs_ref["parent_bias"]),
    )
    return winner_id


def _pobb_replay_snapshot(
    ctx: ReplayContext, inputs_ref: dict[str, Any], data: dict[str, Any]
) -> float | None:
    """``None`` where a cell the live check fit on is no longer among the arm's rows."""
    arm = ctx.round_data.all_candidate_results.get(inputs_ref["candidate_id"], NO_CELLS)
    rows = {cell.ruler_key: cell for cell in arm}
    cells = [int(s) for s in data["candidate_sample_ids"]]
    if any(cell not in rows for cell in cells):
        return None
    priors = {
        pid: {int(cell): float(grade) for cell, grade in grades.items()}
        for pid, grades in data["prior_histories"].items()
    }
    fit = paired_p_best([rows[cell] for cell in cells], priors, ctx.ruler)
    return None if fit is None else fit.p_best


def _replay_elimination_cut(
    ctx: ReplayContext, inputs_ref: dict[str, Any], data: dict[str, Any]
) -> bool:
    """Dispatches on the gate the PRODUCER named: a collapse cut holds no posterior, so ε would test it against a bar nobody set."""
    if inputs_ref.get("gate") == EliminationGate.COLLAPSED:
        # Re-asked: a labelless round reads `fitness`, so a formula that now solves every cell retires the cut.
        cid = str(inputs_ref.get("candidate_id", ""))
        rows = ctx.round_data.all_candidate_results.get(cid, NO_CELLS).cells
        return is_answer_collapsed(rows[: int(inputs_ref["queries_scored"])])
    p_best = _pobb_replay_snapshot(ctx, inputs_ref, data)
    if p_best is None:
        return False
    return p_best < float(inputs_ref["epsilon"])


def _replay_leader_lock_in(
    ctx: ReplayContext, inputs_ref: dict[str, Any], data: dict[str, Any]
) -> bool:
    if int(inputs_ref["queries_scored"]) < int(inputs_ref["lock_in_n_min"]):
        return False
    p_best = _pobb_replay_snapshot(ctx, inputs_ref, data)
    if p_best is None:
        return False
    # ``min`` over priors is the lock-in metric; no separate leader guard.
    return p_best >= float(inputs_ref["lock_in"])


# The two escalation triggers are archived: each folds the cycle's escalation history, which no replayer holds.
POTTER_REPLAYERS: dict[str, Replayer] = {
    PotterCheckpointKind.ROUND_WINNER: _replay_round_winner,
    PotterCheckpointKind.ELIMINATION_CUT: _replay_elimination_cut,
    PotterCheckpointKind.LEADER_LOCK_IN: _replay_leader_lock_in,
}


def _seat_on(
    cycle: Cycle, rounds: list[RoundResult], rr: RoundResult
) -> tuple[NodeContext[Any], PotterState]:
    cycle.replay_priors([*(p for p in rounds if p.round < rr.round), rr])
    ledger = cycle.session.state.ledger
    assert ledger is not None, "a resume replays its rounds under the run's observers"
    seat = RoundContext(cycle=cycle, round_num=rr.round, callbacks=RunCallbacks(ledger=ledger))
    return NodeContext(seat, "l1_critique"), potter_state(cycle.working_state)


def round_packages(cycle: Cycle, rounds: list[RoundResult]) -> dict[int, dict[str, str]]:
    """Each rebuilt at ITS OWN point in the run: a bundle carries the cumulative trajectory, so one full rebuild moves round 0's."""

    out: dict[int, dict[str, str]] = {}
    for rr in rounds:
        out[rr.round] = node_packages(build_bundle(*_seat_on(cycle, rounds, rr)))
    cycle.replay_priors(rounds)  # leave the caller the full trajectory it walked in with
    return out


async def rederive_critiques(
    campaign_store: CampaignStore,
    hop: CycleHop,
    cycle: Cycle,
    rounds: list[RoundResult],
    drifted: list[int],
) -> list[RoundResult]:
    """A repair, not a rewind: measurements and winner untouched; round 0's comes from the ORIGIN path."""

    restated = list(rounds)
    try:
        for slot, rr in enumerate(rounds):
            payload = rr.optimizer_state.payload_as(PotterRoundState)
            if rr.round not in drifted or not payload.critique or rr.round == 0:
                continue
            seat = _seat_on(cycle, restated, rr)
            # Outside the round loop this ContextVar still holds whatever the last round set.
            token = set_current_round(rr.round)
            try:
                with graceful(f"round {rr.round} critique re-derivation failed"):
                    state = rr.optimizer_state.model_copy(
                        update={
                            "payload": payload.model_copy(
                                update={"critique": await run_l1_critique(*seat)}
                            )
                        }
                    )
                    restated[slot] = rr.model_copy(update={"optimizer_state": state})
                    campaign_store.restate_optimizer_state(hop, restated[slot])
                    logger.warning(
                        "Round %d critique re-distilled: its input package drifted when the "
                        "round was repaired, so the recorded one described evidence that no "
                        "longer exists.",
                        rr.round,
                    )
            finally:
                reset_current_round(token)
    finally:
        cycle.replay_priors(restated)
    return restated
