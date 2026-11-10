"""Potter's replayers; the generic half of resume and fork, ``bench/resume_and_fork/``, reaches them only through its members' declarations."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from promptpotter.application.optimizers.potter.pobb.checks import (
    graded_sheet,
    locks_in,
    prior_reads,
    race_sheet,
)
from promptpotter.application.optimizers.potter.records import PotterCheckpointKind
from promptpotter.application.scoring.selection import elect_round_winner
from promptpotter.domain.origin_panel import election_rows
from promptpotter.domain.paired_reading import ArmKey
from promptpotter.domain.scoring import NO_CELLS

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
        admission=float(inputs_ref["admission"]),
    )
    return winner


def _race_replay(
    ctx: ReplayContext, inputs_ref: dict[str, Any], data: dict[str, Any]
) -> tuple[CellSheet, dict[ArmKey, CellSheet]] | None:
    """The arm's sheet off its rescored rows at ``candidate_cells``, each prior's off the grades
    recorded; ``None`` where a cell the live check read is no longer among the arm's rows."""
    rows = ctx.round_data.rows(ArmKey(inputs_ref["arm"])).by_key()
    cells = [str(key) for key in data["candidate_cells"]]
    if any(cell not in rows for cell in cells):
        return None
    priors = {ArmKey(pid): graded_sheet(grades) for pid, grades in data["prior_histories"].items()}
    return race_sheet(rows[cell] for cell in cells), priors


def _replay_elimination_cut(
    ctx: ReplayContext, inputs_ref: dict[str, Any], data: dict[str, Any]
) -> bool:
    read = _race_replay(ctx, inputs_ref, data)
    if read is None:
        return False
    reads = prior_reads(*read, ctx.ruler, n_min=int(inputs_ref["n_min"]))
    return bool(reads) and min(r.p_better for r in reads.values()) < float(inputs_ref["epsilon"])


def _replay_leader_lock_in(
    ctx: ReplayContext, inputs_ref: dict[str, Any], data: dict[str, Any]
) -> bool:
    read = _race_replay(ctx, inputs_ref, data)
    if read is None:
        return False
    return locks_in(
        ArmKey(inputs_ref["arm"]),
        *read,
        ArmKey(inputs_ref["parent"]),
        crowned=frozenset(str(cell) for cell in data["crowned"]),
        n_min=int(inputs_ref["n_min"]),
        ruler=ctx.ruler,
        admission=float(inputs_ref["admission"]),
    )


# The two escalation triggers are archived: each folds the cycle's escalation history, which no replayer holds.
ELECTION_REPLAYERS: dict[str, Replayer] = {
    PotterCheckpointKind.ROUND_WINNER: _replay_round_winner,
}
RACE_REPLAYERS: dict[str, Replayer] = {
    PotterCheckpointKind.ELIMINATION_CUT: _replay_elimination_cut,
    PotterCheckpointKind.LEADER_LOCK_IN: _replay_leader_lock_in,
}
