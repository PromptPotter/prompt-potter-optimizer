"""Potter's half of resume and fork: how each of its decision kinds is gated and re-derived, what
its nodes were handed per round, and re-distilling the critique a repair left stale. The generic
half — replay, repair, fork — is ``bench/resume_and_fork/``, which reaches this only
through ``OptimizerRuntime``."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any, cast

from promptpotter.application.bench.resume_and_fork.decisions import GatingMode
from promptpotter.application.intelligence.exploration import graded_response
from promptpotter.application.optimizers.potter.dispatch.facade import build_bundle, node_packages
from promptpotter.application.optimizers.potter.l1.critique import run_l1_critique
from promptpotter.application.optimizers.potter.pobb.checks import EliminationGate
from promptpotter.application.optimizers.potter.state import potter_state
from promptpotter.application.scoring.selection import elect_round_winner, elimination_p_best
from promptpotter.domain.optimizer_state import potter_round_state
from promptpotter.domain.run_records import PotterCheckpointKind, ResumeCheckpointKind
from promptpotter.domain.scoring import is_answer_collapsed
from promptpotter.infrastructure.llm.telemetry import reset_current_round, set_current_round
from promptpotter.shared.errors import graceful

if TYPE_CHECKING:
    from promptpotter.application.bench.cycle import Cycle
    from promptpotter.application.bench.resume_and_fork.replayers import (
        ReplayContext,
        Replayer,
    )
    from promptpotter.application.initialization.session import Session
    from promptpotter.domain.cycle_paths import CycleHop
    from promptpotter.domain.results import RoundResult
    from promptpotter.domain.scoring import QueryMeasurement
    from promptpotter.infrastructure.store.campaign_store.store import CampaignStore

logger = logging.getLogger(__name__)

__all__ = ["POTTER_CHECKPOINT_GATING", "POTTER_REPLAYERS", "rederive_critiques", "round_packages"]


POTTER_CHECKPOINT_GATING: dict[ResumeCheckpointKind, GatingMode] = {
    PotterCheckpointKind.ROUND_WINNER: GatingMode.REPLAYED,
    PotterCheckpointKind.ELIMINATION_CUT: GatingMode.REPLAYED,
    PotterCheckpointKind.LEADER_LOCK_IN: GatingMode.REPLAYED,
    # A layer trigger is a FOLD over the cycle's escalation history, not a function of
    # one round's measurements — the counter bumps once per escalation *request*, resets
    # on every fire, and compares against the best-at-entry snapshot taken at the last
    # fire (`EscalationFSM.observe_l2_escalation`). A replayer is pure over
    # `ReplayContext` (one round + the origin), so that fold is not expressible there.
    # Their scorer-dependence is entirely mediated by `improved`, hence by the round
    # measurements — which ARE replayed above, so a scorer change that would move a
    # trigger already shows up as a winner/cut divergence in the same round.
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
    """Re-derive ``p_best`` on the cycle's fixed δ ruler via the same closed-form ``elimination_p_best``
    the live check ran. ``None`` when the rescored measurements are not available."""
    candidate_id = str(inputs_ref.get("candidate_id", ""))
    candidate_sample_ids = [str(s) for s in (data.get("candidate_sample_ids") or [])]
    prior_histories: dict[str, dict[str, float]] = data.get("prior_histories") or {}
    if not candidate_sample_ids or not prior_histories:
        return None

    all_results = ctx.round_data.all_candidate_results
    cur_results = all_results.get(candidate_id) or []
    cur_by_sample = {
        str(r.get("sample_id")): graded_response(r)
        for r in cur_results
        if r.get("sample_id") is not None
    }
    if not all(sid in cur_by_sample for sid in candidate_sample_ids):
        return None
    candidate_grades = [cur_by_sample[sid] for sid in candidate_sample_ids]

    paired_prior_grades: dict[str, list[float]] = {}
    for cid, hist in prior_histories.items():
        if all(sid in hist for sid in candidate_sample_ids):
            paired_prior_grades[cid] = [float(hist[sid]) for sid in candidate_sample_ids]
    if not paired_prior_grades:
        return None

    p_best, _per_prior = elimination_p_best(
        candidate_grades,
        paired_prior_grades,
        [int(s) for s in candidate_sample_ids],
        ctx.ruler,
    )
    return float(p_best)


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


def round_packages(cycle: Cycle, rounds: list[RoundResult]) -> dict[int, dict[str, str]]:
    """``{round: {node: package fingerprint}}``, each rebuilt at ITS OWN point in the run — a bundle
    carries the cumulative trajectory, so one full rebuild would move round 0's bundle too."""

    out: dict[int, dict[str, str]] = {}
    for k, rr in enumerate(rounds):
        # ``max(k, 1)``: ``replay_priors([])`` is a NO-OP, so k=0 would inherit the caller's
        # full trajectory and round 0's fingerprint would move whenever a LATER round was
        # repaired. Round 0 IS the origin, so the state before it is the origin's.
        cycle.replay_priors(rounds[: max(k, 1)])
        state = potter_state(cycle.working_state)
        out[rr.round] = node_packages(build_bundle(cycle, state, latest_round=rr))
    cycle.replay_priors(rounds)  # leave the caller the full trajectory it walked in with
    return out


async def rederive_critiques(
    campaign_store: CampaignStore,
    hop: CycleHop,
    session: Session,
    cycle: Cycle,
    drifted: list[RoundResult],
) -> None:
    """Re-distil the critique of each round whose package drifted, in place on disk. Measurements and
    winner untouched, so this is a repair, not a rewind; round 0's comes from the ORIGIN path."""

    saved = cycle.rounds
    try:
        for rr in drifted:
            payload = potter_round_state(rr.optimizer_state)
            if not payload.critique or rr.round == 0:
                continue
            cycle.rounds = [p for p in saved if p.round < rr.round]
            # `emit_token_usage` stamps from this ContextVar, which outside the round loop
            # still holds whatever the last round set.
            token = set_current_round(rr.round)
            try:
                with graceful(f"round {rr.round} critique re-derivation failed"):
                    payload.critique = await run_l1_critique(
                        cycle,
                        potter_state(cycle.working_state),
                        rr,
                        round_num=rr.round,
                        ledger=session.state.ledger,
                    )
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
        cycle.rounds = saved


# Both directions fail import: a REPLAYED kind with no replayer (silent non-replay on resume) and
# an ARCHIVAL kind with one (replaying a kind that must never be re-derived).
_replayed = {k for k, mode in POTTER_CHECKPOINT_GATING.items() if mode is GatingMode.REPLAYED}
if _replayed != set(POTTER_REPLAYERS):
    raise RuntimeError(
        f"potter's REPLAYED kinds {sorted(_replayed)} and its replayers "
        f"{sorted(POTTER_REPLAYERS)} must be one set."
    )
del _replayed
