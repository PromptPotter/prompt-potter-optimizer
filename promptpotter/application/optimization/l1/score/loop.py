"""Per-population scoring loop. Owns the candidate loop, accumulators, online Rasch adaptive queue mechanism, and ESCALATED break."""

from __future__ import annotations

from functools import partial
from typing import TYPE_CHECKING, Any, cast

from promptpotter.application.intelligence.adaptive_queue_mechanism import (
    expected_order,
    next_sample,
    pick_value,
    posterior_from_outcomes,
)
from promptpotter.application.intelligence.exploration import (
    Observation,
    RaschPosterior,
    fit_rasch,
)
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
    online_reorder: bool = True,
) -> tuple[
    dict[str, list[QueryMeasurement]],
    list[ScoredCandidate],
    EscalationSignal | None,
    list[SampleOrderStep],
]:
    """Score each individual; per-candidate body in `score_one_candidate`. Owns the ESCALATED break.

    The 4th return is the round's adaptive sample-selection timeline (the
    representative/longest candidate's per-step (computed, planned) split),
    captured from the online picker for the trajectory hover.
    """
    session = cycle.session
    obs = session.state.obs
    n = len(population)

    all_candidate_results: dict[str, list[QueryMeasurement]] = {}
    candidate_scores: list[ScoredCandidate] = []
    escalation_signal: EscalationSignal | None = None
    # Per-candidate adaptive-picker timelines, keyed by candidate id; the
    # representative (longest, matching `selection`) is returned after the loop.
    sample_order_timelines: dict[str, list[SampleOrderStep]] = {}

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
    if seed_results and seed_sp is not None:
        seed_id = f"R{cycle.rounds[-1].round}_winner" if cycle.rounds else "origin"
        elim_check.register_completed(
            cast("list[QueryMeasurement]", seed_results), candidate_id=seed_id, sp=seed_sp
        )

    # Queue-mechanism plumbing. Undermeasured filter: PoBB-aborted candidates (<n/2 measurements)
    # only sit on the queue mechanism's discriminating samples and would bias the Rasch fit — drop
    # them. The seed prior is registered separately and full.
    pop_min_obs = max(1, len(dataset) // 2)

    def _filtered_obs(
        results_by_cid: dict[str, list[QueryMeasurement]] | dict[str, list[dict[str, Any]]],
    ) -> list[Observation]:
        out: list[Observation] = []
        for cid, results in results_by_cid.items():
            valid = [r for r in results if r.get("sample_id") is not None and not r.get("error")]
            if len(valid) < pop_min_obs:
                continue
            out.extend(
                Observation(
                    candidate_id=cid,
                    sample_id=int(r["sample_id"]),
                    hit=bool(r.get("hit")),
                )
                for r in valid
            )
        return out

    prior_round_obs: list[Observation] = []
    for rr in cycle.rounds:
        prior_round_obs.extend(_filtered_obs(rr.all_candidate_results))
    # Archive observations are completed + dataset-scoped — δ posterior covers all cross-cycle data.
    round_boundary_obs: list[Observation] = list(cycle.archive_observations) + prior_round_obs

    dataset_sample_ids = [int(s.id) for s in dataset]

    def _queue_mechanism_maps(
        posterior: RaschPosterior,
    ) -> tuple[dict[int, float], dict[int, float]]:
        """Full (δ̂_s, se_δ_s) maps. Unmeasured samples fall back to (μ_δ, σ_δ) — maximally
        informative rather than artificially zero.
        """
        delta_map = {
            sid: posterior.delta.get(sid, posterior.mu_delta) for sid in dataset_sample_ids
        }
        delta_se_map = {
            sid: posterior.delta_se.get(sid, posterior.sigma_delta) for sid in dataset_sample_ids
        }
        return delta_map, delta_se_map

    def _seed_posterior(
        d_map: dict[int, float], d_se_map: dict[int, float], p_var: float
    ) -> tuple[float, float]:
        """Fold seed outcomes into a θ_s posterior — anchors the queue mechanism's decision IG
        against `θ_c > θ_s`. Returns centred prior `(0, σ_θ²)` when seed is unmeasured.
        """
        if not seed_results:
            return 0.0, p_var
        outcomes = [
            (
                d_map.get(int(sr["sample_id"]), 0.0),
                d_se_map.get(int(sr["sample_id"]), 1.0),
                bool(sr.get("hit")),
            )
            for sr in seed_results
            if sr.get("sample_id") is not None and not sr.get("error")
        ]
        return posterior_from_outcomes(0.0, p_var, outcomes)

    # Live queue-mechanism observations — δ leaderboard re-fits on EVERY measurement (archive +
    # priors + this round's completed + in-flight outcomes) so queue mechanism + webapp track in
    # real time.
    round_live_obs: list[Observation] = []

    def _fit_queue_mechanism(
        extra_obs: list[Observation],
    ) -> tuple[dict[int, float], dict[int, float], float, float, float]:
        """Re-fit Rasch over all observations so far. *extra_obs* is the in-flight outcomes,
        not yet folded into `round_live_obs`. Returns (delta_map, delta_se_map, prior_var, seed_mu, seed_var).
        """
        posterior = fit_rasch(round_boundary_obs + round_live_obs + extra_obs)
        d_map, d_se_map = _queue_mechanism_maps(posterior)
        p_var = posterior.sigma_theta**2
        s_mu, s_var = _seed_posterior(d_map, d_se_map, p_var)
        return d_map, d_se_map, p_var, s_mu, s_var

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

        # Per-candidate timeline accumulator — one frozen (computed, planned)
        # step per measurement, captured below as the picker re-ranks.
        order_steps: list[SampleOrderStep] = []

        # Online 1PL Rasch adaptive queue mechanism — re-fits δ on every measurement, folds
        # outcomes into a running θ̂_c, picks argmax of decision-IG. Fold is from-scratch each step
        # (no (μ_c, var_c) persistence). Re-emits `hard_sample_order` so the webapp reorders on
        # every score.
        def _next_sample(
            scored_outcomes: dict[int, bool],
            _cid: str = osp_c.lineage.id,
            _idx: int = idx,
            _steps: list[SampleOrderStep] = order_steps,
        ) -> int | None:
            extra = [
                Observation(candidate_id=_cid, sample_id=sid, hit=hit)
                for sid, hit in scored_outcomes.items()
            ]
            d_map, d_se_map, p_var, s_mu, s_var = _fit_queue_mechanism(extra)
            # Fresh-mutation prior is seed θ_s, not 0. A centred-at-0 prior flattens decision-IG
            # and lets the explore term walk unmeasured blocks in sample-id order — folding from
            # θ_s gives the decision term real signal from pick 1.
            mu_c, var_c = posterior_from_outcomes(
                s_mu,
                p_var,
                (
                    (d_map.get(sid, 0.0), d_se_map.get(sid, 1.0), hit)
                    for sid, hit in scored_outcomes.items()
                ),
            )
            # Emit at the candidate's current posterior so webapp reorder + console preview match the actual pick.
            order = expected_order(mu_c, var_c, s_mu, s_var, d_map, d_se_map, dataset_sample_ids)
            preview = [
                (
                    sid,
                    float(pick_value(mu_c, var_c, s_mu, s_var, d_map[sid], d_se_map[sid])),
                )
                for sid in order
                if sid not in scored_outcomes
            ][:3]
            callbacks.on_sample_order_preview(
                round_num,
                _idx,
                n,
                preview,
                n_priors=len(elim_check.priors_by_sample),
                sample_order=order,
            )
            # Freeze this step: computed = measured-so-far (insertion = measurement
            # order), planned = the picker's intended order for the untouched tail.
            computed_ids = list(scored_outcomes.keys())
            planned_ids = [sid for sid in order if sid not in scored_outcomes]
            _steps.append(
                SampleOrderStep(
                    step=len(computed_ids),
                    current_sample_id=planned_ids[0] if planned_ids else None,
                    computed=computed_ids,
                    planned=planned_ids,
                )
            )
            remaining = set(dataset_sample_ids) - scored_outcomes.keys()
            return next_sample(mu_c, var_c, s_mu, s_var, d_map, d_se_map, remaining)

        # Universe for PoBB's dominance gate `cand_max_hits = cand_hits + remaining`. Online picking
        # consumes the full dataset; universe is unordered.
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
            # online_reorder off → no adaptive re-rank: fixed insertion order, no
            # live sample-order preview emitted (query_loop's None fallback).
            next_sample=_next_sample if online_reorder else None,
        )
        all_candidate_results[osp_c.lineage.id] = cr_result.results
        if order_steps:
            sample_order_timelines[osp_c.lineage.id] = order_steps
        # Fold outcomes into live obs so next candidate's queue mechanism + leaderboard see them.
        round_live_obs.extend(_filtered_obs({osp_c.lineage.id: cr_result.results}))
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

    # Representative timeline = the longest-surviving candidate's, matching the
    # `selection` rule in round_summary._measurement_order (longest list, tie-break
    # on candidate id) so the executed prefix lines up with the displayed order.
    representative_timeline: list[SampleOrderStep] = []
    if sample_order_timelines:
        rep_cid = max(
            sample_order_timelines,
            key=lambda cid: (len(all_candidate_results.get(cid, [])), cid),
        )
        representative_timeline = sample_order_timelines[rep_cid]

    return all_candidate_results, candidate_scores, escalation_signal, representative_timeline


__all__ = ["score_population"]
