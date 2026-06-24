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

# A difficulty-ruler entry is either a bare δ (1PL — discrimination ≡ 1) or a
# ``(δ, a)`` pair (2PL — per-sample discrimination ``a``). The richer 2PL value
# rides *inside* the same ruler mapping so every θ consumer reads one ``Ruler``
# and the 1PL→2PL switch is invisible above ``fit_theta_given_delta`` (the seam):
# a plain float stays 1PL, a tuple carries discrimination, an absent sample is
# flat (δ=0, a=1). This is the "one ruler, θ always, flat where cold" contract,
# generalized to 2PL (fitness-comparability slice 3).
RulerEntry = float | tuple[float, float]
Ruler = Mapping[int, RulerEntry]

__all__ = [
    "ORIGIN_ABILITY_ID",
    "Observation",
    "RaschPosterior",
    "Ruler",
    "RulerEntry",
    "build_observations",
    "candidate_abilities",
    "fit_rasch",
    "fit_rasch_2pl",
    "fit_theta_given_delta",
    "graduate_ruler_model",
    "select_round_subset",
]


class Observation(NamedTuple):
    """One ``(candidate, sample, hit)`` triple."""

    candidate_id: str
    sample_id: int
    hit: bool


def _ruler_entry(value: RulerEntry) -> tuple[float, float]:
    """Split a ruler entry into ``(δ, a)``; a bare float is 1PL (a≡1)."""
    if isinstance(value, tuple):
        return float(value[0]), float(value[1])
    return float(value), 1.0


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
        """The fixed difficulty ruler this fit defines, as the one mapping the θ
        seam reads: ``{sid: δ}`` under 1PL, ``{sid: (δ, a)}`` where discrimination
        was estimated (2PL). Folds δ + a back into a single per-sample value."""
        return {
            sid: ((d, self.discrimination[sid]) if sid in self.discrimination else d)
            for sid, d in self.delta.items()
        }


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
    delta: Ruler,
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
    candidate's observations: ``p = σ(aₛ·(θ − δₛ))``. A ruler entry is either a bare δ
    (1PL, ``aₛ ≡ 1``) or a ``(δ, a)`` pair (2PL — the high-signal sample weights its
    residual harder, the noisy one is discounted). A sample absent from the ruler is
    placed at **δ=0, a=1** — a FLAT ruler where it is cold, so θ degenerates to
    logit-accuracy there rather than the observation being dropped. This is the
    "one ruler, θ always, flat where cold" contract (fitness-comparability slices 2–3):
    an empty ruler ``{}`` ⇒ every θ is plain logit-accuracy. Returns
    ``{candidate_id: (theta, theta_se)}``; only a candidate with *no* observation is omitted.
    """
    by_c: dict[str, list[tuple[float, float, bool]]] = {}
    for o in observations:
        d, a = _ruler_entry(delta.get(o.sample_id, 0.0))
        by_c.setdefault(o.candidate_id, []).append((d, a, o.hit))

    out: dict[str, tuple[float, float]] = {}
    inv_var = 1.0 / (sigma_theta * sigma_theta)
    for cid, rows in by_c.items():
        d_arr = np.fromiter((d for d, _, _ in rows), dtype=np.float64)
        a_arr = np.fromiter((a for _, a, _ in rows), dtype=np.float64)
        h_arr = np.fromiter((1.0 if hit else 0.0 for _, _, hit in rows), dtype=np.float64)
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
        out[cid] = (theta, float(1.0 / np.sqrt(max(info, 1e-9))))
    return out


# Prior on log-discrimination (2PL): log(aₛ) ~ N(0, σ_a²) shrinks aₛ → 1, so the
# 2PL collapses to 1PL absent evidence and the scale (a vs θ/δ spread) is identified.
_SIGMA_LOG_A = 0.5
# Clip log(a) so a stays in ≈[0.05, 20] — guards a runaway sample with separable hits.
_LOG_A_CLIP = 3.0


def fit_rasch_2pl(
    observations: list[Observation],
    *,
    max_iter: int = 100,
    tol: float = 1e-4,
) -> RaschPosterior:
    """Hierarchical **2PL** fit — like ``fit_rasch`` but with a per-sample discrimination
    ``aₛ`` (``p = σ(aₛ·(θ_c − δₛ))``). 1PL says only *how hard* a sample is; 2PL also says
    *how much it tells you* — ``aₛ`` is the sample's signal-to-noise, so selection and the
    gate can weight high-discrimination samples harder and discount noisy ones.

    Warm-started from the 1PL fit (θ, δ, and its EB hyperparameters) with ``log aₛ = 0``,
    then alternating-Newton MAP over θ, δ, and ``log aₛ`` (the log keeps a > 0). Priors:
    ``θ ~ N(0, σ_θ²)``, ``δ ~ N(μ_δ, σ_δ²)`` (both from the 1PL EB fit, held fixed here),
    ``log a ~ N(0, σ_a²)`` (``_SIGMA_LOG_A``) — the log-a prior shrinks toward a=1 and pins
    the a-vs-θ scale degeneracy. ``mean(θ)==0`` anchored each step. Not gated on its own:
    ``graduate_ruler_model`` decides per-dataset whether this model is adopted.
    """
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
    hits = np.fromiter((1.0 if o.hit else 0.0 for o in observations), dtype=np.float64)

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
            np.bincount(rows, weights=a[cols] * (hits - p), minlength=n_c) - inv_var_theta * theta
        )
        info_t = (
            np.bincount(rows, weights=a[cols] ** 2 * p * (1 - p), minlength=n_c) + inv_var_theta
        )
        theta = theta + grad_t / np.maximum(info_t, 1e-9)

        # δ step — ∂η/∂δ = −aₛ.
        p = 1.0 / (1.0 + np.exp(-np.clip(a[cols] * (theta[rows] - delta[cols]), -50, 50)))
        grad_d = -np.bincount(cols, weights=a[cols] * (hits - p), minlength=n_s) - inv_var_delta * (
            delta - mu_delta
        )
        info_d = (
            np.bincount(cols, weights=a[cols] ** 2 * p * (1 - p), minlength=n_s) + inv_var_delta
        )
        delta = delta + grad_d / np.maximum(info_d, 1e-9)

        # log-a step — η = aₛ(θ−δ), ∂η/∂log a = η; Gauss-Newton info ≈ Σ w·η².
        eta = a[cols] * (theta[rows] - delta[cols])
        p = 1.0 / (1.0 + np.exp(-np.clip(eta, -50, 50)))
        grad_la = np.bincount(cols, weights=(hits - p) * eta, minlength=n_s) - inv_var_a * log_a
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


def _logp(hit: bool, theta: float, delta: float, a: float) -> float:
    """Log-likelihood of one held-out response under ``p = σ(a·(θ−δ))``."""
    p = 1.0 / (1.0 + np.exp(-float(np.clip(a * (theta - delta), -50, 50))))
    p = min(max(p, 1e-9), 1.0 - 1e-9)
    return float(np.log(p if hit else 1.0 - p))


def _full_loglik(observations: list[Observation], post: RaschPosterior) -> float:
    """In-sample log-likelihood of a fit over all its observations (for the BIC pre-check)."""
    return sum(
        _logp(
            o.hit,
            post.theta.get(o.candidate_id, 0.0),
            post.delta.get(o.sample_id, 0.0),
            post.discrimination.get(o.sample_id, 1.0),
        )
        for o in observations
    )


def _cv_loglik(observations: list[Observation], n_folds: int) -> tuple[float, float] | None:
    """Cross-validated held-out total log-likelihood ``(ll_1pl, ll_2pl)``.

    Deterministic stride folds (no RNG). For each held-out fold both models are
    refit on the rest and scored on the fold — but only on responses whose
    candidate *and* sample were seen in training (an unseen one has no estimate).
    ``None`` when too sparse to evaluate either model. This is the primary
    graduation test: held-out fit can't reward 2PL for overfitting in-sample.
    """
    n = len(observations)
    if n < n_folds * 4:
        return None
    folds = [observations[i::n_folds] for i in range(n_folds)]
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
            ll_1 += _logp(o.hit, f1.theta[o.candidate_id], f1.delta[o.sample_id], 1.0)
            ll_2 += _logp(
                o.hit,
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
) -> tuple[str, RaschPosterior]:
    """Decide per-dataset whether the difficulty bank uses 1PL or 2PL, and return
    ``(model_name, posterior)`` — the chosen fit, whose ``ruler()`` the cycle reads.

    1PL fixes sample-set drift now with the data we have; **2PL adds power once enough
    data is collected**, adopted only where it provably wins. Two gates (spec slice 3):

    1. **Cheap BIC pre-check** on the full fit — 2PL spends ``n_s`` extra discrimination
       parameters, so it must clear ``2·Δloglik > n_s·ln(N)`` before the costlier CV runs.
    2. **Cross-validated held-out log-likelihood** (the primary test) — 2PL must beat 1PL
       on out-of-sample (sample, hit) pairs by a per-response ``margin`` (hysteresis: the
       margin + re-evaluation only at calibration refresh stop round-to-round flip-flop;
       and held-out scoring means 2PL can never *regress* a dataset).

    ``enable=False`` (or too-sparse data) ⇒ always 1PL. The discrimination SE is carried
    on the returned posterior so selection/teaching can read how well ``aₛ`` is pinned.
    """
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
    delta_scale: Ruler,
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
    split = {int(sid): _ruler_entry(v) for sid, v in delta_scale.items()}
    return RaschPosterior(
        theta={cid: t for cid, (t, _) in fit.items()},
        theta_se={cid: se for cid, (_, se) in fit.items()},
        delta={sid: d for sid, (d, _) in split.items()},
        delta_se={},
        discrimination={sid: a for sid, (_, a) in split.items() if a != 1.0},
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
