"""Sweep / diag generation-only round — L1 variants without scoring.

Used by both ``sweep`` (round-1 scored + round-2 generate-only) and
``diag`` modes. The round_data JSON is minimal: ``status='generation_only'``,
no scoreboard, no accuracy.
"""

from __future__ import annotations

from promptpotter.application.bootstrap.session import Session
from promptpotter.application.optimization.cycle import Cycle
from promptpotter.application.optimization.l1 import generate_or_load_candidates
from promptpotter.application.run_observers import RunCallbacks
from promptpotter.domain.run_records import PhaseRecord
from promptpotter.presentation.writers import (
    write_hard_samples_artifacts,
    write_log_md,
    write_review_md,
)
from promptpotter.shared.errors import graceful


async def run_sweep_generation_only(
    cycle: Cycle,
    session: Session,
    cb: RunCallbacks,
    round_num: int,
    *,
    label: str = "sweep_gen_only",
) -> None:
    """L1 variants without scoring; round_data JSON is minimal status='generation_only'."""
    cb.set_round(round_num)
    if (ledger := session.state.ledger) is not None:
        ledger.append(PhaseRecord(phase="round", event="enter", round=round_num))
    elif _rr := session.state.audit_projection:
        _rr.begin_round(round_num)

    candidates, yield_stats = await generate_or_load_candidates(
        round_num,
        cycle,
        cb.on_phase,
        n_eval_queries=0,
    )

    if session.state.cycle_id:
        with graceful("Sweep generation-only round_data write failed"):
            session.store.campaigns.save_round_file(
                session.backend_id,
                session.state.cycle_id,
                {
                    "round_id": f"round_{round_num}",
                    "round": round_num,
                    "label": label,
                    "status": "generation_only",
                    "accuracy": 0.0,
                    "composite_fitness": 0.0,
                    "hits": 0,
                    "total": 0,
                    "improved": False,
                    "candidates_scored": 0,
                    "candidate_scores": [],
                    "decisions": [],
                    "evaluators": {},
                    "l1_yield": yield_stats.l1_yield,
                    "l1_n_no_op": yield_stats.l1_n_no_op,
                    "l1_n_duplicate": yield_stats.l1_n_duplicate,
                    "opt_search_point": cycle.opt_sp.model_dump(),
                },
            )
        hard_samples_artifact = write_hard_samples_artifacts(session, cycle)
        write_log_md(session, hard_samples_artifact=hard_samples_artifact)
        write_review_md(session, cycle)

    if (ledger := session.state.ledger) is not None:
        ledger.append(
            PhaseRecord(
                phase="round",
                event="complete",
                round=round_num,
                payload={"status": "generation_only", "n_candidates": len(candidates)},
            )
        )
    elif _rr := session.state.audit_projection:
        _rr.flush()


__all__ = ["run_sweep_generation_only"]
