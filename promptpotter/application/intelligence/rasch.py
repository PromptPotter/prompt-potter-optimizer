"""Rasch model + Knowledge Gradient for adaptive sample selection.

Rasch joint MLE on a sparse ``(candidate_id, sample_id, hit)`` observation
matrix produces:
  - ``theta``: candidate ability (higher = better candidate)
  - ``delta``: sample difficulty (higher = harder sample)
  - Laplace standard errors on both — first-class uncertainty readout
    for hard-sample identification and swap decisions.

Knowledge Gradient (one-step lookahead, Bernoulli closed-form): how much
does a single new measurement at ``(c, s)`` shift our point estimate for
the best candidate? Drives swap-in selection.

Pure functions, numpy only, no I/O. Re-fit per round on the cumulative
observation table; cheap at our scale (~50 candidates × ~200 samples).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import NamedTuple

import numpy as np


class Observation(NamedTuple):
    """One ``(candidate, sample, hit)`` triple."""

    candidate_id: str
    sample_id: int
    hit: bool


@dataclass
class RaschPosterior:
    """MAP point estimates + Laplace standard errors for a fitted Rasch model.

    ``theta`` and ``delta`` are anchored to ``mean(theta) == 0`` for
    identifiability. SE arrays are derived from observed Fisher information
    plus the prior precision contribution.
    """

    theta: dict[str, float]
    theta_se: dict[str, float]
    delta: dict[int, float]
    delta_se: dict[int, float]
    n_obs_per_candidate: dict[str, int] = field(default_factory=dict)
    n_obs_per_sample: dict[int, int] = field(default_factory=dict)
    n_iterations: int = 0
    converged: bool = False

    def predict_hit(self, candidate_id: str, sample_id: int) -> float:
        """Posterior point-estimate of P(hit) at ``(c, s)``. Defaults to 0.5 when unknown."""
        t = self.theta.get(candidate_id, 0.0)
        d = self.delta.get(sample_id, 0.0)
        return _sigmoid(t - d)


def _sigmoid(x: float) -> float:
    if x >= 0:
        ex = np.exp(-x)
        return float(1.0 / (1.0 + ex))
    ex = np.exp(x)
    return float(ex / (1.0 + ex))


def fit_rasch(
    observations: list[Observation],
    *,
    theta_prior_mean: dict[str, float] | None = None,
    theta_prior_sigma: float = 1.5,
    delta_prior_sigma: float = 2.0,
    max_iter: int = 50,
    tol: float = 1e-4,
) -> RaschPosterior:
    """Fit Rasch by alternating Newton MAP. Returns full posterior summary.

    ``theta_prior_mean`` is the structural-kernel cold-start prior
    on candidate ability. Candidates absent from the dict get prior mean 0.
    The prior is ``N(mean, sigma²)``; sigma ``inf`` disables regularization
    for that parameter.
    """
    if not observations:
        return RaschPosterior(theta={}, theta_se={}, delta={}, delta_se={})

    candidate_ids = sorted({o.candidate_id for o in observations})
    sample_ids = sorted({o.sample_id for o in observations})
    c_idx = {cid: i for i, cid in enumerate(candidate_ids)}
    s_idx = {sid: j for j, sid in enumerate(sample_ids)}

    n_c = len(candidate_ids)
    n_s = len(sample_ids)
    theta = np.zeros(n_c)
    delta = np.zeros(n_s)

    # Prior mean on theta — defaults to zero unless caller passes structural prior.
    prior_theta = np.zeros(n_c)
    if theta_prior_mean:
        for cid, mu in theta_prior_mean.items():
            if cid in c_idx:
                prior_theta[c_idx[cid]] = mu

    # Build sparse observation arrays for vectorized updates.
    rows = np.fromiter((c_idx[o.candidate_id] for o in observations), dtype=np.int64)
    cols = np.fromiter((s_idx[o.sample_id] for o in observations), dtype=np.int64)
    hits = np.fromiter((1.0 if o.hit else 0.0 for o in observations), dtype=np.float64)

    inv_var_theta = 1.0 / (theta_prior_sigma * theta_prior_sigma)
    inv_var_delta = 1.0 / (delta_prior_sigma * delta_prior_sigma)

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

        # Theta update: Newton step per candidate.
        grad_theta = np.bincount(rows, weights=hits - p, minlength=n_c) - inv_var_theta * (
            theta - prior_theta
        )
        info_theta = np.bincount(rows, weights=w, minlength=n_c) + inv_var_theta
        theta = theta + grad_theta / np.maximum(info_theta, 1e-9)

        # Recompute p/w for delta step (theta moved).
        eta = theta[rows] - delta[cols]
        p = 1.0 / (1.0 + np.exp(-np.clip(eta, -50, 50)))
        w = p * (1.0 - p)

        # Delta update: Newton step per sample (gradient sign flipped).
        grad_delta = -np.bincount(cols, weights=hits - p, minlength=n_s) - inv_var_delta * delta
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
    )


def knowledge_gradient(
    posterior: RaschPosterior,
    candidate_id: str,
    sample_id: int,
    *,
    theta_prior_sigma: float = 1.5,
) -> float:
    """One-step KG for measuring ``(candidate, sample)``.

    Closed-form for Bernoulli observation under the Laplace approximation.
    Returns the expected absolute shift in this candidate's ``theta``
    estimate, weighted by the predicted outcome probability. Larger means
    "this measurement would move our belief about candidate's ability
    more, in expectation".

    Returns 0 when the pair is already heavily measured (info dominates
    the new observation) or when the candidate / sample is unknown.
    """
    t = posterior.theta.get(candidate_id)
    d = posterior.delta.get(sample_id)
    if t is None or d is None:
        return 0.0

    p = _sigmoid(t - d)
    w = p * (1.0 - p)
    n_obs = posterior.n_obs_per_candidate.get(candidate_id, 0)

    # Posterior info on theta_c is approximately n_obs * mean_w + 1/sigma_prior^2;
    # use the candidate's actual SE to back out the current info budget.
    se = posterior.theta_se.get(candidate_id, 1.0)
    info_current = 1.0 / max(se * se, 1e-9)
    info_new = info_current + w  # one more observation contributes w to Fisher info

    # Newton step delta for an observation outcome y ∈ {0, 1}:
    # Δθ = (y - p) / info_new
    delta_if_hit = (1.0 - p) / max(info_new, 1e-9)
    delta_if_miss = (0.0 - p) / max(info_new, 1e-9)

    # Expected absolute shift in posterior mean of theta_c.
    expected_shift = p * abs(delta_if_hit) + (1.0 - p) * abs(delta_if_miss)

    # Already-measured pairs: KG should be discounted (returns ≈ 0 when n_obs >> 1).
    # The 1/info_new factor handles this naturally; no extra term needed.
    _ = n_obs, theta_prior_sigma  # parameters reserved for future variance-aware KG variant
    return float(expected_shift)


def sample_kg_max(
    posterior: RaschPosterior,
    sample_id: int,
    candidate_ids: list[str],
) -> float:
    """Per-sample KG = ``max_c KG(c, s)`` over a candidate set.

    The "any high-value pair justifies measuring this sample" interpretation.
    Used by the swap-in ranker.
    """
    if not candidate_ids:
        return 0.0
    return max(knowledge_gradient(posterior, cid, sample_id) for cid in candidate_ids)


def confidence_interval_width(se: float, z: float = 1.96) -> float:
    """Two-sided CI width: ``2 * z * se``. Default z=1.96 → 95% CI."""
    return 2.0 * z * se
