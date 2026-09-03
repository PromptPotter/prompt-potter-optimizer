from __future__ import annotations

from functools import partial
from typing import TYPE_CHECKING, Any, cast

from promptpotter.application.intelligence.adaptive_queue_mechanism import build_round_order
from promptpotter.application.optimization.l1.score.candidate import score_one_candidate
from promptpotter.application.optimization.l1.score.signal_effect import CandidateOutcome
from promptpotter.application.optimization.pobb.checks import (
    PoBBConfig,
    build_elimination_check,
)
from promptpotter.application.optimization.resume_and_fork.decisions import ResumeCheckpointRecord
from promptpotter.application.scoring.search_point_scorer import score_search_point
from promptpotter.domain.escalation_signals import EscalationSignal
from promptpotter.domain.opt_search_point import OptSearchPoint
from promptpotter.domain.results import CandidateProposal, ScoredCandidate
from promptpotter.domain.scoring import QueryMeasurement
from promptpotter.domain.validators import StopRule
from promptpotter.shared.errors import is_error_result
from promptpotter.shared.instrument import MeasuredCandidate, MeasurementRole

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
]:
    n = len(population)

    all_candidate_results: dict[str, list[QueryMeasurement]] = {}
    candidate_scores: list[ScoredCandidate] = []
    escalation_signal: EscalationSignal | None = None

    async def _pobb_backfill(
        sp: JobSearchPoint, samples: list[Sample], prior_id: str
    ) -> list[QueryMeasurement]:
        """Score a PRIOR searchpoint for paired fill-in — so it fires NO per-sample display callbacks, which would mint a
        bogus ``C{round}.0`` row. ``degradation_checks=None`` blocks recursive PoBB on the backfill."""
        best_full_results, _best_full_scores, _signal = await score_search_point(
            sp,
            samples,
            cycle.session,
            label="pobb_backfill",
            # A backfill catches a PRIOR up on a sample the current candidate reached; it
            # feeds the paired posterior, not that prior's own report. Its opt_sp-aware
            # evaluators would describe optimizer state from the round it was scored in.
            opt_sp=None,
            degradation_checks=None,
            n_total_candidates=0,
            axes=cycle.axes,
            on_sample_scored=None,
            on_sample_starting=None,
            # The PRIOR being caught up — never the foreground candidate whose sample set
            # triggered this. Inheriting that binding is what filed C1.1's backfills under
            # C1.2. ``idx`` is -1 because a backfill occupies no slot in the round's
            # population; ``role`` is what tells a reader this row was measured for a
            # paired comparison, outside the round's shared order.
            measured=MeasuredCandidate(
                idx=-1,
                candidate_id=prior_id,
                label=f"prior:{prior_id[:8]}",
                role=MeasurementRole.BACKFILL,
            ),
        )
        return best_full_results

    elim_check = build_elimination_check(
        pobb_config,
        n_samples=len(dataset),
        ruler=cycle.ruler,
        backfill_fn=_pobb_backfill,
    )

    # Prime PoBB priors so candidate #1 has a comparator — without it, PoBB short-circuits on empty
    # priors and round-1 cand-1 was un-eliminable. `current_results` = best-so-far per-sample
    # history; `current_sp` is the leader, backfill-able on the candidate's hard samples. This is
    # the round's PARENT (`RoundParent` — the origin at round 0, the prior winner after it); the
    # repo spends `seed` on `CycleSeed`, the `seed-screen` verb and an L4 inner cell.
    parent_results = cycle.tracking.current_results
    parent_sp = cycle.tracking.current_sp
    parent_grades: dict[int, float] = {}
    if parent_results and parent_sp is not None:
        parent_id = f"R{cycle.rounds[-1].round}_winner"
        elim_check.register_completed(
            cast("list[QueryMeasurement]", parent_results), candidate_id=parent_id, sp=parent_sp
        )
        # CORRECTNESS, not the composite. `priors_by_sample` holds `graded_response` — the
        # `objective` θ is fit on — and `build_round_order` thresholds these with `is_hit`, which
        # `domain/scoring.py::CellScorer` declares a predicate on `fitness` and names the
        # difficulty stratification as one of its readers. Under any `per_cell` composite the two
        # differ, and where the composite scales below 1.0 (a latency or token penalty) EVERY cell
        # reads as a miss: the hit stratum empties and the parent-HIT regression probe never fires.
        parent_grades = {
            int(sid): float(r["fitness"])
            for r in parent_results
            if (sid := r.get("sample_id")) is not None and not is_error_result(r)
        }

    # ONE deterministic shared order per round — parent-MISS samples front-loaded, a parent-HIT
    # regression probe every 4th slot, cells the parent never answered ordered by discrimination —
    # so the ε-gate sees discriminating evidence immediately instead of a zero-information tie
    # prefix. Every candidate walks the same order: shared prefixes keep paired stats comparable
    # and the running display honest.
    order = build_round_order(parent_grades, cycle.ruler, [int(s.id) for s in dataset])
    samples_by_id = {int(s.id): s for s in dataset}
    dataset = [samples_by_id[sid] for sid in order]

    for idx, opt_sp_c in enumerate(population):
        pipeline_params_override = proposals[idx].pipeline_params_override or None
        # Single merge site: build the candidate's frozen searchpoint once and
        # share it with both consumers — the in-flight dashboard seed (resolved
        # config-only) and ``score_one_candidate`` (scoring + round-file report).
        candidate_sp = opt_sp_c.to_job_search_point(
            base_pipeline_params=effective_pipeline_params[idx],
            schema=cycle.session.pipeline_schema,
        )
        callbacks.on_candidate_started(
            idx,
            n,
            opt_sp_c.lineage.changes_description or "",
            pipeline_params_override,
            opt_sp_c.prompt_field_dict(),
            candidate_sp.config_params,
        )
        # Bind PoBBCheck so this candidate's per-sample snapshot rides the telemetry stream tagged.
        elim_check.set_current(
            opt_sp_c.lineage.id,
            on_snapshot=partial(callbacks.on_p_best_update, round_num, idx, n),
        )
        # The shared order at candidate start. Read off the ledger by the console, over SSE
        # by the chat's run card for "next in line", and absorbed into the dashboard
        # projection as `declared_sample_order` — which is what lets a reader that missed
        # this event still see forward.
        callbacks.on_sample_order_preview(
            round_num,
            idx,
            n,
            n_priors=len(elim_check.priors_by_sample),
            sample_order=order,
        )

        cr_result = await score_one_candidate(
            idx=idx,
            opt_sp_c=opt_sp_c,
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
        all_candidate_results[opt_sp_c.lineage.id] = cr_result.results
        if cr_result.runtime_failure is not None:
            opt_sp_c.memory.wounds.runtime_failures = [
                *opt_sp_c.memory.wounds.runtime_failures,
                cr_result.runtime_failure,
            ]
        candidate_scores.append(cr_result.report)
        callbacks.on_candidate_scored(idx, n, cr_result.report.model_dump())

        if cr_result.outcome == CandidateOutcome.ESCALATED:
            escalation_signal = cr_result.escalation_signal
            break  # true degradation — abort remaining candidates

    return all_candidate_results, candidate_scores, escalation_signal


__all__ = ["score_population"]
