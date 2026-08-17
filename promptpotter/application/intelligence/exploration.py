from __future__ import annotations

import hashlib
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, NamedTuple

import numpy as np

from promptpotter.application.intelligence.adaptive_queue_mechanism import expected_order
from promptpotter.shared.errors import is_error_result

if TYPE_CHECKING:
    from promptpotter.domain.results import CalibrationModel, RoundResult
    from promptpotter.domain.sample import Sample
    from promptpotter.domain.scoring import QueryMeasurement

# Sentinel candidate id the origin rides under inside a joint ability fit, so the
# election can read its θ on the same scale as the real candidates.
ORIGIN_ABILITY_ID = "__origin__"

# A difficulty-ruler entry is either a bare δ (1PL — discrimination ≡ 1) or a
# ``(δ, a)`` pair (2PL — per-sample discrimination ``a``). The richer 2PL value
# rides *inside* the same ruler mapping so every θ consumer reads one ``Ruler``
# and the 1PL→2PL switch is invisible above ``fit_theta_given_delta`` (the seam):
# a plain float stays 1PL, a tuple carries discrimination, an absent sample is
# flat (δ=0, a=1). This is the "one ruler, θ always, flat where cold" contract,
# generalized to 2PL.
RulerEntry = float | tuple[float, float]
Ruler = Mapping[int, RulerEntry]

__all__ = [
    "ORIGIN_ABILITY_ID",
    "Observation",
    "RaschPosterior",
    "Ruler",
    "RulerEntry",
    "adopted_level_trajectory",
    "build_observations",
    "candidate_abilities",
    "dedup_observations",
    "fit_rasch",
    "fit_rasch_2pl",
    "fit_theta_given_delta",
    "graded_response",
    "graduate_ruler_model",
    "ruler_entry",
    "ruler_expected_accuracy",
    "select_round_subset",
    "theta_lift_over_origin",
]


class Observation(NamedTuple):
    """``response`` is the sample's GRADED per-sample fitness ∈ [0,1], never a binarized ``hit`` — the
    cross-entropy MAP is valid for any y ∈ [0,1], so a graded backend keeps its gradient."""

    candidate_id: str
    sample_id: int
    response: float


def graded_response(result: Mapping[str, Any]) -> float:
    """One reader, so every θ/δ fit sees the same graded signal the composite does."""
    return min(max(float(result.get("fitness", 0.0) or 0.0), 0.0), 1.0)


def ruler_entry(value: RulerEntry) -> tuple[float, float]:
    """Split a ruler entry into ``(δ, a)``; a bare float is 1PL (a≡1)."""
    if isinstance(value, tuple):
        return float(value[0]), float(value[1])
    return float(value), 1.0


def ruler_expected_accuracy(theta: float | None, delta_scale: Ruler | None) -> float | None:
    """Ability re-projected onto the FIXED ruler — the subset-invariant peer of a round's raw accuracy.
    ``None`` at cold start (no ability or ruler), where callers fall back to raw accuracy."""
    if theta is None or not delta_scale:
        return None
    etas = np.array([a * (theta - d) for d, a in (ruler_entry(v) for v in delta_scale.values())])
    return float(np.mean(1.0 / (1.0 + np.exp(-np.clip(etas, -50, 50)))))


def adopted_level_trajectory(
    origin: tuple[float, float] | None,
    incumbents: Sequence[tuple[float, float] | None],
    delta_scale: Ruler | None,
) -> tuple[tuple[float, float] | None, list[tuple[float, float]]]:
    """Origin ability plus the per-round ability of the incumbent the search ADOPTED, in logits on
    the locked ruler. Why the incumbent: ``specs/l4-outer-loop.md`` § The measurand."""
    if origin is None or not delta_scale:
        return None, []
    prev = origin
    out: list[tuple[float, float]] = []
    for level in incumbents:
        if level is not None:
            prev = level
        out.append(prev)
    return origin, out


# Floor on the quasi-likelihood dispersion φ (`fit_theta_given_delta`). A response with no
# residual spread — a constant, a single observation — says nothing about its own dispersion,
# and an unfloored φ→0 would turn that silence into infinite confidence in θ. 0.05 caps the
# precision claim at ~4.5x the Bernoulli one, comfortably past the ×4.66 measured on the
# most underdispersed real backend (the L4 outer composite) so it never binds on live data.
_MIN_DISPERSION = 0.05

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
    # 2PL only: per-sample discrimination aᵢ (signal-to-noise). Empty under 1PL
    # (a≡1 implicitly). ``ruler()`` folds it back into the one ``Ruler`` the seam reads.
    discrimination: dict[int, float] = field(default_factory=dict)
    discrimination_se: dict[int, float] = field(default_factory=dict)

    def ruler(self) -> dict[int, RulerEntry]:
        """``{sid: δ}`` under 1PL, ``{sid: (δ, a)}`` where discrimination was estimated — δ and a folded
        into one per-sample value, the shape the θ seam reads."""
        return {
            sid: ((d, self.discrimination[sid]) if sid in self.discrimination else d)
            for sid, d in self.delta.items()
        }


def _map_fit(
    rows: np.ndarray,
    cols: np.ndarray,
    responses: np.ndarray,
    n_c: int,
    n_s: int,
    sigma_theta: float,
    sigma_delta: float,
    mu_delta: float,
    max_iter: int,
    tol: float,
) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray, int, bool]:
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
        grad_theta = np.bincount(rows, weights=responses - p, minlength=n_c) - inv_var_theta * theta
        info_theta = np.bincount(rows, weights=w, minlength=n_c) + inv_var_theta
        theta = theta + grad_theta / np.maximum(info_theta, 1e-9)

        eta = theta[rows] - delta[cols]
        p = 1.0 / (1.0 + np.exp(-np.clip(eta, -50, 50)))
        w = p * (1.0 - p)

        # Delta Newton step (prior N(μ_δ, σ_δ²); sign flipped — likelihood is θ_c − δ_s).
        grad_delta = -np.bincount(cols, weights=responses - p, minlength=n_s) - inv_var_delta * (
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
    responses = np.fromiter((o.response for o in observations), dtype=np.float64)

    sigma_theta, sigma_delta, mu_delta = _INIT_SIGMA_THETA, _INIT_SIGMA_DELTA, 0.0
    theta = delta = se_theta = se_delta = np.empty(0)
    iteration = 0
    converged = False
    for _ in range(eb_max_iter):
        theta, delta, se_theta, se_delta, iteration, converged = _map_fit(
            rows, cols, responses, n_c, n_s, sigma_theta, sigma_delta, mu_delta, max_iter, tol
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
        rows, cols, responses, n_c, n_s, sigma_theta, sigma_delta, mu_delta, max_iter, tol
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
    delta: Ruler,
    *,
    sigma_theta: float = _INIT_SIGMA_THETA,
    max_iter: int = 50,
    tol: float = 1e-4,
) -> dict[str, tuple[float, float]]:
    """Fixed δ decouples the candidates; a sample absent from the ruler sits at δ=0, a=1 — FLAT where
    cold, so θ degenerates to logit-accuracy. ``theta_se`` carries the √φ dispersion correction."""
    by_c: dict[str, list[tuple[float, float, float]]] = {}
    for o in observations:
        d, a = ruler_entry(delta.get(o.sample_id, 0.0))
        by_c.setdefault(o.candidate_id, []).append((d, a, o.response))

    out: dict[str, tuple[float, float]] = {}
    inv_var = 1.0 / (sigma_theta * sigma_theta)
    for cid, rows in by_c.items():
        d_arr = np.fromiter((d for d, _, _ in rows), dtype=np.float64)
        a_arr = np.fromiter((a for _, a, _ in rows), dtype=np.float64)
        h_arr = np.fromiter((y for _, _, y in rows), dtype=np.float64)
        theta = 0.0
        for _ in range(max_iter):
            p = 1.0 / (1.0 + np.exp(-np.clip(a_arr * (theta - d_arr), -50, 50)))
            grad = float(np.sum(a_arr * (h_arr - p))) - inv_var * theta
            info = float(np.sum(a_arr * a_arr * p * (1.0 - p))) + inv_var
            step = grad / max(info, 1e-9)
            theta += step
            if abs(step) < tol:
                break
        p = 1.0 / (1.0 + np.exp(-np.clip(a_arr * (theta - d_arr), -50, 50)))
        info = float(np.sum(a_arr * a_arr * p * (1.0 - p))) + inv_var
        # Quasi-likelihood dispersion off this candidate's own residuals (see docstring):
        # ≈1 on binary data, <1 on a graded one, >1 on an overdispersed one. `n - 1` because
        # θ was estimated from these same rows; a single row has no spare df and takes the floor.
        var = np.clip(p * (1.0 - p), 1e-6, None)
        dof = max(len(rows) - 1, 1)
        phi = max(float(np.sum((h_arr - p) ** 2 / var)) / dof, _MIN_DISPERSION)
        out[cid] = (theta, float(np.sqrt(phi) / np.sqrt(max(info, 1e-9))))
    return out


# Prior on log-discrimination (2PL): log(aₛ) ~ N(0, σ_a²) shrinks aₛ → 1, so the
# 2PL collapses to 1PL absent evidence and the scale (a vs θ/δ spread) is identified.
_SIGMA_LOG_A = 0.5
# Clip log(a) so a stays in ≈[0.05, 20] — guards a runaway sample with separable responses.
_LOG_A_CLIP = 3.0


def fit_rasch_2pl(
    observations: list[Observation],
    *,
    max_iter: int = 100,
    tol: float = 1e-4,
) -> RaschPosterior:
    """Warm-started from the 1PL fit; ``log aₛ`` keeps a > 0 and its prior pins the a-vs-θ degeneracy.
    Not gated on its own — ``graduate_ruler_model`` decides per-dataset whether it is adopted."""
    if not observations:
        return RaschPosterior(theta={}, theta_se={}, delta={}, delta_se={})

    base = fit_rasch(observations)
    candidate_ids = sorted({o.candidate_id for o in observations})
    sample_ids = sorted({o.sample_id for o in observations})
    c_idx = {cid: i for i, cid in enumerate(candidate_ids)}
    s_idx = {sid: j for j, sid in enumerate(sample_ids)}
    n_c, n_s = len(candidate_ids), len(sample_ids)

    rows = np.fromiter((c_idx[o.candidate_id] for o in observations), dtype=np.int64)
    cols = np.fromiter((s_idx[o.sample_id] for o in observations), dtype=np.int64)
    responses = np.fromiter((o.response for o in observations), dtype=np.float64)

    theta = np.array([base.theta[cid] for cid in candidate_ids], dtype=np.float64)
    delta = np.array([base.delta[sid] for sid in sample_ids], dtype=np.float64)
    log_a = np.zeros(n_s)
    inv_var_theta = 1.0 / (base.sigma_theta * base.sigma_theta)
    inv_var_delta = 1.0 / (base.sigma_delta * base.sigma_delta)
    inv_var_a = 1.0 / (_SIGMA_LOG_A * _SIGMA_LOG_A)
    mu_delta = base.mu_delta

    converged = False
    iteration = 0
    for it in range(1, max_iter + 1):
        iteration = it
        old_theta, old_delta, old_log_a = theta.copy(), delta.copy(), log_a.copy()
        a = np.exp(log_a)

        # θ step — ∂η/∂θ = aₛ.
        p = 1.0 / (1.0 + np.exp(-np.clip(a[cols] * (theta[rows] - delta[cols]), -50, 50)))
        grad_t = (
            np.bincount(rows, weights=a[cols] * (responses - p), minlength=n_c)
            - inv_var_theta * theta
        )
        info_t = (
            np.bincount(rows, weights=a[cols] ** 2 * p * (1 - p), minlength=n_c) + inv_var_theta
        )
        theta = theta + grad_t / np.maximum(info_t, 1e-9)

        # δ step — ∂η/∂δ = −aₛ.
        p = 1.0 / (1.0 + np.exp(-np.clip(a[cols] * (theta[rows] - delta[cols]), -50, 50)))
        grad_d = -np.bincount(
            cols, weights=a[cols] * (responses - p), minlength=n_s
        ) - inv_var_delta * (delta - mu_delta)
        info_d = (
            np.bincount(cols, weights=a[cols] ** 2 * p * (1 - p), minlength=n_s) + inv_var_delta
        )
        delta = delta + grad_d / np.maximum(info_d, 1e-9)

        # log-a step — η = aₛ(θ−δ), ∂η/∂log a = η; Gauss-Newton info ≈ Σ w·η².
        eta = a[cols] * (theta[rows] - delta[cols])
        p = 1.0 / (1.0 + np.exp(-np.clip(eta, -50, 50)))
        grad_la = (
            np.bincount(cols, weights=(responses - p) * eta, minlength=n_s) - inv_var_a * log_a
        )
        info_la = np.bincount(cols, weights=p * (1 - p) * eta * eta, minlength=n_s) + inv_var_a
        log_a = np.clip(log_a + grad_la / np.maximum(info_la, 1e-9), -_LOG_A_CLIP, _LOG_A_CLIP)

        shift = float(theta.mean())
        theta -= shift
        delta -= shift

        if (
            max(
                float(np.max(np.abs(theta - old_theta))) if n_c else 0.0,
                float(np.max(np.abs(delta - old_delta))) if n_s else 0.0,
                float(np.max(np.abs(log_a - old_log_a))) if n_s else 0.0,
            )
            < tol
        ):
            converged = True
            break

    a = np.exp(log_a)
    eta = a[cols] * (theta[rows] - delta[cols])
    p = 1.0 / (1.0 + np.exp(-np.clip(eta, -50, 50)))
    w = p * (1.0 - p)
    info_t = np.bincount(rows, weights=a[cols] ** 2 * w, minlength=n_c) + inv_var_theta
    info_d = np.bincount(cols, weights=a[cols] ** 2 * w, minlength=n_s) + inv_var_delta
    info_la = np.bincount(cols, weights=w * eta * eta, minlength=n_s) + inv_var_a
    se_theta = 1.0 / np.sqrt(np.maximum(info_t, 1e-9))
    se_delta = 1.0 / np.sqrt(np.maximum(info_d, 1e-9))
    se_log_a = 1.0 / np.sqrt(np.maximum(info_la, 1e-9))
    se_a = a * se_log_a  # delta method: a = exp(log a)

    return RaschPosterior(
        theta=dict(zip(candidate_ids, theta.tolist(), strict=True)),
        theta_se=dict(zip(candidate_ids, se_theta.tolist(), strict=True)),
        delta=dict(zip(sample_ids, delta.tolist(), strict=True)),
        delta_se=dict(zip(sample_ids, se_delta.tolist(), strict=True)),
        n_obs_per_candidate=base.n_obs_per_candidate,
        n_obs_per_sample=base.n_obs_per_sample,
        n_iterations=iteration,
        converged=converged,
        sigma_theta=base.sigma_theta,
        sigma_delta=base.sigma_delta,
        mu_delta=mu_delta,
        discrimination=dict(zip(sample_ids, a.tolist(), strict=True)),
        discrimination_se=dict(zip(sample_ids, se_a.tolist(), strict=True)),
    )


def _logp(y: float, theta: float, delta: float, a: float) -> float:
    p = 1.0 / (1.0 + np.exp(-float(np.clip(a * (theta - delta), -50, 50))))
    p = min(max(p, 1e-9), 1.0 - 1e-9)
    return float(y * np.log(p) + (1.0 - y) * np.log(1.0 - p))


def _full_loglik(observations: list[Observation], post: RaschPosterior) -> float:
    return sum(
        _logp(
            o.response,
            post.theta.get(o.candidate_id, 0.0),
            post.delta.get(o.sample_id, 0.0),
            post.discrimination.get(o.sample_id, 1.0),
        )
        for o in observations
    )


def _fold_of(o: Observation, n_folds: int) -> int:
    """A stable hash of the observation's OWN identity, never its position: stride folds make the
    verdict a function of walk order, and collapse outright on a stream sorted by candidate."""
    digest = hashlib.blake2b(f"{o.candidate_id}\x00{o.sample_id}".encode(), digest_size=8).digest()
    return int.from_bytes(digest, "big") % n_folds


def _cv_loglik(observations: list[Observation], n_folds: int) -> tuple[float, float] | None:
    """Scored only on responses whose candidate AND sample were seen in training. The primary
    graduation test — held-out fit cannot reward 2PL for overfitting in-sample."""
    n = len(observations)
    if n < n_folds * 4:
        return None
    folds: list[list[Observation]] = [[] for _ in range(n_folds)]
    for o in observations:
        folds[_fold_of(o, n_folds)].append(o)
    ll_1, ll_2, n_eval = 0.0, 0.0, 0
    for k in range(n_folds):
        test = folds[k]
        train = [o for j, f in enumerate(folds) if j != k for o in f]
        if not test or not train:
            continue
        f1 = fit_rasch(train)
        f2 = fit_rasch_2pl(train)
        for o in test:
            if o.candidate_id not in f1.theta or o.sample_id not in f1.delta:
                continue
            ll_1 += _logp(o.response, f1.theta[o.candidate_id], f1.delta[o.sample_id], 1.0)
            ll_2 += _logp(
                o.response,
                f2.theta.get(o.candidate_id, 0.0),
                f2.delta.get(o.sample_id, 0.0),
                f2.discrimination.get(o.sample_id, 1.0),
            )
            n_eval += 1
    if n_eval == 0:
        return None
    return ll_1, ll_2


def graduate_ruler_model(
    observations: list[Observation],
    *,
    enable: bool = True,
    margin: float = 0.01,
    n_folds: int = 5,
) -> tuple[CalibrationModel, RaschPosterior]:
    """Two gates: a cheap BIC pre-check on the full fit, then cross-validated held-out log-likelihood
    by a per-response ``margin`` (hysteresis). ``enable=False`` or too-sparse data ⇒ always 1PL."""
    base = fit_rasch(observations)
    if not enable or len(base.delta) < 2:
        return "1PL", base

    full_2pl = fit_rasch_2pl(observations)
    n_obs, n_s = len(observations), len(full_2pl.delta)
    bic_gain = 2.0 * (_full_loglik(observations, full_2pl) - _full_loglik(observations, base))
    if bic_gain <= n_s * float(np.log(max(n_obs, 2))):
        return "1PL", base  # extra discrimination params don't pay for themselves

    cv = _cv_loglik(observations, n_folds)
    if cv is None:
        return "1PL", base
    ll_1, ll_2 = cv
    # Per-response held-out gain must clear the hysteresis margin.
    n_eval_proxy = max(n_obs // n_folds, 1)
    if (ll_2 - ll_1) > margin * n_eval_proxy:
        return "2PL", full_2pl
    return "1PL", base


def build_observations(rounds: list[RoundResult]) -> list[Observation]:
    obs: list[Observation] = []
    for rr in rounds:
        for cid, results in rr.all_candidate_results.items():
            for r in results:
                sid = r.get("sample_id")
                if sid is None or is_error_result(r):
                    continue
                obs.append(
                    Observation(candidate_id=cid, sample_id=int(sid), response=graded_response(r))
                )
    return obs


def dedup_observations(*groups: Sequence[Observation]) -> list[Observation]:
    """A cell measured twice is one piece of evidence, not two — the second is almost always a cache
    replay, so LAST wins and callers pass groups oldest-first."""
    cells: dict[tuple[str, int], Observation] = {}
    for group in groups:
        for o in group:
            cells[(o.candidate_id, o.sample_id)] = o
    return list(cells.values())


def candidate_abilities(
    results_by_id: Mapping[str, list[QueryMeasurement]],
    origin_results: list[QueryMeasurement],
    delta_scale: Ruler,
) -> RaschPosterior:
    """The origin is folded in as a pseudo-candidate under ``ORIGIN_ABILITY_ID`` so it shares the arms'
    scale; holding δ at the bank is what makes θ cross-round and cross-subset comparable."""
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
            obs.append(
                Observation(candidate_id=cid, sample_id=int(sid), response=graded_response(r))
            )
    fit = fit_theta_given_delta(obs, delta_scale)
    split = {int(sid): ruler_entry(v) for sid, v in delta_scale.items()}
    return RaschPosterior(
        theta={cid: t for cid, (t, _) in fit.items()},
        theta_se={cid: se for cid, (_, se) in fit.items()},
        delta={sid: d for sid, (d, _) in split.items()},
        delta_se={},
        discrimination={sid: a for sid, (_, a) in split.items() if a != 1.0},
    )


def theta_lift_over_origin(abilities: RaschPosterior, candidate_id: str) -> float | None:
    """``None`` when either arm was never fit — an origin whose samples all errored has no entry, and
    defaulting it to 0.0 invents a logit-0 floor nobody measured. Absent is not a number."""
    theta_c = abilities.theta.get(candidate_id)
    theta_origin = abilities.theta.get(ORIGIN_ABILITY_ID)
    if theta_c is None or theta_origin is None:
        return None
    return theta_c - theta_origin


def select_round_subset(
    bank: list[Sample],
    observations: list[Observation],
    budget: int,
) -> list[Sample]:
    """A δ fit needs at least TWO arms or selecting on it is a difficulty ratchet — with one arm δ
    collapses to its hit pattern, so the contested band is just the incumbent's own misses."""
    if budget <= 0 or not bank:
        return []
    if budget >= len(bank):
        return list(bank)
    if len({o.candidate_id for o in observations}) < 2:
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
