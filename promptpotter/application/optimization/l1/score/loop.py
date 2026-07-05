"""Per-population scoring loop. Owns the candidate loop, accumulators, the shared round order, and ESCALATED break."""

from __future__ import annotations

from functools import partial
from typing import TYPE_CHECKING, Any, cast

from promptpotter.application.intelligence.adaptive_queue_mechanism import build_round_order
from promptpotter.application.optimization.l1.score.candidate import score_one_candidate
from promptpotter.application.optimization.l1.score.signal_effect import CandidateOutcome
from promptpotter.application.optimization.pobb.elimination import (
    PoBBConfig,
    build_elimination_check,
)
from promptpotter.application.optimization.resume_and_fork import ResumeCheckpointRecord
from promptpotter.application.scoring.search_point_scorer import score_search_point
from promptpotter.domain.escalation_signals import EscalationSignal
from promptpotter.domain.opt_search_point import OptSearchPoint
from promptpotter.domain.results import CandidateProposal, SampleOrderStep, ScoredCandidate
from promptpotter.domain.scoring import QueryMeasurement
from promptpotter.domain.validators import StopRule
from promptpotter.infrastructure.tracing import CandidateScored
from promptpotter.shared.errors import graceful

if TYPE_CHECKING:
    from promptpotter.application.optimization.cycle import Cycle
    from promptpotter.application.run_observers import RunCallbacks
    from promptpotter.domain.sample import Sample
    from promptpotter.domain.search_point import JobSearchPoint


async def score_population(
    cycle: Cycle,
    population: list[OptSearchPoint],
    effective_pipeline_params: list[dict[str, Any] | None],
    proposals: list[CandidateProposal],
    dataset: list[Sample],
    *,
    degradation_checks: list[StopRule] | None = None,
    callbacks: RunCallbacks,
    pobb_config: PoBBConfig,
    round_num: int = 0,
    decisions: list[ResumeCheckpointRecord] | None = None,
    l1_diversity: float = 1.0,
) -> tuple[
    dict[str, list[QueryMeasurement]],
    list[ScoredCandidate],
    EscalationSignal | None,
    list[SampleOrderStep],
]:
    """Score each individual; per-candidate body in `score_one_candidate`. Owns the ESCALATED break.

    The 4th return is the round's sample-order timeline — a single frozen step
    holding the shared deterministic order every candidate walks (the trajectory
    hover renders it as the round's plan).
    """
    session = cycle.session
    obs = session.state.obs
    n = len(population)

    all_candidate_results: dict[str, list[QueryMeasurement]] = {}
    candidate_scores: list[ScoredCandidate] = []
    escalation_signal: EscalationSignal | None = None

    async def _pobb_backfill(sp: JobSearchPoint, samples: list[Sample]) -> list[QueryMeasurement]:
        """Score *sp* on *samples* for PoBB paired-comparison fill-in. *sp* is a
        PRIOR searchpoint, not a foreground candidate — so this fires **no**
        per-sample display callbacks (``on_sample_scored=None``). The backfill's
        own surface is the dedicated ``on_pobb_backfill`` event the candidate
        loop emits (`candidate.py`); its spend rides the token ledger. Wiring the
        foreground candidate callbacks here would mint a bogus ``C{round}.0`` row.
        ``degradation_checks=None`` blocks recursive PoBB on backfill.
        """
        best_full_results, _best_full_scores, _signal = await score_search_point(
            sp,
            samples,
            cycle.session,
            label="pobb_backfill",
            degradation_checks=None,
            n_total_candidates=0,
            axes=cycle.axes,
            l1_diversity=0.0,
            on_sample_scored=None,
            on_sample_starting=None,
        )
        return best_full_results

    elim_check = build_elimination_check(
        pobb_config,
        n_samples=len(dataset),
        delta_scale=cycle.delta_scale or {},
        backfill_fn=_pobb_backfill,
    )

    # Seed PoBB priors so candidate #1 has a comparator — without it, PoBB short-circuits on empty
    # priors and round-1 cand-1 was un-eliminable. `current_results` = best-so-far per-sample
    # history; `current_sp` is the leader, backfill-able on the candidate's hard samples.
    seed_results = cycle.tracking.current_results
    seed_sp = cycle.tracking.current_sp
    seed_grades: dict[int, float] = {}
    if seed_results and seed_sp is not None:
        seed_id = f"R{cycle.rounds[-1].round}_winner" if cycle.rounds else "origin"
        elim_check.register_completed(
            cast("list[QueryMeasurement]", seed_results), candidate_id=seed_id, sp=seed_sp
        )
        seed_grades = {
            int(sid): grade for sid, grade in elim_check.priors_by_sample[seed_id].items()
        }

    # ONE deterministic shared order per round — seed-MISS (win-opportunity)
    # samples front-loaded, a seed-HIT regression probe every 4th slot — so the
    # paired-margin gate sees decision evidence immediately instead of a
    # zero-information tie prefix. Every candidate walks the same order: shared
    # prefixes keep paired stats comparable and the running display honest.
    order = build_round_order(seed_grades, cycle.delta_scale or {}, [int(s.id) for s in dataset])
    samples_by_id = {int(s.id): s for s in dataset}
    dataset = [samples_by_id[sid] for sid in order]
    dataset_sample_ids = [int(s.id) for s in dataset]
    # The round's frozen plan — a single step; there is no per-sample re-rank.
    representative_timeline = [
        SampleOrderStep(
            step=0,
            current_sample_id=order[0] if order else None,
            computed=[],
            planned=list(order),
        )
    ]

    for idx, osp_c in enumerate(population):
        pipeline_params_override = proposals[idx].pipeline_params_override or None
        # Single merge site: build the candidate's frozen searchpoint once and
        # share it with both consumers — the in-flight dashboard seed (resolved
        # config-only) and ``score_one_candidate`` (scoring + round-file report).
        candidate_sp = osp_c.to_job_search_point(
            base_pipeline_params=effective_pipeline_params[idx],
            schema=cycle.session.pipeline_schema,
        )
        callbacks.on_candidate_started(
            idx,
            n,
            osp_c.lineage.changes_description or "",
            pipeline_params_override,
            osp_c.prompt_field_dict(),
            candidate_sp.config_params,
        )
        # Bind PoBBCheck so this candidate's per-sample snapshot rides the telemetry stream tagged.
        elim_check.set_current(
            osp_c.lineage.id,
            on_snapshot=partial(callbacks.on_p_best_update, round_num, idx, n),
        )
        # The shared order at candidate start — webapp table + console read it.
        callbacks.on_sample_order_preview(
            round_num,
            idx,
            n,
            n_priors=len(elim_check.priors_by_sample),
            sample_order=order,
        )

        # Universe for the paired-margin gate's win-opportunity count; unordered.
        elim_check.set_sample_universe(dataset_sample_ids)

        cr_result = await score_one_candidate(
            idx=idx,
            osp_c=osp_c,
            candidate_sp=candidate_sp,
            pipeline_params_override=pipeline_params_override,
            cycle=cycle,
            dataset=dataset,
            n_total=n,
            effective_pipeline_params=effective_pipeline_params[idx],
            elim_check=elim_check,
            callbacks=callbacks,
            degradation_checks=degradation_checks,
            decisions=decisions,
            candidate_scores=candidate_scores,
            round_num=round_num,
            l1_diversity=l1_diversity,
            force_fresh=False,
        )
        all_candidate_results[osp_c.lineage.id] = cr_result.results
        if cr_result.runtime_failure is not None:
            osp_c.memory.wounds.runtime_failures = [
                *osp_c.memory.wounds.runtime_failures,
                cr_result.runtime_failure,
            ]
        candidate_scores.append(cr_result.report)
        callbacks.on_candidate_scored(idx, n, cr_result.report.model_dump())
        if obs:
            with graceful("CandidateScored emit failed"):
                obs.emit_write_point(
                    CandidateScored,
                    campaign_id=session.state.tracing_campaign_id,
                    round_num=round_num,
                    candidate_idx=idx,
                    report=cr_result.report.model_dump(),
                )

        if cr_result.outcome == CandidateOutcome.ESCALATED:
            escalation_signal = cr_result.escalation_signal
            break  # true degradation — abort remaining candidates

    return all_candidate_results, candidate_scores, escalation_signal, representative_timeline


__all__ = ["score_population"]
