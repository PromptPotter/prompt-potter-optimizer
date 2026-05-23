"""Per-population scoring loop — :func:`score_population`.

Owns the per-candidate loop, the shared accumulators, the online Rasch CAT
picker plumbing, and the ESCALATED break condition. The per-candidate body
lives in :func:`score_one_candidate`.
"""

from __future__ import annotations

from functools import partial
from typing import TYPE_CHECKING, Any, cast

from promptpotter.application.intelligence.adaptive_picker import (
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
from promptpotter.application.optimization.pobb.elimination import PoBBCheck, PoBBConfig
from promptpotter.application.optimization.resume_and_fork import ResumeCheckpointRecord
from promptpotter.application.scoring.search_point_scorer import score_search_point
from promptpotter.domain.escalation_signals import EscalationSignal
from promptpotter.domain.opt_search_point import OptSearchPoint
from promptpotter.domain.results import CandidateProposal, CandidateScore
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
    merged_pp: list[dict[str, Any] | None],
    proposals: list[CandidateProposal],
    dataset: list[Sample],
    *,
    degradation_checks: list[StopRule] | None = None,
    callbacks: RunCallbacks,
    pobb_config: PoBBConfig,
    round_num: int = 0,
    decisions: list[ResumeCheckpointRecord] | None = None,
    l1_diversity: float = 1.0,
) -> tuple[dict[str, list[QueryMeasurement]], list[CandidateScore], EscalationSignal | None]:
    """Score each individual; dispatch over three exit paths (validation/cache/scored).

    Per-candidate body lives in ``score_one_candidate``; this function owns
    the loop, the shared accumulators (``candidate_scores``, ``decisions``,
    ``all_candidate_results``), and the ESCALATED break condition."""
    session = cycle.session
    obs = session.state.obs
    n = len(population)

    all_candidate_results: dict[str, list[QueryMeasurement]] = {}
    candidate_scores: list[CandidateScore] = []
    escalation_signal: EscalationSignal | None = None

    async def _pobb_backfill(sp: JobSearchPoint, samples: list[Sample]) -> list[QueryMeasurement]:
        """Score *sp* on *samples* and return the measurements.

        Used by PoBBCheck to fill in missing (prior, sample) pairs so paired
        comparison can run on identical sample sets. ``degradation_checks=None``
        so the backfill cannot recursively trip PoBB on its own measurements.

        Per-sample callbacks ride the same callback objects the main loop
        uses, with ``candidate_idx=-1`` as the "this is a backfill row"
        sentinel so the display layer can prefix the row distinctively and
        skip the main-loop counter. Without this the operator sees only the
        opaque "deprecated retry: resuming" stderr lines while the backfill
        burns LLM credits in silence.
        """
        bf_results, _bf_scores, _was_cached, _signal = await score_search_point(
            sp,
            samples,
            cycle.session,
            label="pobb_backfill",
            degradation_checks=None,
            candidate_idx=-1,
            n_total_candidates=0,
            axes=cycle.axes,
            l1_diversity=0.0,
            on_sample_scored=partial(callbacks.on_sample_scored, -1, 0),
            on_sample_starting=partial(callbacks.on_sample_started, -1, 0),
        )
        return bf_results

    elim_check = PoBBCheck(
        pobb_config,
        n_samples=len(dataset),
        round_num=round_num,
        backfill_fn=_pobb_backfill,
    )

    # Seed PoBB priors so candidate #1 of every round has a comparator
    # to lose against. Without this, ``PoBBCheck.check()`` short-circuits
    # on empty priors and the mechanism never fires until candidate #2
    # — round 1 candidate #1 was un-eliminable. ``current_results`` is
    # the round's "best-so-far" per-sample fitness history (campaign
    # origin in round 1, prior round winner in round 2+). The seed SP
    # is ``tracking.current_sp`` so the leader is backfill-able on the
    # candidate's hard samples when needed.
    seed_results = cycle.tracking.current_results
    seed_sp = cycle.tracking.current_sp
    if seed_results and seed_sp is not None:
        seed_id = f"R{cycle.rounds[-1].round}_winner" if cycle.rounds else "origin"
        elim_check.register_completed(
            cast("list[QueryMeasurement]", seed_results), candidate_id=seed_id, sp=seed_sp
        )

    # Hoisted picker plumbing — shared across the per-candidate loop.
    #
    # Fix B — undermeasured filter: candidates with fewer than
    # n_samples // 2 measurements (i.e. ones the prior PoBB iteration
    # aborted early) contribute observations only on the picker's
    # discriminating samples. Including them biases the Rasch fit on
    # those samples — the seed prior (registered separately) is full,
    # so dropping early-abort residue from ``sorter_obs`` is safe.
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
    # Archive observations come from completed scored runs (no mid-run abort
    # residue) and are dataset-scoped, so the picker's δ posterior carries
    # every cross-cycle measurement, not just this cycle's rounds.
    round_boundary_obs: list[Observation] = list(cycle.archive_observations) + prior_round_obs

    explore_weight = cycle.config.optimization.exploration.explore_weight
    dataset_sample_ids = [int(s.id) for s in dataset]

    def _picker_maps(
        posterior: RaschPosterior,
    ) -> tuple[dict[int, float], dict[int, float]]:
        """Complete (δ̂_s, se_δ_s) maps over every dataset sample.

        Measured samples carry their fitted posterior; unmeasured ones the
        estimated population prior (μ_δ, σ_δ) — so an as-yet-unseen sample
        reads as maximally informative rather than as δ = 0 / se = 0.
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
        """Fold the seed's per-sample outcomes into a θ_s posterior (μ_s, var_s).

        The picker's decision term scores each sample's information gain
        against the keep/abort verdict ``θ_c > θ_s`` — this is that seed.
        Returns the centred prior ``(0, σ_θ²)`` when the seed has no
        measurements.
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

    # Live picker observations. The Rasch δ leaderboard is re-fit on EVERY
    # measurement over the archive + prior rounds + this round's completed
    # candidates (``round_live_obs``) + the in-flight candidate's outcomes,
    # so the picker and the webapp hard-samples table track every score in
    # real time instead of freezing at the round boundary.
    round_live_obs: list[Observation] = []

    def _fit_picker(
        extra_obs: list[Observation],
    ) -> tuple[dict[int, float], dict[int, float], float, float, float]:
        """Re-fit the Rasch picker over every observation gathered so far.

        Returns ``(delta_map, delta_se_map, prior_var, seed_mu, seed_var)``.
        ``extra_obs`` is the in-flight candidate's outcomes — not yet folded
        into ``round_live_obs``. ``fit_rasch`` is pure numpy over a
        dataset-scoped observation set, cheap enough to run per measurement.
        """
        posterior = fit_rasch(round_boundary_obs + round_live_obs + extra_obs)
        d_map, d_se_map = _picker_maps(posterior)
        p_var = posterior.sigma_theta**2
        s_mu, s_var = _seed_posterior(d_map, d_se_map, p_var)
        return d_map, d_se_map, p_var, s_mu, s_var

    for idx, osp_c in enumerate(population):
        pipeline_params_override = proposals[idx].pipeline_params_override or None
        callbacks.on_candidate_started(
            idx, n, osp_c.lineage.changes_description or "", pipeline_params_override
        )
        # Bind the PoBBCheck to this candidate so its per-sample snapshot
        # lands on the live telemetry stream tagged with the right id.
        elim_check.set_current(
            osp_c.lineage.id,
            on_snapshot=partial(callbacks.on_p_best_update, round_num, idx, n),
        )

        # Online 1PL Rasch CAT picker — re-fits the δ leaderboard on every
        # measurement (``_fit_picker``), folds the candidate's outcomes into
        # a running θ̂_c posterior, and picks argmax of the blended objective
        # (decision-information-gain against the seed + the small explore
        # term). Re-emits ``hard_sample_order`` each call so the webapp
        # hard-samples table reorders on every score. Callers don't persist
        # (μ_c, var_c) across calls — the fold is from scratch each step.
        def _next_sample(
            scored_outcomes: dict[int, bool],
            _cid: str = osp_c.lineage.id,
            _idx: int = idx,
        ) -> int | None:
            extra = [
                Observation(candidate_id=_cid, sample_id=sid, hit=hit)
                for sid, hit in scored_outcomes.items()
            ]
            d_map, d_se_map, p_var, s_mu, s_var = _fit_picker(extra)
            # A fresh mutation is a small edit of its parent — its ability
            # prior is the seed's ability θ_s, not the population-mean anchor
            # 0. Fold the candidate's outcomes from there so the decision
            # term has real signal from the first pick: a centred-at-0 prior
            # makes decision-IG flat and lets the explore term walk fresh
            # unmeasured blocks in sample-id order.
            mu_c, var_c = posterior_from_outcomes(
                s_mu,
                p_var,
                (
                    (d_map.get(sid, 0.0), d_se_map.get(sid, 1.0), hit)
                    for sid, hit in scored_outcomes.items()
                ),
            )
            # Re-emit the live leaderboard at the candidate's *current*
            # posterior so the webapp hard-samples table reorders on every
            # score and the console preview matches the pick actually made.
            order = expected_order(
                mu_c, var_c, s_mu, s_var, d_map, d_se_map, dataset_sample_ids, explore_weight
            )
            preview = [
                (
                    sid,
                    float(
                        pick_value(
                            mu_c, var_c, s_mu, s_var, d_map[sid], d_se_map[sid], explore_weight
                        )
                    ),
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
            remaining = set(dataset_sample_ids) - scored_outcomes.keys()
            return next_sample(mu_c, var_c, s_mu, s_var, d_map, d_se_map, remaining, explore_weight)

        # Bind the candidate's sample budget so PoBB's dominance gate
        # can compute ``cand_max_hits = cand_hits + remaining`` against
        # the seed prior's total hits. Online picking means the candidate
        # consumes the full dataset; the universe is unordered.
        elim_check.set_sample_universe(dataset_sample_ids)

        cr_result = await score_one_candidate(
            idx=idx,
            osp_c=osp_c,
            pipeline_params_override=pipeline_params_override,
            cycle=cycle,
            dataset=dataset,
            n_total=n,
            merged_pp=merged_pp[idx],
            elim_check=elim_check,
            callbacks=callbacks,
            degradation_checks=degradation_checks,
            decisions=decisions,
            candidate_scores=candidate_scores,
            round_num=round_num,
            l1_diversity=l1_diversity,
            next_sample=_next_sample,
        )
        all_candidate_results[osp_c.lineage.id] = cr_result.results
        # Fold this candidate's outcomes into the live picker observations so
        # the next candidate's picker — and the leaderboard — see them.
        round_live_obs.extend(_filtered_obs({osp_c.lineage.id: cr_result.results}))
        if cr_result.runtime_failure is not None:
            osp_c.wounds.runtime_failures = [
                *osp_c.wounds.runtime_failures,
                cr_result.runtime_failure,
            ]
        candidate_scores.append(cr_result.report)
        callbacks.on_candidate_scored(idx, n, cr_result.report.to_dict())
        if obs:
            with graceful("CandidateScored emit failed"):
                obs.emit_write_point(
                    CandidateScored,
                    campaign_id=session.state.tracing_campaign_id,
                    round_num=round_num,
                    candidate_idx=idx,
                    report=cr_result.report.to_dict(),
                )

        if cr_result.outcome == CandidateOutcome.ESCALATED:
            escalation_signal = cr_result.escalation_signal
            break  # true degradation — abort remaining candidates

    return all_candidate_results, candidate_scores, escalation_signal


__all__ = ["score_population"]
