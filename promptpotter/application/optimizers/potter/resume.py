"""Potter's half of resume and fork; the generic half, ``bench/resume_and_fork/``, reaches it only through ``OptimizerRuntime``."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from promptpotter.application import optimizers
from promptpotter.application.bench.node_context import NodeContext
from promptpotter.application.optimizers.nodes import RoundContext
from promptpotter.application.optimizers.potter.dispatch.facade import build_bundle, node_packages
from promptpotter.application.optimizers.potter.l1.critique import distil_critique
from promptpotter.application.optimizers.potter.pobb.checks import EliminationGate
from promptpotter.application.optimizers.potter.records import Ladder, PotterCheckpointKind
from promptpotter.application.run_callbacks import RunCallbacks
from promptpotter.application.scoring.selection import (
    PairedGrade,
    elect_round_winner,
    paired_p_best,
)
from promptpotter.domain.paired_reading import ArmKey
from promptpotter.domain.results import election_rows
from promptpotter.domain.scoring import NO_CELLS, is_answer_collapsed
from promptpotter.infrastructure.llm.telemetry import at_round
from promptpotter.shared.errors import graceful

if TYPE_CHECKING:
    from promptpotter.application.bench.cycle import Cycle
    from promptpotter.application.bench.resume_and_fork.replayers import (
        ReplayContext,
        Replayer,
    )
    from promptpotter.domain.cycle_paths import CycleHop
    from promptpotter.domain.results import RoundResult
    from promptpotter.domain.scoring import CellSheet
    from promptpotter.infrastructure.store.campaign_store.store import CampaignStore

logger = logging.getLogger(__name__)


def _replay_round_winner(
    ctx: ReplayContext, inputs_ref: dict[str, Any], data: dict[str, Any]
) -> ArmKey | None:
    """The parent is the reference the round BANKED, never a panel rebuilt here: that one re-elects differently."""
    round_data = ctx.round_data
    arms = [ArmKey(str(key)) for key in inputs_ref["arms"]]
    coverage_floor = int(inputs_ref["coverage_floor"])
    crowned = frozenset(str(cell) for cell in inputs_ref["crowned"])

    def pooled(key: ArmKey) -> CellSheet:
        on_panel = round_data.overlap_results.get(key, NO_CELLS)
        return election_rows(round_data.rows(key), on_panel, crowned)

    winner, _ = elect_round_winner(
        arms,
        {key: pooled(key) for key in arms},
        pooled(ArmKey(str(inputs_ref["parent"]))),
        coverage_floor,
        ctx.ruler,
        epsilon=float(inputs_ref["epsilon"]),
    )
    return winner


def _pobb_replay_snapshot(
    ctx: ReplayContext, inputs_ref: dict[str, Any], data: dict[str, Any]
) -> float | None:
    """``None`` where a cell the live check fit on is no longer among the arm's rows."""
    rows = ctx.round_data.rows(ArmKey(inputs_ref["arm"])).by_key()
    cells = [str(key) for key in data["candidate_cells"]]
    if any(cell not in rows for cell in cells):
        return None
    priors = {
        ArmKey(pid): {str(cell): PairedGrade(**grade) for cell, grade in grades.items()}
        for pid, grades in data["prior_histories"].items()
    }
    fit = paired_p_best(
        [rows[cell] for cell in cells], priors, ctx.ruler, min_shared=int(inputs_ref["n_min"])
    )
    return None if fit is None else fit.p_best


def _replay_elimination_cut(
    ctx: ReplayContext, inputs_ref: dict[str, Any], data: dict[str, Any]
) -> bool:
    """Dispatches on the gate the PRODUCER named: a collapse cut holds no posterior, so ε would test it against a bar nobody set."""
    if inputs_ref.get("gate") == EliminationGate.COLLAPSED:
        # Re-asked: a labelless round reads `fitness`, so a formula that now solves every cell retires the cut.
        rows = ctx.round_data.rows(ArmKey(str(inputs_ref["arm"]))).cells
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


def _seat_on(cycle: Cycle, rounds: list[RoundResult], rr: RoundResult) -> NodeContext[Any]:
    cycle.replay_priors([*(p for p in rounds if p.round < rr.round), rr])
    ledger = cycle.session.state.ledger
    assert ledger is not None, "a resume replays its rounds under the run's observers"
    seat = RoundContext(cycle=cycle, round_num=rr.round, callbacks=RunCallbacks(ledger=ledger))
    return NodeContext[Any](seat, optimizers.member("l1_critique"))


def round_packages(cycle: Cycle, rounds: list[RoundResult]) -> dict[int, dict[str, str]]:
    """Each rebuilt at ITS OWN point in the run: a bundle carries the cumulative trajectory, so one full rebuild moves round 0's."""

    out: dict[int, dict[str, str]] = {}
    for rr in rounds:
        ctx = _seat_on(cycle, rounds, rr)
        out[rr.round] = node_packages(build_bundle(ctx, ctx.slice(Ladder)))
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
            if rr.round not in drifted or not rr.closed_state.feedback() or rr.round == 0:
                continue
            seat = _seat_on(cycle, restated, rr)
            # Outside the round loop this ContextVar still holds whatever the last round set.
            with at_round(rr.round), graceful(f"round {rr.round} critique re-derivation failed"):
                state = rr.closed_state.holding(await distil_critique(seat))
                restated[slot] = rr.model_copy(update={"optimizer_state": state})
                campaign_store.restate_round(
                    hop, campaign_store.restated_state(hop, restated[slot])
                )
                logger.warning(
                    "Round %d critique re-distilled: its input package drifted when the "
                    "round was repaired, so the recorded one described evidence that no "
                    "longer exists.",
                    rr.round,
                )
    finally:
        cycle.replay_priors(restated)
    return restated
