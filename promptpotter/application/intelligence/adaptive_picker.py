"""Online adaptive sample picker — 1PL Rasch CAT with Fisher-info objective.

Maintains a Gaussian-approximation posterior on the candidate's latent
ability ``θ_c`` (mean ``μ`` + variance ``σ²``) and re-picks the next
sample after each observation. Objective: **Maximum Fisher Information**
at the candidate's current ``θ̂_c`` — i.e., select the sample whose
outcome is most uncertain under current beliefs, equivalently the sample
whose ``δ_s`` sits closest to the candidate's estimated ability.

This is Phase A of the picker arc: textbook Computerized Adaptive
Testing on the 1PL (Rasch) model (Owen 1975 sequential Bayes / Lord
1980 MFI). Phase B will swap the per-sample objective for the Chernoff
information between the candidate's and seed's predictive distributions
(Track-and-Stop, Garivier-Kaufmann 2016) — decision-aligned variant
that directly minimizes expected samples to a keep/abort verdict.

Pure module: no I/O, no mutation of inputs.
"""

from __future__ import annotations

import math
from collections.abc import Iterable

__all__ = [
    "chernoff_bernoulli",
    "chernoff_info_pair",
    "expected_order_mfi",
    "expected_order_track_and_stop",
    "fisher_info",
    "next_sample_mfi",
    "next_sample_track_and_stop",
    "posterior_from_outcomes",
    "predicted_hit_probability",
    "update_theta_posterior",
]


def _sigmoid(x: float) -> float:
    """Numerically-stable sigmoid (no SciPy dependency)."""
    if x >= 0:
        return 1.0 / (1.0 + math.exp(-x))
    ex = math.exp(x)
    return ex / (1.0 + ex)


def update_theta_posterior(mu: float, var: float, delta_s: float, hit: bool) -> tuple[float, float]:
    """One Newton-step Gaussian update on ``θ_c`` given (δ_s, hit).

    Posterior log-density under a Gaussian prior ``θ ~ N(μ, σ²)`` and
    Bernoulli 1PL likelihood:

        log p(θ | data) = -(θ - μ)²/(2σ²) + y·(θ - δ_s) - log(1 + e^(θ - δ_s))

    where ``y = 1`` if hit else ``0``. One Newton step anchored at the
    prior mean ``θ = μ`` gives the Laplace-approximation posterior:

        score   = y - σ(μ - δ_s)                      (first deriv)
        info    = 1/σ² + σ(μ - δ_s)·(1 - σ(μ - δ_s))  (observed Fisher)
        σ'²     = 1 / info
        μ'      = μ + σ'² · score

    Closed form, no iteration. Variance is floored at ``1e-6`` so
    repeated updates on a confidently-pinned θ stay numerically stable.
    """
    p = _sigmoid(mu - delta_s)
    score = (1.0 if hit else 0.0) - p
    info = 1.0 / var + p * (1.0 - p)
    new_var = max(1.0 / info, 1e-6)
    new_mu = mu + new_var * score
    return new_mu, new_var


def posterior_from_outcomes(
    prior_mu: float,
    prior_var: float,
    outcomes: Iterable[tuple[float, bool]],
) -> tuple[float, float]:
    """Fold ``update_theta_posterior`` across a sequence of (δ_s, hit) pairs.

    Stateless recomputation — callers don't persist the running posterior;
    they fold over the candidate's measured outcomes from scratch each
    time the picker fires. With ≤ ``len(dataset)`` updates per candidate
    and each update O(1), this is microseconds per call.
    """
    mu, var = prior_mu, prior_var
    for delta_s, hit in outcomes:
        mu, var = update_theta_posterior(mu, var, delta_s, hit)
    return mu, var


def fisher_info(mu_c: float, delta_s: float) -> float:
    """1PL Fisher information at the candidate's current ``θ̂_c``.

    Returns ``p · (1 - p)`` where ``p = σ(μ_c - δ_s)``. Maximized at
    ``δ_s == μ_c`` (peak 0.25) and falls off symmetrically — the
    "capable region" centered on the candidate's currently-estimated
    ability, not statically on ``p_pop = 0.5``.
    """
    p = _sigmoid(mu_c - delta_s)
    return p * (1.0 - p)


def predicted_hit_probability(mu_c: float, delta_s: float) -> float:
    """Convenience accessor: ``σ(μ_c - δ_s)`` — the candidate's currently-
    predicted hit probability on sample ``s``. Telemetry-only; the picker
    consumes ``fisher_info`` directly."""
    return _sigmoid(mu_c - delta_s)


def next_sample_mfi(
    mu_c: float,
    delta_map: dict[int, float],
    remaining: set[int],
) -> int | None:
    """Pick the remaining sample maximizing Fisher info at ``μ_c``.

    Returns ``None`` when ``remaining`` is empty (loop-exit signal).
    Ties break by ascending sample id (deterministic). Samples missing
    from ``delta_map`` fall back to ``δ = 0`` (centred difficulty), which
    yields the cold-start tie behaviour: everything sorts equal and
    sample-id ascending decides.
    """
    if not remaining:
        return None
    return max(
        remaining,
        key=lambda sid: (fisher_info(mu_c, delta_map.get(sid, 0.0)), -sid),
    )


def expected_order_mfi(
    mu_c: float,
    delta_map: dict[int, float],
    sample_ids: Iterable[int],
) -> list[int]:
    """Full ranking of ``sample_ids`` by descending Fisher info under ``μ_c``.

    Telemetry-only snapshot consumed by ``dashboard.json::hard_sample_order``
    so the webapp's hard-samples table mirrors the picker's expected
    sequence. Live execution still re-picks per step via
    :func:`next_sample_mfi` once outcomes start arriving.
    """
    return sorted(
        sample_ids,
        key=lambda sid: (-fisher_info(mu_c, delta_map.get(sid, 0.0)), sid),
    )


# ---------------------------------------------------------------------------
# Phase B — Track-and-Stop objective (decision-aligned picker)
# ---------------------------------------------------------------------------


def chernoff_bernoulli(p: float, q: float) -> float:
    """Chernoff information between two Bernoulli distributions.

    ``C(p, q) = max_{λ∈[0,1]} -log[ p^λ q^{1-λ} + (1-p)^λ (1-q)^{1-λ} ]``.

    Zero iff ``p == q``; positive otherwise. The exponent of the
    asymptotic error rate in optimal binary hypothesis testing
    (Garivier-Kaufmann 2016 Track-and-Stop). No closed form for λ*;
    99-point grid is sub-1% precision and trivially cheap at the
    per-sample fire rate.
    """
    if abs(p - q) < 1e-9:
        return 0.0
    p = min(max(p, 1e-9), 1.0 - 1e-9)
    q = min(max(q, 1e-9), 1.0 - 1e-9)
    best = 0.0
    for i in range(1, 100):
        lam = i / 100.0
        mix = p**lam * q ** (1.0 - lam) + (1.0 - p) ** lam * (1.0 - q) ** (1.0 - lam)
        val = -math.log(mix)
        if val > best:
            best = val
    return best


def chernoff_info_pair(mu_c: float, mu_s: float, delta_s: float) -> float:
    """Chernoff info between candidate's and seed's predictive Bernoulli on sample s.

    ``p_c = σ(μ_c - δ_s)``, ``p_s = σ(μ_s - δ_s)``. Returns
    ``C(p_c, p_s)`` — the expected information one measurement on
    sample ``s`` contributes toward the candidate-vs-seed decision.
    Peaks where the two predicted hit probabilities differ most
    (``δ_s`` sitting in the gap between ``μ_c`` and ``μ_s``); zero
    where they agree (no discriminatory value).

    Decision-aligned variant of the picker: directly minimizes the
    expected number of measurements to a confident keep/abort
    verdict, replacing Phase A's θ-estimation-variance objective.
    """
    p_c = _sigmoid(mu_c - delta_s)
    p_s = _sigmoid(mu_s - delta_s)
    return chernoff_bernoulli(p_c, p_s)


def next_sample_track_and_stop(
    mu_c: float,
    mu_s: float,
    delta_map: dict[int, float],
    remaining: set[int],
) -> int | None:
    """Pick the remaining sample maximizing Chernoff info between candidate and seed.

    Returns ``None`` when ``remaining`` is empty. Ties break by
    ascending sample id (deterministic). Samples missing from
    ``delta_map`` fall back to ``δ = 0`` — under cold-start
    ``μ_c == μ_s == 0`` this gives zero pick-score everywhere, sid-asc
    decides, matching MFI's cold-start behaviour.
    """
    if not remaining:
        return None
    return max(
        remaining,
        key=lambda sid: (chernoff_info_pair(mu_c, mu_s, delta_map.get(sid, 0.0)), -sid),
    )


def expected_order_track_and_stop(
    mu_c: float,
    mu_s: float,
    delta_map: dict[int, float],
    sample_ids: Iterable[int],
) -> list[int]:
    """Full ranking by descending Chernoff info between (μ_c, μ_s) on each δ_s.

    Telemetry snapshot peer of :func:`expected_order_mfi` — surfaced
    on ``dashboard.json::hard_sample_order`` when the picker objective
    is ``track_and_stop``.
    """
    return sorted(
        sample_ids,
        key=lambda sid: (
            -chernoff_info_pair(mu_c, mu_s, delta_map.get(sid, 0.0)),
            sid,
        ),
    )
