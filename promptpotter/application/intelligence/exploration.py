"""Rasch IRT primitives + per-round scoring-subset selection via the adaptive queue mechanism."""

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
    "candidate_lcb_ability",
    "dedup_observations",
    "discovered_level_trajectory",
    "fit_rasch",
    "fit_rasch_2pl",
    "fit_theta_given_delta",
    "graded_response",
    "graduate_ruler_model",
    "ruler_entry",
    "ruler_expected_accuracy",
    "select_round_subset",
    "theta_accuracy_ci",
    "theta_lift_over_origin",
]


class Observation(NamedTuple):
    """One ``(candidate, sample, response)`` triple. ``response`` is the sample's
    **graded** per-sample fitness ∈ [0,1] — the same continuous score accuracy
    (mean fitness) and ``paired_fitness`` read, NOT a binarized ``hit``. The
    logistic MAP maximizes cross-entropy ``Σ y·log p + (1−y)·log(1−p)`` (valid for
    any y ∈ [0,1]), so a binary dataset (y ∈ {0,1}) is bit-identical to a binarized
    ``hit`` while a continuous-fitness backend (reciprocal-rank matching, the
    L4 outer proxy) keeps its gradient instead of collapsing to all-miss θ."""

    candidate_id: str
    sample_id: int
    response: float


def graded_response(result: Mapping[str, Any]) -> float:
    """The per-sample graded response for a result dict — its ``fitness`` clamped to
    [0,1] (the logistic likelihood needs y ∈ [0,1]). One reader so every θ/δ fit
    sees the same graded signal the composite does."""
    return min(max(float(result.get("fitness", 0.0) or 0.0), 0.0), 1.0)


def ruler_entry(value: RulerEntry) -> tuple[float, float]:
    """Split a ruler entry into ``(δ, a)``; a bare float is 1PL (a≡1)."""
    if isinstance(value, tuple):
        return float(value[0]), float(value[1])
    return float(value), 1.0


def ruler_expected_accuracy(theta: float | None, delta_scale: Ruler | None) -> float | None:
    """Expected score of ability ``theta`` over the fixed difficulty ruler — the
    θ-implied accuracy on the ruler's one reference sample set. Subset-invariant
    (always the whole ruler, never a round's drifting probe subset), so it is the
    honest peer of a round's raw accuracy under ``per_round_resubset``: a lucky
    thin subset can inflate raw accuracy, but ability re-projected onto the fixed
    ruler cannot. ``None`` when the ability or ruler is absent (cold start) →
    callers fall back to raw accuracy."""
    if theta is None or not delta_scale:
        return None
    etas = np.array([a * (theta - d) for d, a in (ruler_entry(v) for v in delta_scale.values())])
    return float(np.mean(1.0 / (1.0 + np.exp(-np.clip(etas, -50, 50)))))


# Residual per-candidate winner's-curse discount on the L4 *discovery* proxy. A FULL θ_se
# haircut per inner candidate asks "is each candidate individually 1-SE above origin?" — too
# strict at the thin inner budget (24 samples ⇒ θ_se ≈ 0.55–0.64, LARGER than a real
# meta-prompt lift ≈ 0.05–0.45), so every genuine inner improvement floored to ~0 and the
# outer θ went degenerate (p_best a 3-way tie at 0.5). The load-bearing winner's-curse guard
# is the OUTER θ-LCB election, which pools the whole panel (√panel lower effective SE);
# re-subtracting a full SE here double-counts it (discount-then-pool destroys what
# pool-then-discount keeps). So keep only a light residual fraction (~1/√8, the outer panel
# size): enough to penalize a wildly-uncertain inner candidate, small enough to let a real
# below-SE lift survive to be pooled. This constant is the single knob if a validation run
# over/under-corrects.
_DISCOVERY_SE_DISCOUNT = 0.25


def theta_accuracy_ci(
    theta: float | None,
    theta_se: float | None,
    delta_scale: Ruler | None,
    *,
    alpha: float = 0.05,
) -> tuple[float, float] | None:
    """The candidate's ability θ re-projected to accuracy on the fixed ruler, as a CI band.

    The decision-relevant whisker: ``ruler_expected_accuracy`` at ``θ ± z·θ_se``, the SAME
    difficulty-adjusted, subset-invariant scale the round-winner election ranks on. Because it
    borrows strength through the ruler's per-sample difficulty, it is tighter than the raw
    mean-CLT band on the same evidence — the "throw away less info" the loop already computes
    for its own decisions but never showed. ``ruler_expected_accuracy`` is monotone in θ, so the
    lower θ maps to the lower accuracy; no sort needed.

    ``None`` when θ / SE / ruler is absent (a COLD ruler, e.g. a fresh dataset or an inner
    instrument) — the caller then keeps the raw composite CI, so a difficulty-blind round is
    never dressed up as a difficulty-adjusted one."""
    if theta is None or theta_se is None or not delta_scale:
        return None
    from scipy.stats import norm

    z = float(norm.ppf(1 - alpha / 2))
    lo = ruler_expected_accuracy(theta - z * theta_se, delta_scale)
    hi = ruler_expected_accuracy(theta + z * theta_se, delta_scale)
    if lo is None or hi is None:
        return None
    return (lo, hi)


def candidate_lcb_ability(
    theta: float | None, theta_se: float | None, delta_scale: Ruler | None
) -> float | None:
    """A candidate's lightly-discounted ability on the fixed ruler: project
    ``θ − _DISCOVERY_SE_DISCOUNT · θ_se`` through :func:`ruler_expected_accuracy`.

    Reads the best candidate a meta-prompt's inner search *found* — not just the one it
    *crowned* — to recover the sub-crowning-threshold signal the conservative θ-LCB election
    throws away at a small inner sample budget. A naïve max over candidates' point θ rewards
    *variance* (the max of noisy estimates is upward-biased), so each candidate's point θ is
    discounted by a *fraction* of its SE — a residual guard only; the primary winner's-curse
    guard is the outer pooled θ-LCB election (see :data:`_DISCOVERY_SE_DISCOUNT` for why the
    full-SE haircut here was double-counting and floored the signal). ``None`` when θ / SE /
    ruler is absent — the caller then either skips the candidate (ability space) or reads its
    accuracy Wilson-LB instead (raw space)."""
    if theta is None or theta_se is None:
        return None
    return ruler_expected_accuracy(theta - _DISCOVERY_SE_DISCOUNT * theta_se, delta_scale)


def discovered_level_trajectory(
    origin_theta: float | None,
    rounds: Sequence[Sequence[tuple[float | None, float | None]]],
    delta_scale: Ruler | None,
) -> tuple[float | None, list[float]]:
    """Origin ability + per-round **mean candidate ability** — the single-scale signal an
    outer L4 cycle scores inner search quality by. Every level is a θ in LOGITS on the
    cycle's locked δ ruler, the one estimator that is subset-invariant.

    **Why logits, and not expected accuracy.** θ and the ruler's δ share one *interval*
    scale — that is the point of fitting a Rasch model at all: equal Δθ is equal difficulty
    of gain wherever the origin sits. Projecting each θ back through
    :func:`ruler_expected_accuracy` *before* differencing throws that property away, because
    the sigmoid is flat near the ruler's ceiling: the same ability gain then reads smaller for
    a strong origin than for a weak one, and the outer loop ranks meta-prompts partly by which
    seed drew an easy origin. So the ruler is still what puts every θ on one scale; it just
    stops being applied a second time on the way out.

    **Why the mean, and not the best.** A max over the trajectory is an order statistic: at an
    inner θ_se ≈ 0.42 over ~16 candidates it reads a lift above origin even when every
    candidate sits *at* origin, and its spread across arms is driven by how lucky the luckiest
    draw was rather than by the meta-prompt. It also cannot see a regression at all — one good
    round hides every bad round after it. Averaging is what makes the estimate unbiased, and it
    doubles as the RATE measurement: a meta-prompt that climbs fast spends more of its rounds at
    high ability, so it out-scores a slow climber with the same asymptote.

    **Why nothing here reads raw accuracy.** Each round scores its candidates on a different
    signal-chased subset, and a Wilson bound over raw accuracy moves with the subset's SIZE as
    much as with the candidate's skill: the origin runs the full bank while an eliminated arm
    may carry five samples, and at *identical* accuracy the shorter arm's bound sits ~0.1–0.2
    lower. Differencing those two bounds reports a regression no candidate ever committed, and
    rewards the meta-prompts whose candidates merely survived long enough to be scored. The
    election already rejects raw subset accuracy for exactly this reason
    (``elect_round_winner``); the proxy that *grades* the election must not be laxer than it.

    A candidate with **no fitted θ is skipped**. That is not data-dropping: θ is stamped onto
    exactly the *electable* candidates (``l1/score/winner.py``), so a θ-less arm is one PoBB
    already eliminated or held under the coverage floor — one the loop itself judged to carry no
    reliable measurement. A round that measured no candidate at all carries the PRIOR round's
    ability (the incumbent persists; nothing says it moved), falling back to the origin at round 1.

    Levels are **not** floored at the origin: a round that surfaced only worse-than-origin
    candidates lands *below* it, so the deltas stay negative on a genuinely regressing
    meta-prompt (the gradient the outer optimizer needs to *avoid* bad mutations).

    ``delta_scale`` is read only as the WARM-RULER GATE, not by the arithmetic — where the ruler
    is cold ``fit_theta_given_delta`` places every sample at δ=0, so θ collapses to that round's
    logit-accuracy and stops being subset-invariant, and differencing across rounds would compare
    two different scales. ``(None, [])`` there and when the origin was never fit; the caller
    excludes the cycle (``_no_evidence_reason``)."""
    if origin_theta is None or not delta_scale:
        return None, []
    prev = origin_theta
    out: list[float] = []
    for cands in rounds:
        thetas = [t for t, _ in cands if t is not None]
        if thetas:
            prev = sum(thetas) / len(thetas)
        out.append(prev)
    return origin_theta, out


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
    hits = np.fromiter((o.response for o in observations), dtype=np.float64)

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

    **``theta_se`` carries a quasi-likelihood dispersion correction, and it is load-bearing
    for every graded backend.** The Bernoulli information ``Σ a²·p(1−p)`` is the variance of a
    COIN FLIP, but ``Observation.response`` is a graded fitness ∈ [0,1]: a ranked-table answer
    at position 5 of 20 is neither a hit nor a miss, and the L4 outer proxy is a composite
    score. A graded response varies far less than a coin flip about the same mean, so assuming
    Bernoulli variance OVERSTATES the uncertainty — measured at n=28 against the true sampling
    spread of θ̂: ×1.02 on binary hit/miss, ×1.51 on reciprocal-rank-of-20, **×4.66** on the
    low-dispersion L4 outer composite. That inflation is what left the outer election unable to
    crown and PoBB unable to eliminate (p_best pinned at a tie): the loop was discarding ~4.7×
    the precision it had actually paid for.

    So the SE is scaled by ``√φ``, with ``φ`` the Pearson dispersion ``Σ (y−p)²/(p(1−p)) / (n−1)``
    (Wedderburn 1974). This is an ESTIMATE off the same residuals, not a knob — φ ≈ 1 on genuinely
    binary data, so a dichotomous dataset is unchanged; φ < 1 hands a graded backend its real
    precision back; and φ > 1 on an OVERDISPERSED backend widens the SE instead, so it fails safe
    in both directions. Floored at ``_MIN_DISPERSION``: a response with no residual variance at
    all (a constant, or a single observation) carries no evidence about its own dispersion, and
    an unfloored φ→0 would report infinite confidence from it."""
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
    hits = np.fromiter((o.response for o in observations), dtype=np.float64)

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


def _logp(y: float, theta: float, delta: float, a: float) -> float:
    """Cross-entropy log-likelihood of one held-out graded response ``y ∈ [0,1]``
    under ``p = σ(a·(θ−δ))``: ``y·log p + (1−y)·log(1−p)`` (reduces to the binary
    branch when ``y ∈ {0,1}``)."""
    p = 1.0 / (1.0 + np.exp(-float(np.clip(a * (theta - delta), -50, 50))))
    p = min(max(p, 1e-9), 1.0 - 1e-9)
    return float(y * np.log(p) + (1.0 - y) * np.log(1.0 - p))


def _full_loglik(observations: list[Observation], post: RaschPosterior) -> float:
    """In-sample log-likelihood of a fit over all its observations (for the BIC pre-check)."""
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
    """Which CV fold an observation belongs to — a stable hash of its OWN identity.

    Never its position in the list. Stride folds (``observations[i::n_folds]``) made the
    graduation verdict a function of the order the archive happened to be walked in, so a
    ``reindex`` could flip 1PL↔2PL, hence δ, hence which candidates PoBB kills. Worse, they
    collapse outright on a sorted stream: with the observations grouped by candidate and
    sorted by sample, a stride index becomes a bijection onto the sample, so fold *k* holds
    a fixed sample partition whose samples are never in training and EVERY held-out response
    is dropped (``n_eval == 0`` ⇒ 1PL forced) — for any dataset size divisible by ``n_folds``.
    """
    digest = hashlib.blake2b(f"{o.candidate_id}\x00{o.sample_id}".encode(), digest_size=8).digest()
    return int.from_bytes(digest, "big") % n_folds


def _cv_loglik(observations: list[Observation], n_folds: int) -> tuple[float, float] | None:
    """Cross-validated held-out total log-likelihood ``(ll_1pl, ll_2pl)``.

    Deterministic hash folds (no RNG, no positional dependence — see :func:`_fold_of`).
    For each held-out fold both models are refit on the rest and scored on the fold — but
    only on responses whose candidate *and* sample were seen in training (an unseen one has
    no estimate). ``None`` when too sparse to evaluate either model. This is the primary
    graduation test: held-out fit can't reward 2PL for overfitting in-sample.
    """
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
    """Flatten ``cycle.rounds`` into ``(candidate, sample, response)`` triples; skip errors."""
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
    """Collapse ``groups`` onto one observation per ``(candidate, sample)`` cell, LAST wins.

    A cell measured twice is not two pieces of evidence — the second is almost always a cache
    replay of the first (a re-scored origin under a new round subset), and letting it through
    weights that sample in δ by how often it happened to be replayed. Newest-wins is the same
    rule ``load_reusable_results`` resolves the identical collision with, so pass groups
    oldest-first.
    """
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
    """``θ_candidate − θ_origin`` on the fixed ruler, or ``None`` when either arm was never fit.

    The one reader of the matched origin's ability. ``fit_theta_given_delta`` **omits an arm
    with no observation**, and ``candidate_abilities`` drops every errored row — so an origin
    whose samples all failed carries no ``ORIGIN_ABILITY_ID`` entry at all. Defaulting it to
    ``0.0`` invents a logit-0 origin: an arm as able as a coin, on a floor nobody measured.
    Both the winner election and the promotion gate read the lift, so both would rank against
    that phantom.

    ``paired_fitness`` does not catch this. It counts an errored row as a 0.0 cell (its
    declared policy — "the score it earned"), so the origin-overlap guard passes on exactly the
    rows the θ fit threw away.

    There is no lift over a floor that was never measured. Absent is not a number.
    """
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
    """Pick ``budget`` most-informative samples via the adaptive queue mechanism.

    Prior ``N(θ_leader, σ_θ²)``; peaks on the contested band (δ ≈ leader θ).
    Cold start → bank-order prefix.

    **A δ fit needs at least two arms, or selecting on it is a difficulty ratchet.** With a
    single arm the Rasch likelihood cannot separate ability from difficulty: θ is pinned by the
    identifiability anchor and δ collapses to that one arm's hit pattern, shrunk — two values,
    "it passed" and "it failed". Selecting the contested band off that ruler then means "give me
    everything the incumbent got wrong", which is not the most-informative subset, it is the
    incumbent's own worst 28. Measured on all four ``justlogic-d234`` inner campaigns of
    2026-07-27: round 1 dropped 12 samples versus round 0 and **all 12 were C0 hits, zero were
    misses**, in every campaign — C0's own rate on the round-1 panel fell 0.525 → 0.321. Every
    one of those campaigns then read round 1 as a regression. So one arm falls back to the
    bank prefix, alongside the no-observation cold start it is a special case of.
    """
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
