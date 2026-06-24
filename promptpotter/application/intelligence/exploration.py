"""Rasch IRT primitives + per-round scoring-subset selection via the adaptive queue mechanism."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, NamedTuple

import numpy as np

from promptpotter.application.intelligence.adaptive_queue_mechanism import expected_order
from promptpotter.shared.errors import is_error_result

if TYPE_CHECKING:
    from promptpotter.domain.results import RoundResult
    from promptpotter.domain.sample import Sample
    from promptpotter.domain.scoring import QueryMeasurement

# Sentinel candidate id the origin rides under inside a joint ability fit, so the
# election can read its θ on the same scale as the real candidates.
ORIGIN_ABILITY_ID = "__origin__"

__all__ = [
    "ORIGIN_ABILITY_ID",
    "Observation",
    "RaschPosterior",
    "build_observations",
    "candidate_abilities",
    "fit_rasch",
    "fit_theta_given_delta",
    "select_round_subset",
]


class Observation(NamedTuple):
    """One ``(candidate, sample, hit)`` triple."""

    candidate_id: str
    sample_id: int
    hit: bool


# Broad EB starting priors — first inner MAP fit is barely regularized.
_INIT_SIGMA_THETA = 1.5
_INIT_SIGMA_DELTA = 2.0

# Weak inverse-gamma hyperprior on each variance — stops σ → 0 collapse under
# sparse data; washes out against real n.
_EB_NU0 = 1.0
_EB_S0_SQ = 1.0


@dataclass
class RaschPosterior:
    """MAP + Laplace-SE for a hierarchical Rasch fit. ``mean(theta) == 0``; ``sigma_*`` / ``mu_delta`` are EB-estimated."""

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
    """Alternating-Newton MAP at fixed hyperparameters. Priors ``θ ~ N(0, σ_θ²)``, ``δ ~ N(μ_δ, σ_δ²)``."""
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

        eta = theta[rows] - delta[cols]
        p = 1.0 / (1.0 + np.exp(-np.clip(eta, -50, 50)))
        w = p * (1.0 - p)

        # Theta Newton step (prior N(0, σ_θ²)).
        grad_theta = np.bincount(rows, weights=hits - p, minlength=n_c) - inv_var_theta * theta
        info_theta = np.bincount(rows, weights=w, minlength=n_c) + inv_var_theta
        theta = theta + grad_theta / np.maximum(info_theta, 1e-9)

        eta = theta[rows] - delta[cols]
        p = 1.0 / (1.0 + np.exp(-np.clip(eta, -50, 50)))
        w = p * (1.0 - p)

        # Delta Newton step (prior N(μ_δ, σ_δ²); sign flipped — likelihood is θ_c − δ_s).
        grad_delta = -np.bincount(cols, weights=hits - p, minlength=n_s) - inv_var_delta * (
            delta - mu_delta
        )
        info_delta = np.bincount(cols, weights=w, minlength=n_s) + inv_var_delta
        delta = delta + grad_delta / np.maximum(info_delta, 1e-9)

        # Anchor mean(theta) == 0 for identifiability.
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
    """Hierarchical 1PL Rasch by EB (Laplace-EM, Type-II MLE). Hyperparameters estimated, not configured."""
    if not observations:
        return RaschPosterior(theta={}, theta_se={}, delta={}, delta_se={})

    candidate_ids = sorted({o.candidate_id for o in observations})
    sample_ids = sorted({o.sample_id for o in observations})
    c_idx = {cid: i for i, cid in enumerate(candidate_ids)}
    s_idx = {sid: j for j, sid in enumerate(sample_ids)}

    n_c = len(candidate_ids)
    n_s = len(sample_ids)

    rows = np.fromiter((c_idx[o.candidate_id] for o in observations), dtype=np.int64)
    cols = np.fromiter((s_idx[o.sample_id] for o in observations), dtype=np.int64)
    hits = np.fromiter((1.0 if o.hit else 0.0 for o in observations), dtype=np.float64)

    sigma_theta, sigma_delta, mu_delta = _INIT_SIGMA_THETA, _INIT_SIGMA_DELTA, 0.0
    theta = delta = se_theta = se_delta = np.empty(0)
    iteration = 0
    converged = False
    for _ in range(eb_max_iter):
        theta, delta, se_theta, se_delta, iteration, converged = _map_fit(
            rows, cols, hits, n_c, n_s, sigma_theta, sigma_delta, mu_delta, max_iter, tol
        )
        # EM M-step on Gaussian variance — posterior 2nd moment + hyperprior reg.
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

    # Final inner fit at converged hyperparameters.
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


def fit_theta_given_delta(
    observations: list[Observation],
    delta: Mapping[int, float],
    *,
    sigma_theta: float = _INIT_SIGMA_THETA,
    max_iter: int = 50,
    tol: float = 1e-4,
) -> dict[str, tuple[float, float]]:
    """Estimate each candidate's ability θ at a **fixed** difficulty ruler ``delta``.

    The cross-round comparability primitive. ``fit_rasch`` re-estimates δ *and*
    re-anchors ``mean(θ) == 0`` on every call, so its θ scale drifts between fits —
    round-N θ and round-0 θ sit on different rulers, which is why the round-winner
    election / c0_ok / the stall ladder couldn't compare θ across rounds on it. Holding
    δ fixed at the calibrated ruler pins the scale: every θ this returns lands on the
    one shared scale, so cross-round θ comparison is valid.

    With δ fixed the candidates **decouple** — each θ_c is an independent 1-D logistic
    MAP (prior ``N(0, σ_θ²)``, σ_θ = the ruler's population spread) over that
    candidate's observations. A sample absent from the ruler is placed at **δ=0** — a
    FLAT ruler where it is cold, so θ degenerates to logit-accuracy there rather than the
    observation being dropped. This is the "one ruler, θ always, flat where cold" contract
    (fitness-comparability slice 2): an empty ruler ``{}`` ⇒ every θ is plain logit-accuracy.
    Returns ``{candidate_id: (theta, theta_se)}``; only a candidate with *no* observation at
    all is omitted.
    """
    by_c: dict[str, list[tuple[float, bool]]] = {}
    for o in observations:
        by_c.setdefault(o.candidate_id, []).append((delta.get(o.sample_id, 0.0), o.hit))

    out: dict[str, tuple[float, float]] = {}
    inv_var = 1.0 / (sigma_theta * sigma_theta)
    for cid, rows in by_c.items():
        d_arr = np.fromiter((d for d, _ in rows), dtype=np.float64)
        h_arr = np.fromiter((1.0 if hit else 0.0 for _, hit in rows), dtype=np.float64)
        theta = 0.0
        for _ in range(max_iter):
            p = 1.0 / (1.0 + np.exp(-np.clip(theta - d_arr, -50, 50)))
            grad = float(np.sum(h_arr - p)) - inv_var * theta
            info = float(np.sum(p * (1.0 - p))) + inv_var
            step = grad / max(info, 1e-9)
            theta += step
            if abs(step) < tol:
                break
        p = 1.0 / (1.0 + np.exp(-np.clip(theta - d_arr, -50, 50)))
        info = float(np.sum(p * (1.0 - p))) + inv_var
        out[cid] = (theta, float(1.0 / np.sqrt(max(info, 1e-9))))
    return out


def build_observations(rounds: list[RoundResult]) -> list[Observation]:
    """Flatten ``cycle.rounds`` into ``(candidate, sample, hit)`` triples; skip errors."""
    obs: list[Observation] = []
    for rr in rounds:
        for cid, results in rr.all_candidate_results.items():
            for r in results:
                sid = r.get("sample_id")
                if sid is None or is_error_result(r):
                    continue
                obs.append(
                    Observation(candidate_id=cid, sample_id=int(sid), hit=bool(r.get("hit")))
                )
    return obs


def candidate_abilities(
    results_by_id: Mapping[str, list[QueryMeasurement]],
    origin_results: list[QueryMeasurement],
    delta_scale: Mapping[int, float],
) -> RaschPosterior:
    """Each arm's ability θ on the **fixed** difficulty ruler ``delta_scale`` (the cycle's
    δ bank), via ``fit_theta_given_delta``.

    The origin is folded in as a pseudo-candidate under ``ORIGIN_ABILITY_ID`` so it shares
    the arms' scale. Holding δ fixed at the bank — rather than a per-call joint ``fit_rasch``
    that re-anchors ``mean(θ)==0`` every call — is what makes θ **cross-round/cross-subset
    comparable**: the election, the c0_ok floor, the stall ladder and PoBB all read the one
    ruler. Samples absent from the ruler are flat (δ=0); raw subset accuracy is
    difficulty-blind and drifts across subsets, θ does not.
    """
    obs: list[Observation] = []
    pools: list[tuple[str, list[QueryMeasurement]]] = [
        (cid, list(results)) for cid, results in results_by_id.items()
    ]
    pools.append((ORIGIN_ABILITY_ID, list(origin_results)))
    for cid, results in pools:
        for r in results:
            sid = r.get("sample_id")
            if sid is None or is_error_result(r):
                continue
            obs.append(Observation(candidate_id=cid, sample_id=int(sid), hit=bool(r.get("hit"))))
    fit = fit_theta_given_delta(obs, delta_scale)
    return RaschPosterior(
        theta={cid: t for cid, (t, _) in fit.items()},
        theta_se={cid: se for cid, (_, se) in fit.items()},
        delta={int(sid): float(d) for sid, d in delta_scale.items()},
        delta_se={},
    )


def select_round_subset(
    bank: list[Sample],
    observations: list[Observation],
    budget: int,
) -> list[Sample]:
    """Pick ``budget`` most-informative samples via the adaptive queue mechanism.

    Prior ``N(θ_leader, σ_θ²)``; peaks on the contested band (δ ≈ leader θ).
    Cold start → bank-order prefix.
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
    # Unmeasured samples fall back to population prior (μ_δ, σ_δ).
    delta_map = {sid: posterior.delta.get(sid, posterior.mu_delta) for sid in by_id}
    delta_se_map = {sid: posterior.delta_se.get(sid, posterior.sigma_delta) for sid in by_id}
    if posterior.theta:
        leader_id = max(posterior.theta, key=lambda cid: posterior.theta[cid])
        leader_theta = posterior.theta[leader_id]
        leader_var = posterior.theta_se[leader_id] ** 2
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
    )
    return [by_id[sid] for sid in ranked[:budget]]
