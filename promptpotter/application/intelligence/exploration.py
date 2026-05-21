"""Rasch IRT primitives + per-round scoring-subset selection.

Two related concerns share this module:

1. **Rasch model** — fitting the IRT posterior (``fit_rasch`` /
   ``RaschPosterior``) over ``(candidate, sample, hit)`` observations.
   Pure-math primitives, ``mean(theta) == 0`` anchored for identifiability.

2. **Per-round subset selection** (``select_round_subset``) — each round,
   pick the ``sp_budget_ttest`` most-informative samples out of the full
   bank via the CAT picker's blended objective: a fresh mutation scored
   against the leading candidate. This is what decouples the bank (the
   full train split) from the per-round eval budget.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, NamedTuple

import numpy as np

from promptpotter.application.intelligence.adaptive_picker import expected_order

if TYPE_CHECKING:
    from promptpotter.domain.results import RoundResult
    from promptpotter.domain.sample import Sample

__all__ = [
    "Observation",
    "RaschPosterior",
    "build_observations",
    "fit_rasch",
    "select_round_subset",
]


class Observation(NamedTuple):
    """One ``(candidate, sample, hit)`` triple."""

    candidate_id: str
    sample_id: int
    hit: bool


# Empirical-Bayes starting point for the hyperparameters. Broad priors so the
# first inner MAP fit is barely regularized; the EB loop walks them to the
# Type-II MLE from there. Also the value the hyperparameters report on an
# empty observation set.
_INIT_SIGMA_THETA = 1.5
_INIT_SIGMA_DELTA = 2.0

# Weak conjugate (inverse-gamma) hyperprior on each variance component:
# ``_EB_NU0`` pseudo-samples pulling the variance toward scale ``_EB_S0_SQ``.
# This is a prior, not a tuning knob — it washes out against the real sample
# count and stops the marginal likelihood collapsing σ → 0 when the data is
# too sparse to identify a spread (Rasch-validation degeneracy).
_EB_NU0 = 1.0
_EB_S0_SQ = 1.0


@dataclass
class RaschPosterior:
    """MAP point estimates + Laplace standard errors for a fitted Rasch model.

    ``theta`` and ``delta`` are anchored to ``mean(theta) == 0`` for
    identifiability. SE arrays are derived from observed Fisher information
    plus the prior precision contribution.

    ``sigma_theta`` / ``sigma_delta`` / ``mu_delta`` are the hierarchical
    hyperparameters — the candidate-ability and sample-difficulty population
    spreads and the mean difficulty — estimated by empirical Bayes inside
    :func:`fit_rasch`, not configured. Every downstream consumer (picker,
    sorter) reads the shrinkage strength from these instead of a hardcoded
    constant.
    """

    theta: dict[str, float]
    theta_se: dict[str, float]
    delta: dict[int, float]
    delta_se: dict[int, float]
    n_obs_per_candidate: dict[str, int] = field(default_factory=dict)
    n_obs_per_sample: dict[int, int] = field(default_factory=dict)
    n_iterations: int = 0
    converged: bool = False
    sigma_theta: float = _INIT_SIGMA_THETA
    sigma_delta: float = _INIT_SIGMA_DELTA
    mu_delta: float = 0.0


def _map_fit(
    rows: np.ndarray,
    cols: np.ndarray,
    hits: np.ndarray,
    n_c: int,
    n_s: int,
    sigma_theta: float,
    sigma_delta: float,
    mu_delta: float,
    max_iter: int,
    tol: float,
) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray, int, bool]:
    """One alternating-Newton MAP fit at fixed hyperparameters.

    Priors: ``theta ~ N(0, sigma_theta²)``, ``delta ~ N(mu_delta, sigma_delta²)``.
    Returns ``(theta, delta, se_theta, se_delta, n_iterations, converged)`` —
    point estimates anchored to ``mean(theta) == 0`` plus Laplace SEs from the
    observed Fisher information.
    """
    theta = np.zeros(n_c)
    delta = np.full(n_s, mu_delta)

    inv_var_theta = 1.0 / (sigma_theta * sigma_theta)
    inv_var_delta = 1.0 / (sigma_delta * sigma_delta)

    converged = False
    iteration = 0
    for it in range(1, max_iter + 1):
        iteration = it
        old_theta = theta.copy()
        old_delta = delta.copy()

        # Predicted probabilities and weights for each observation.
        eta = theta[rows] - delta[cols]
        p = 1.0 / (1.0 + np.exp(-np.clip(eta, -50, 50)))
        w = p * (1.0 - p)

        # Theta update: Newton step per candidate (prior N(0, σ_θ²)).
        grad_theta = np.bincount(rows, weights=hits - p, minlength=n_c) - inv_var_theta * theta
        info_theta = np.bincount(rows, weights=w, minlength=n_c) + inv_var_theta
        theta = theta + grad_theta / np.maximum(info_theta, 1e-9)

        # Recompute p/w for delta step (theta moved).
        eta = theta[rows] - delta[cols]
        p = 1.0 / (1.0 + np.exp(-np.clip(eta, -50, 50)))
        w = p * (1.0 - p)

        # Delta update: Newton step per sample (prior N(μ_δ, σ_δ²); gradient
        # sign flipped because the likelihood depends on θ_c − δ_s).
        grad_delta = -np.bincount(cols, weights=hits - p, minlength=n_s) - inv_var_delta * (
            delta - mu_delta
        )
        info_delta = np.bincount(cols, weights=w, minlength=n_s) + inv_var_delta
        delta = delta + grad_delta / np.maximum(info_delta, 1e-9)

        # Anchor for identifiability: mean(theta) == 0.
        shift = float(theta.mean())
        theta -= shift
        delta -= shift

        max_change = max(
            float(np.max(np.abs(theta - old_theta))) if theta.size else 0.0,
            float(np.max(np.abs(delta - old_delta))) if delta.size else 0.0,
        )
        if max_change < tol:
            converged = True
            break

    # Final Fisher info for SEs.
    eta = theta[rows] - delta[cols]
    p = 1.0 / (1.0 + np.exp(-np.clip(eta, -50, 50)))
    w = p * (1.0 - p)
    info_theta = np.bincount(rows, weights=w, minlength=n_c) + inv_var_theta
    info_delta = np.bincount(cols, weights=w, minlength=n_s) + inv_var_delta
    se_theta = 1.0 / np.sqrt(np.maximum(info_theta, 1e-9))
    se_delta = 1.0 / np.sqrt(np.maximum(info_delta, 1e-9))
    return theta, delta, se_theta, se_delta, iteration, converged


def fit_rasch(
    observations: list[Observation],
    *,
    max_iter: int = 50,
    tol: float = 1e-4,
    eb_max_iter: int = 20,
    eb_tol: float = 1e-3,
) -> RaschPosterior:
    """Fit a hierarchical 1PL Rasch model by empirical Bayes.

    Likelihood ``P(hit | c, s) = σ(θ_c − δ_s)``; hierarchical priors
    ``θ_c ~ N(0, σ_θ²)`` and ``δ_s ~ N(μ_δ, σ_δ²)``. The population
    hyperparameters ``η = (σ_θ, σ_δ, μ_δ)`` are **estimated, not configured**:
    an outer Laplace-EM loop alternates an inner MAP fit (the E-step — the
    Laplace posterior, point estimates + SEs) with a marginal-likelihood
    M-step on the variance components. This is Type-II MLE; it converges to a
    local maximum of ``p(data | η)``.

    The M-step carries a weak conjugate hyperprior (:data:`_EB_NU0` /
    :data:`_EB_S0_SQ`) so the variance components stay identified when the
    data is too sparse to pin a spread. Shrinkage strength is therefore
    data-determined: a barely-measured sample is pulled toward ``μ_δ`` by
    exactly the fraction the rest of the sample population warrants.
    """
    if not observations:
        return RaschPosterior(theta={}, theta_se={}, delta={}, delta_se={})

    candidate_ids = sorted({o.candidate_id for o in observations})
    sample_ids = sorted({o.sample_id for o in observations})
    c_idx = {cid: i for i, cid in enumerate(candidate_ids)}
    s_idx = {sid: j for j, sid in enumerate(sample_ids)}

    n_c = len(candidate_ids)
    n_s = len(sample_ids)

    # Build sparse observation arrays once — shared across every inner fit.
    rows = np.fromiter((c_idx[o.candidate_id] for o in observations), dtype=np.int64)
    cols = np.fromiter((s_idx[o.sample_id] for o in observations), dtype=np.int64)
    hits = np.fromiter((1.0 if o.hit else 0.0 for o in observations), dtype=np.float64)

    # Empirical-Bayes outer loop. Each pass: inner MAP fit at the current
    # hyperparameters, then an EM variance-components M-step. ``θ`` is anchored
    # to mean 0 so its population mean is fixed; ``μ_δ`` is free.
    sigma_theta, sigma_delta, mu_delta = _INIT_SIGMA_THETA, _INIT_SIGMA_DELTA, 0.0
    theta = delta = se_theta = se_delta = np.empty(0)
    iteration = 0
    converged = False
    for _ in range(eb_max_iter):
        theta, delta, se_theta, se_delta, iteration, converged = _map_fit(
            rows, cols, hits, n_c, n_s, sigma_theta, sigma_delta, mu_delta, max_iter, tol
        )
        # M-step: the EM update for a Gaussian variance component is the
        # posterior second moment of the latent parameter — point-estimate
        # spread plus posterior variance — regularized by the hyperprior.
        new_mu_delta = float(delta.mean())
        new_var_theta = (
            float(np.sum(theta * theta + se_theta * se_theta)) + _EB_NU0 * _EB_S0_SQ
        ) / (n_c + _EB_NU0)
        d_centered = delta - new_mu_delta
        new_var_delta = (
            float(np.sum(d_centered * d_centered + se_delta * se_delta)) + _EB_NU0 * _EB_S0_SQ
        ) / (n_s + _EB_NU0)
        new_sigma_theta = float(np.sqrt(new_var_theta))
        new_sigma_delta = float(np.sqrt(new_var_delta))

        change = max(
            abs(new_sigma_theta - sigma_theta),
            abs(new_sigma_delta - sigma_delta),
            abs(new_mu_delta - mu_delta),
        )
        sigma_theta, sigma_delta, mu_delta = new_sigma_theta, new_sigma_delta, new_mu_delta
        if change < eb_tol:
            break

    # One final inner fit so the returned posterior is consistent with the
    # converged hyperparameters (the loop's last fit used the prior pass's).
    theta, delta, se_theta, se_delta, iteration, converged = _map_fit(
        rows, cols, hits, n_c, n_s, sigma_theta, sigma_delta, mu_delta, max_iter, tol
    )

    n_obs_c = dict(
        zip(candidate_ids, np.bincount(rows, minlength=n_c).astype(int).tolist(), strict=True)
    )
    n_obs_s = dict(
        zip(sample_ids, np.bincount(cols, minlength=n_s).astype(int).tolist(), strict=True)
    )

    return RaschPosterior(
        theta=dict(zip(candidate_ids, theta.tolist(), strict=True)),
        theta_se=dict(zip(candidate_ids, se_theta.tolist(), strict=True)),
        delta=dict(zip(sample_ids, delta.tolist(), strict=True)),
        delta_se=dict(zip(sample_ids, se_delta.tolist(), strict=True)),
        n_obs_per_candidate=n_obs_c,
        n_obs_per_sample=n_obs_s,
        n_iterations=iteration,
        converged=converged,
        sigma_theta=sigma_theta,
        sigma_delta=sigma_delta,
        mu_delta=mu_delta,
    )


def build_observations(rounds: list[RoundResult]) -> list[Observation]:
    """Flatten ``cycle.rounds`` into the ``(candidate, sample, hit)`` triples Rasch needs.

    Skips items missing ``sample_id`` (older traces predate the field) or
    flagged as errors via the QueryMeasurement contract.
    """
    obs: list[Observation] = []
    for rr in rounds:
        for cid, results in rr.all_candidate_results.items():
            for r in results:
                sid = r.get("sample_id")
                if sid is None:
                    continue
                # Errors and synthetic abort-padding rows shouldn't drive Rasch.
                if r.get("error"):
                    continue
                obs.append(
                    Observation(candidate_id=cid, sample_id=int(sid), hit=bool(r.get("hit")))
                )
    return obs


def select_round_subset(
    bank: list[Sample],
    observations: list[Observation],
    budget: int,
    explore_weight: float,
) -> list[Sample]:
    """Pick the ``budget`` most-informative samples from ``bank`` for one round.

    Fits Rasch on ``observations``, then ranks the bank by the CAT picker's
    blended objective for a fresh mutation of the leading candidate. The
    mutation's ability prior is ``N(θ_leader, σ_θ²)`` — a mutation is a small
    edit of its parent, so it starts at the parent's ability, not the
    population-mean anchor ``0``. Centred there, the decision term peaks on
    the contested band — measured samples whose difficulty sits at the
    leader's ability, where a mutation can still flip the verdict — with a
    small ``explore_weight`` pull toward poorly-characterized samples. A
    centred-at-0 prior would instead make the decision term flat and let the
    explore term sweep up fresh unmeasured blocks. Cold start (no
    observations, hence no δ estimates) falls back to the bank-order prefix,
    byte-identical to the pre-decoupling ``dataset[:budget]`` slice. Pure: no I/O.
    """
    if budget <= 0 or not bank:
        return []
    if budget >= len(bank):
        return list(bank)
    if not observations:
        return list(bank[:budget])
    posterior = fit_rasch(observations)
    if not posterior.delta:
        return list(bank[:budget])
    by_id = {int(s.id): s for s in bank}
    # Complete maps over the bank: measured samples carry their fitted
    # posterior, unmeasured ones the estimated population prior (μ_δ, σ_δ) —
    # so an as-yet-unseen sample reads as maximally informative.
    delta_map = {sid: posterior.delta.get(sid, posterior.mu_delta) for sid in by_id}
    delta_se_map = {sid: posterior.delta_se.get(sid, posterior.sigma_delta) for sid in by_id}
    if posterior.theta:
        leader_id = max(posterior.theta, key=lambda cid: posterior.theta[cid])
        leader_theta = posterior.theta[leader_id]
        leader_var = posterior.theta_se.get(leader_id, posterior.sigma_theta) ** 2
    else:
        leader_theta, leader_var = 0.0, posterior.sigma_theta**2
    ranked = expected_order(
        leader_theta,
        posterior.sigma_theta**2,
        leader_theta,
        leader_var,
        delta_map,
        delta_se_map,
        list(by_id),
        explore_weight,
    )
    return [by_id[sid] for sid in ranked[:budget]]
