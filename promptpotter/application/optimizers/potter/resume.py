"""Potter's half of resume and fork: how each of its decision kinds is gated and re-derived, what
its nodes were handed per round, and re-distilling the critique a repair left stale. The generic
half — replay, repair, fork — is ``bench/resume_and_fork/``, which reaches this only
through ``OptimizerRuntime``."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any, cast

from promptpotter.application.bench.resume_and_fork.decisions import GatingMode
from promptpotter.application.optimizers.potter.dispatch.facade import build_bundle, node_packages
from promptpotter.application.optimizers.potter.l1.critique import run_l1_critique
from promptpotter.application.optimizers.potter.pobb.checks import EliminationGate
from promptpotter.application.optimizers.potter.records import (
    PotterCheckpointKind,
    PotterRoundState,
)
from promptpotter.application.optimizers.potter.state import potter_state
from promptpotter.application.scoring.selection import elect_round_winner, paired_p_best
from promptpotter.domain.run_records import CheckpointKind
from promptpotter.domain.scoring import is_answer_collapsed
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
    from promptpotter.domain.scoring import QueryMeasurement
    from promptpotter.infrastructure.store.campaign_store.store import CampaignStore

logger = logging.getLogger(__name__)

__all__ = ["POTTER_CHECKPOINT_GATING", "POTTER_REPLAYERS", "rederive_critiques", "round_packages"]


POTTER_CHECKPOINT_GATING: dict[CheckpointKind, GatingMode] = {
    PotterCheckpointKind.ROUND_WINNER: GatingMode.REPLAYED,
    PotterCheckpointKind.ELIMINATION_CUT: GatingMode.REPLAYED,
    PotterCheckpointKind.LEADER_LOCK_IN: GatingMode.REPLAYED,
    # A trigger is a fold over the cycle's escalation history, which no replayer holds:
    # `docs/developer/dispatch-hub.md` § Trigger.
    PotterCheckpointKind.L2_ESCALATION_TRIGGER: GatingMode.ARCHIVAL,
    PotterCheckpointKind.L3_ESCALATION_TRIGGER: GatingMode.ARCHIVAL,
}


def _replay_round_winner(
    ctx: ReplayContext, inputs_ref: dict[str, Any], data: dict[str, Any]
) -> str:
    """Re-derive the round winner through the SAME ``elect_round_winner`` the live scorer ran, against
    the SAME parent — READ from the decision, never reconstructed: the rule ranks each arm against
    the parent panel, so a reconstructed panel re-elects differently under an unchanged scorer."""
    parent = data.get("parent_cells")
    if parent is None:
        # Never fall back to a reconstruction — guessing quietly is the defect itself.
        raise ValueError(
            "this ROUND_WINNER decision carries no `parent_cells`, so the panel its election "
            "ranked against is unrecoverable and the winner cannot be re-derived"
        )
    all_results = ctx.round_data.all_candidate_results
    candidate_ids = [str(c) for c in (inputs_ref.get("candidate_ids") or [])]
    coverage_floor = int(inputs_ref["coverage_floor"])
    # Read, never re-derived: it is a function of the round HISTORY, which this replay does not
    # hold, so recomputing it is the same defect as reconstructing the parent panel above. A
    # record missing it RAISES.
    winner_id, _ = elect_round_winner(
        candidate_ids,
        cast("dict[str, list[QueryMeasurement]]", all_results),
        cast("list[QueryMeasurement]", parent),
        coverage_floor,
        ctx.ruler,
        parent_bias=float(inputs_ref["parent_bias"]),
    )
    return winner_id


def _pobb_replay_snapshot(
    ctx: ReplayContext, inputs_ref: dict[str, Any], data: dict[str, Any]
) -> float | None:
    """Re-derive ``p_best`` on the cycle's fixed δ ruler through the pairing the live check ran, over
    what its decision archived. ``None`` where a cell it fit on is no longer among the arm's rows."""
    rows = {
        str(r["sample_id"]): r
        for r in ctx.round_data.all_candidate_results.get(inputs_ref["candidate_id"]) or []
    }
    cells = [str(s) for s in data["candidate_sample_ids"]]
    if any(cell not in rows for cell in cells):
        return None
    fit = paired_p_best(
        cast("list[QueryMeasurement]", [rows[cell] for cell in cells]),
        data["prior_histories"],
        ctx.ruler,
    )
    return None if fit is None else fit.p_best


def _replay_elimination_cut(
    ctx: ReplayContext, inputs_ref: dict[str, Any], data: dict[str, Any]
) -> bool:
    """Dispatches on the gate the PRODUCER named, never on the ε rule alone: a collapse cut returns
    before ``elimination_p_best`` is reached, so it holds no posterior and re-deriving it under ε
    tests a real ``p_best`` against a bar nobody set — which no collapse can re-derive as true."""
    if inputs_ref.get("gate") == EliminationGate.COLLAPSED:
        # Re-asked, not re-derived under ε: on a labelled round the answer and its truths decide
        # it and rescoring touches neither, while a labelless one reads `fitness` — so a formula
        # that now solves every cell retires the cut, which is the verdict this replay exists for.
        cid = str(inputs_ref.get("candidate_id", ""))
        rows = ctx.round_data.all_candidate_results.get(cid) or []
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


POTTER_REPLAYERS: dict[str, Replayer] = {
    PotterCheckpointKind.ROUND_WINNER: _replay_round_winner,
    PotterCheckpointKind.ELIMINATION_CUT: _replay_elimination_cut,
    PotterCheckpointKind.LEADER_LOCK_IN: _replay_leader_lock_in,
}


def _seat_before(cycle: Cycle, rounds: list[RoundResult], rr: RoundResult) -> PotterState:
    """Re-seat the cycle where *rr*'s nodes read it: on the rounds closed before it. Round 0 is
    the origin's own measurement, which ``Cycle.start`` seats before any node runs."""
    cycle.replay_priors([p for p in rounds if p.round < rr.round] or rounds[:1])
    return potter_state(cycle.working_state)


def round_packages(cycle: Cycle, rounds: list[RoundResult]) -> dict[int, dict[str, str]]:
    """``{round: {node: package fingerprint}}``, each rebuilt at ITS OWN point in the run — a bundle
    carries the cumulative trajectory, so one full rebuild would move round 0's bundle too."""

    out: dict[int, dict[str, str]] = {}
    for rr in rounds:
        state = _seat_before(cycle, rounds, rr)
        out[rr.round] = node_packages(build_bundle(cycle, state, latest_round=rr))
    cycle.replay_priors(rounds)  # leave the caller the full trajectory it walked in with
    return out


async def rederive_critiques(
    campaign_store: CampaignStore,
    hop: CycleHop,
    cycle: Cycle,
    drifted: list[RoundResult],
) -> None:
    """Re-distil the critique of each round whose package drifted, in place on disk. Measurements and
    winner untouched, so this is a repair, not a rewind; round 0's comes from the ORIGIN path."""

    saved = list(cycle.rounds)
    try:
        for rr in drifted:
            payload = rr.optimizer_state.payload_as(PotterRoundState)
            if not payload.critique or rr.round == 0:
                continue
            state = _seat_before(cycle, saved, rr)
            # `emit_token_usage` stamps from this ContextVar, which outside the round loop
            # still holds whatever the last round set.
            token = set_current_round(rr.round)
            try:
                with graceful(f"round {rr.round} critique re-derivation failed"):
                    payload.critique = await run_l1_critique(cycle, state, rr)
                    campaign_store.save_round_file(hop, rr)
                    logger.warning(
                        "Round %d critique re-distilled: its input package drifted when the "
                        "round was repaired, so the recorded one described evidence that no "
                        "longer exists.",
                        rr.round,
                    )
            finally:
                reset_current_round(token)
    finally:
        cycle.replay_priors(saved)
