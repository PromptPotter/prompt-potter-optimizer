"""Generation-only round — L1 variants without scoring. Shared by ``new --sweep-batch`` and ``--diag``; the round
document carries ``status='generation_only'``, no scoreboard and no accuracy."""

from __future__ import annotations

from promptpotter.application.initialization.session import Session
from promptpotter.application.optimization.cycle import Cycle
from promptpotter.application.optimization.l1.resume import generate_or_load_candidates
from promptpotter.application.output import (
    write_hard_samples_artifacts,
    write_log_md,
    write_review_md,
)
from promptpotter.application.run_observers import RunCallbacks
from promptpotter.domain.results import RoundResult
from promptpotter.domain.run_records import PhaseRecord
from promptpotter.shared.errors import graceful


async def run_sweep_generation_only(
    cycle: Cycle,
    session: Session,
    cb: RunCallbacks,
    round_num: int,
    *,
    label: str = "sweep_gen_only",
) -> None:
    cb.set_round(round_num)
    if (ledger := session.state.ledger) is not None:
        ledger.append(PhaseRecord(phase="round", event="enter", round=round_num))

    candidates, yield_stats = await generate_or_load_candidates(
        round_num,
        cycle,
        cb.on_phase,
        n_scoring_samples=0,
    )

    if session.state.cycle_id:
        with graceful("Sweep generation-only round_data write failed"):
            # A generation-only round IS a round — same document, `status` telling every
            # reader it was never scored. The scoring scalars below are structural zeros
            # (no measurement happened), not measurements of zero; `health` and the
            # matched-origin pair stay None because a degradation verdict and an
            # origin-restricted-to-the-winner's-samples both need scored samples.
            session.store.campaigns.save_round_file(
                session.hop,
                RoundResult(
                    round=round_num,
                    label=label,
                    status="generation_only",
                    accuracy=0.0,
                    composite_fitness=0.0,
                    total=0,
                    improved=False,
                    prompt_fields=cycle.opt_sp.prompt_field_dict(),
                    candidates_scored=0,
                    l1_yield=yield_stats.l1_yield,
                    # Collapse counts derive from `candidate_scores` — see `RoundResult`.
                    opt_sp=cycle.opt_sp,
                ),
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


__all__ = ["run_sweep_generation_only"]
