"""Hard-sample-sorter — (candidate, sample, hit) → θ_c-ranked × δ_s-ranked matrix.

Standalone capability (no optimizer loop). Spec: `docs/specs/hard-sample-sorter.md`.
Pure read-only; reuses `build_observations` so error/missing-sample policy lives in one place.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from promptpotter.application.intelligence.adaptive_picker import pick_value
from promptpotter.application.intelligence.exploration import build_observations, fit_rasch

if TYPE_CHECKING:
    from promptpotter.application.intelligence.exploration import Observation, RaschPosterior
    from promptpotter.domain.results import RoundResult

__all__ = [
    "ARTIFACT_SCHEMA_VERSION",
    "build_hard_samples_artifact",
    "build_hard_samples_artifact_from_observations",
    "empty_artifact",
]

ARTIFACT_SCHEMA_VERSION = 3


def _candidate_hit_rates(observations: list[Observation]) -> dict[str, float]:
    """Mean hit rate per candidate over measured cells. Tie-breaker for θ_c."""
    totals: dict[str, int] = {}
    hits: dict[str, int] = {}
    for o in observations:
        totals[o.candidate_id] = totals.get(o.candidate_id, 0) + 1
        if o.hit:
            hits[o.candidate_id] = hits.get(o.candidate_id, 0) + 1
    return {cid: hits.get(cid, 0) / n for cid, n in totals.items() if n > 0}


def _sample_miss_rates(observations: list[Observation]) -> dict[int, float]:
    """Mean miss rate per sample over measured cells. Tie-breaker for δ_s."""
    totals: dict[int, int] = {}
    misses: dict[int, int] = {}
    for o in observations:
        totals[o.sample_id] = totals.get(o.sample_id, 0) + 1
        if not o.hit:
            misses[o.sample_id] = misses.get(o.sample_id, 0) + 1
    return {sid: misses.get(sid, 0) / n for sid, n in totals.items() if n > 0}


def _pick_score_under_prior(
    delta: dict[int, float],
    delta_se: dict[int, float],
    sigma_theta: float,
    seed_mu: float,
    seed_var: float,
) -> dict[int, float]:
    """Per-sample pick-value for a fresh mutation centred at θ_seed (not population-mean 0).
    Contested samples (δ near seed) score high; always-hit/always-miss score near 0. Same `pick_value`
    function the live picker calls — this is the round-boundary snapshot of that statistical model.
    """
    var_theta = sigma_theta * sigma_theta
    return {
        sid: pick_value(seed_mu, var_theta, seed_mu, seed_var, d, delta_se.get(sid, 0.0))
        for sid, d in delta.items()
    }


def _resolve_candidate_order(
    posterior: RaschPosterior,
    hit_rates: dict[str, float],
) -> list[str]:
    """Y-axis: desc θ_c; tie → desc mean hit rate; final tie → cid lex ascending."""
    return sorted(
        posterior.theta.keys(),
        key=lambda cid: (-posterior.theta[cid], -hit_rates.get(cid, 0.0), cid),
    )


def _resolve_sample_order(
    posterior: RaschPosterior,
    miss_rates: dict[int, float],
) -> list[int]:
    """X-axis: desc δ_s (hardest left); tie → desc miss rate; final tie → sid asc."""
    return sorted(
        posterior.delta.keys(),
        key=lambda sid: (-posterior.delta[sid], -miss_rates.get(sid, 0.0), sid),
    )


def _resolve_pick_order(pick_score: dict[int, float]) -> list[int]:
    """Desc pick-value, asc sid for determinism. Snapshot only — `adaptive_picker` re-ranks live."""
    return sorted(pick_score.keys(), key=lambda sid: (-pick_score[sid], sid))


def empty_artifact(
    *,
    cycle_id: str | None = None,
    disabled: bool = False,
) -> dict[str, Any]:
    """Schema-valid stub for zero-obs / disabled campaigns — keeps `CAMPAIGN_ARTIFACTS` parity
    without a special-case read path; renderers short-circuit on `n_observations == 0`.
    """
    return {
        "schema_version": ARTIFACT_SCHEMA_VERSION,
        "cycle_id": cycle_id,
        "generated_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "disabled": disabled,
        "truncated": False,
        "total_candidates": 0,
        "total_samples": 0,
        "n_candidates": 0,
        "n_samples": 0,
        "n_observations": 0,
        "candidate_order": [],
        "sample_order": [],
        "rasch": {},
        "pick_score": {"per_sample": {}, "sample_order": []},
        "cells": [],
    }


def build_hard_samples_artifact(
    rounds: list[RoundResult],
    *,
    cycle_id: str | None = None,
    top_k_candidates: int | None = 40,
    top_k_samples: int | None = 40,
    posterior: RaschPosterior | None = None,
) -> dict[str, Any]:
    """Thin wrapper — projects `rounds` to observations then delegates. Skips Rasch refit if
    *posterior* supplied. Pure.
    """
    return build_hard_samples_artifact_from_observations(
        build_observations(rounds),
        cycle_id=cycle_id,
        top_k_candidates=top_k_candidates,
        top_k_samples=top_k_samples,
        posterior=posterior,
    )


def build_hard_samples_artifact_from_observations(
    observations: list[Observation],
    *,
    cycle_id: str | None = None,
    top_k_candidates: int | None = 40,
    top_k_samples: int | None = 40,
    posterior: RaschPosterior | None = None,
) -> dict[str, Any]:
    """Pre-built-observations variant — used by `hard_sample_archive` (which builds obs from the
    MeasurementArchive). Fits Rasch if no *posterior* supplied, then truncates to top-K. Cells are
    restricted to (candidate_order × sample_order). `top_k_*=None` ⇒ full matrix. Pure.
    """
    if not observations:
        return empty_artifact(cycle_id=cycle_id)

    if posterior is None:
        posterior = fit_rasch(observations)
    hit_rates = _candidate_hit_rates(observations)
    miss_rates = _sample_miss_rates(observations)
    # Picker scores a fresh mutation against the seed (best fitted candidate); cold start uses
    # the centred population prior.
    if posterior.theta:
        best_cid = max(posterior.theta, key=lambda cid: posterior.theta[cid])
        seed_mu = posterior.theta[best_cid]
        seed_var = posterior.theta_se.get(best_cid, posterior.sigma_theta) ** 2
    else:
        seed_mu, seed_var = 0.0, posterior.sigma_theta**2
    pick_score_map = _pick_score_under_prior(
        posterior.delta,
        posterior.delta_se,
        posterior.sigma_theta,
        seed_mu,
        seed_var,
    )

    full_candidate_order = _resolve_candidate_order(posterior, hit_rates)
    full_sample_order = _resolve_sample_order(posterior, miss_rates)
    full_pick_order = _resolve_pick_order(pick_score_map)

    total_candidates = len(full_candidate_order)
    total_samples = len(full_sample_order)

    candidate_order = (
        full_candidate_order
        if top_k_candidates is None
        else full_candidate_order[:top_k_candidates]
    )
    sample_order = full_sample_order if top_k_samples is None else full_sample_order[:top_k_samples]
    pick_order = full_pick_order if top_k_samples is None else full_pick_order[:top_k_samples]

    cand_set = set(candidate_order)
    samp_set = set(sample_order)

    cells = [
        {"c": o.candidate_id, "s": int(o.sample_id), "hit": bool(o.hit)}
        for o in observations
        if o.candidate_id in cand_set and o.sample_id in samp_set
    ]

    rasch_view = {
        "converged": bool(posterior.converged),
        "iterations": int(posterior.n_iterations),
        # EB hyperparameters — population spreads + mean difficulty (shrinkage strength).
        "sigma_theta": float(posterior.sigma_theta),
        "sigma_delta": float(posterior.sigma_delta),
        "mu_delta": float(posterior.mu_delta),
        "theta": {cid: float(posterior.theta[cid]) for cid in candidate_order},
        "theta_se": {cid: float(posterior.theta_se.get(cid, 0.0)) for cid in candidate_order},
        # JSON object keys must be strings — consumers cast back to int.
        "delta": {str(sid): float(posterior.delta[sid]) for sid in sample_order},
        "delta_se": {str(sid): float(posterior.delta_se.get(sid, 0.0)) for sid in sample_order},
        "n_obs_per_candidate": {
            cid: int(posterior.n_obs_per_candidate.get(cid, 0)) for cid in candidate_order
        },
        "n_obs_per_sample": {
            str(sid): int(posterior.n_obs_per_sample.get(sid, 0)) for sid in sample_order
        },
    }

    truncated = len(candidate_order) < total_candidates or len(sample_order) < total_samples

    return {
        "schema_version": ARTIFACT_SCHEMA_VERSION,
        "cycle_id": cycle_id,
        "generated_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "disabled": False,
        "truncated": truncated,
        "total_candidates": total_candidates,
        "total_samples": total_samples,
        "n_candidates": len(candidate_order),
        "n_samples": len(sample_order),
        "n_observations": len(cells),
        "candidate_order": candidate_order,
        "sample_order": sample_order,
        "rasch": rasch_view,
        "pick_score": {
            "per_sample": {str(sid): float(pick_score_map.get(sid, 0.0)) for sid in sample_order},
            "sample_order": [int(sid) for sid in pick_order],
        },
        "cells": cells,
    }
