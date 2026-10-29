"""Potter's replayers; the generic half of resume and fork, ``bench/resume_and_fork/``, reaches them only through its members' declarations."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from promptpotter.application.optimizers.potter.pobb.checks import EliminationGate
from promptpotter.application.optimizers.potter.records import PotterCheckpointKind
from promptpotter.application.scoring.selection import (
    PairedGrade,
    elect_round_winner,
    paired_p_best,
)
from promptpotter.domain.origin_panel import election_rows
from promptpotter.domain.paired_reading import ArmKey
from promptpotter.domain.scoring import NO_CELLS, is_answer_collapsed

if TYPE_CHECKING:
    from promptpotter.application.bench.resume_and_fork.replayers import (
        ReplayContext,
        Replayer,
    )
    from promptpotter.domain.scoring import CellSheet


def _replay_round_winner(
    ctx: ReplayContext, inputs_ref: dict[str, Any], _data: dict[str, Any]
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
ELECTION_REPLAYERS: dict[str, Replayer] = {
    PotterCheckpointKind.ROUND_WINNER: _replay_round_winner,
}
RACE_REPLAYERS: dict[str, Replayer] = {
    PotterCheckpointKind.ELIMINATION_CUT: _replay_elimination_cut,
    PotterCheckpointKind.LEADER_LOCK_IN: _replay_leader_lock_in,
}
