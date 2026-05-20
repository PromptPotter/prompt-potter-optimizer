"""Rasch IRT primitives + per-round scoring-subset selection.

Two related concerns share this module:

1. **Rasch model** — fitting the IRT posterior (``fit_rasch`` /
   ``RaschPosterior``) over ``(candidate, sample, hit)`` observations.
   Pure-math primitives, ``mean(theta) == 0`` anchored for identifiability.

2. **Per-round subset selection** (``select_round_subset``) — each round,
   pick the ``sp_budget_ttest`` most-informative samples out of the full
   bank via the CAT picker's Fisher-information objective at the leading
   candidate's estimated ability. This is what decouples the bank (the
   full train split) from the per-round eval budget.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, NamedTuple

import numpy as np

from promptpotter.application.intelligence.adaptive_picker import expected_order_mfi

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


def fit_rasch(
    observations: list[Observation],
    *,
    theta_prior_sigma: float = 1.5,
    delta_prior_sigma: float = 2.0,
    max_iter: int = 50,
    tol: float = 1e-4,
) -> RaschPosterior:
    """Fit Rasch by alternating Newton MAP. Returns full posterior summary.

    Priors: ``theta ~ N(0, theta_prior_sigma²)``, ``delta ~ N(0, delta_prior_sigma²)``.
    Sigma ``inf`` disables regularization for that parameter.
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
        grad_theta = np.bincount(rows, weights=hits - p, minlength=n_c) - inv_var_theta * theta
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
) -> list[Sample]:
    """Pick the ``budget`` most-informative samples from ``bank`` for one round.

    Fits Rasch on ``observations``, estimates the leading candidate's
    ability as ``max(theta)``, and ranks the bank by Fisher information at
    that ability — the samples whose outcome is least certain for the best
    prompt so far, i.e. the contested band where a mutation can still flip
    a miss into a hit. Cold start (no observations, hence no δ estimates)
    falls back to the bank-order prefix, byte-identical to the
    pre-decoupling ``dataset[:budget]`` slice. Pure: no I/O.
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
    delta_map = {int(sid): float(d) for sid, d in posterior.delta.items()}
    leader_theta = max(posterior.theta.values()) if posterior.theta else 0.0
    by_id = {int(s.id): s for s in bank}
    ranked = expected_order_mfi(leader_theta, delta_map, list(by_id))
    return [by_id[sid] for sid in ranked[:budget]]
