from __future__ import annotations

import hashlib
import logging
from collections.abc import Collection, Mapping, Sequence
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, NamedTuple

import numpy as np
from numpy.typing import NDArray

from promptpotter.application.intelligence.adaptive_queue_mechanism import decision_order
from promptpotter.domain.ruler import (
    AbilityReading,
    CalibrationModel,
    DeltaRuler,
    Ruler,
    anchor_id_of,
    ruler_entry,
)
from promptpotter.shared.errors import RulerCoverageError, is_error_result

if TYPE_CHECKING:
    from promptpotter.domain.results import RoundResult
    from promptpotter.domain.sample import Sample
    from promptpotter.domain.scoring import QueryMeasurement

logger = logging.getLogger(__name__)

# C0 inside the δ-calibrating fit; the archive's copies of the origin fold onto this one id.
ORIGIN_ABILITY_ID = "__origin__"
# The ROUND'S PARENT inside a joint ability fit — the origin at round 0, the prior winner after.
PARENT_ABILITY_ID = "__parent__"

__all__ = [
    "ORIGIN_ABILITY_ID",
    "PARENT_ABILITY_ID",
    "Observation",
    "RaschPosterior",
    "build_observations",
    "candidate_abilities",
    "dedup_observations",
    "extend_ruler",
    "fit_rasch",
    "fit_rasch_2pl",
    "fit_theta_given_delta",
    "graded_response",
    "graduate_ruler_model",
    "observations_from_results",
    "parent_level_trajectory",
    "select_round_subset",
    "theta_lift_over_parent",
]


class Observation(NamedTuple):
    """One (candidate, CELL) response — the atom of every fit below.

    ``response`` is the sample's GRADED per-sample fitness ∈ [0,1], never a binarized ``hit`` — the
    cross-entropy MAP is valid for any y ∈ [0,1], so a graded backend keeps its gradient. A cell
    graded in STEPS composes those steps into that one number through the scoring formula; the
    steps never arrive here as separate rows (:func:`dedup_observations`).

    ``sample_id`` is an ``int`` and that is load-bearing rather than incidental: it is a CELL id,
    so a step index cannot be spelled into it without changing this type."""

    candidate_id: str
    sample_id: int
    response: float


def graded_response(result: Mapping[str, Any]) -> float:
    """One reader for STAMPED rows, so every θ/δ fit sees the same graded signal the composite does.

    ``objective``, never ``fitness``: a round is won on θ, so this is the ONE place a cost, latency
    or reliability term reaches the election at all (``domain/scoring.py::CellScorer``).

    A row with no ``objective`` RAISES rather than defaulting — absence means the row never went
    through ``rescore_results``, and a default reads that as a cell the arm got WRONG, which is
    what fits a ruler on an all-zeros matrix. Archive rows are graded by the READING campaign's
    scorer instead (``hard_sample_archive.py::build_archive_observations``)."""
    if "objective" not in result:
        raise KeyError(
            "graded_response: row carries no 'objective'. Only rows stamped by "
            "``rescore_results`` may be read here; grade an archive row with the reading "
            "campaign's CellScorer."
        )
    return min(max(float(result["objective"] or 0.0), 0.0), 1.0)


def parent_level_trajectory(
    origin: AbilityReading | None,
    winners: Sequence[AbilityReading | None],
    ruler: DeltaRuler | None,
) -> tuple[tuple[float, float] | None, list[tuple[float, float]]]:
    """Origin ability plus the per-round ability of the PARENT the search stood on, in logits on
    the locked ruler — a round that crowned nobody carries the previous one forward. Why the
    parent rather than the proposals: ``specs/l4-outer-loop.md`` § The measurand.

    Readings, not bare pairs: these levels are differenced, and that is only a difference if they
    share a scale — ``comparable_to`` is what a bare ``(θ, θ_se)`` cannot be asked. An off-scale
    round carries the previous level forward, exactly as a round that crowned nobody does."""
    if origin is None or origin.se is None or ruler is None or not ruler.delta:
        return None, []
    if origin.ruler_id != ruler.anchor_id:
        # No reference on this scale ⇒ nothing to difference against; the L4 law reads it as
        # no evidence.
        logger.warning(
            "origin ability sits on %s, not the cycle's ruler %s — no level series on this scale",
            origin.scale(),
            ruler.anchor_id,
        )
        return None, []
    origin_pair = (origin.theta, origin.se)
    prev = origin_pair
    out: list[tuple[float, float]] = []
    for level in winners:
        if level is not None and level.se is not None and level.comparable_to(origin):
            prev = (level.theta, level.se)
        out.append(prev)
    return origin_pair, out


# Broad EB starting priors — first inner MAP fit is barely regularized.
_INIT_SIGMA_THETA = 1.5
_INIT_SIGMA_DELTA = 2.0

# Weak inverse-gamma hyperprior on each variance — stops σ → 0 collapse under
# sparse data; washes out against real n.
_EB_NU0 = 1.0
_EB_S0_SQ = 1.0

# The largest logit ONE Newton step may move a parameter. Every fit below is a MAP estimate on a
# strictly log-concave posterior, so the root is unique and a bounded step always reaches it while
# an unbounded one need not: where p saturates, the observed information collapses to the prior
# term alone and `grad / info` jumps tens of logits into the opposite saturation. It bites the arms
# furthest from the centre hardest — the ones that BEAT it — so an undamped fit reads improvement
# as collapse.
_MAX_NEWTON_STEP = 1.0

# Panel slots held for the cells δ is least sure of, out of `sp_budget_ttest`; `_with_ruler_learning`
# states what each end costs. A first estimate off one ruler, worth re-fitting on a second dataset.
_RULER_LEARNING_SLOTS = 4


def _newton_step(
    grad: NDArray[np.floating[Any]] | float, info: NDArray[np.floating[Any]] | float
) -> NDArray[np.floating[Any]]:
    """One DAMPED Newton step — information floored so it cannot divide by zero, step bounded so it
    cannot leave the region that information was measured in. Every fit here steps through this;
    spelled per site, the bound is one a new fit forgets."""
    return np.clip(grad / np.maximum(info, 1e-9), -_MAX_NEWTON_STEP, _MAX_NEWTON_STEP)


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

    def anchored(self, calibration_model: CalibrationModel) -> DeltaRuler:
        """LOCK this fit as a cycle's ruler. Carries the priors the fit converged to, which the one-shot
        version discarded — without them an extension cannot regularize a new cell the way the anchored
        ones were, and the scale bends. The anchor id is stamped HERE, once, and never recomputed.

        It carries no round: the anchor is a fact about the δ scale, and the round the lock happened
        on is already the ``RulerRecord``'s own — stamping it twice invited a reader to compare a
        ruler to the round that minted it, which an anchored extension deliberately makes untrue."""
        return DeltaRuler(
            delta=dict(self.delta),
            delta_se=dict(self.delta_se),
            discrimination={sid: a for sid, a in self.discrimination.items() if a != 1.0},
            mu_delta=self.mu_delta,
            sigma_delta=self.sigma_delta,
            sigma_theta=self.sigma_theta,
            calibration_model=calibration_model,
            anchor_id=anchor_id_of(self.delta, self.mu_delta, self.sigma_delta, calibration_model),
        )


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
        theta = theta + _newton_step(grad_theta, info_theta)

        eta = theta[rows] - delta[cols]
        p = 1.0 / (1.0 + np.exp(-np.clip(eta, -50, 50)))
        w = p * (1.0 - p)

        # Delta Newton step (prior N(μ_δ, σ_δ²); sign flipped — likelihood is θ_c − δ_s).
        grad_delta = -np.bincount(cols, weights=responses - p, minlength=n_s) - inv_var_delta * (
            delta - mu_delta
        )
        info_delta = np.bincount(cols, weights=w, minlength=n_s) + inv_var_delta
        delta = delta + _newton_step(grad_delta, info_delta)

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
    delta: Ruler | None,
    *,
    sigma_theta: float = _INIT_SIGMA_THETA,
    anchor_id: str = "",
    max_iter: int = 50,
    tol: float = 1e-4,
) -> dict[str, tuple[float, float]]:
    """Fixed δ decouples the candidates. ``theta_se`` carries the √φ dispersion correction.

    ``delta=None`` is the COLD ruler — flat δ=0, a=1, so θ degenerates to logit-accuracy. That is
    legitimate (round 0 has one arm, and logit-accuracy depends on no fit, so it is comparable
    across cycles) and it is the ONLY state in which a cell may go ungraded.

    A ruler that is not ``None`` must carry every observed cell, and a hole RAISES rather than
    grading it δ=0 — zero is a POSITION on this scale, not a neutral value, so a ruler centred
    well above it would read an unmeasured cell as easier than anything ever measured.
    """
    if delta is None:
        graded: dict[int, tuple[float, float]] = dict.fromkeys(
            (o.sample_id for o in observations), (0.0, 1.0)
        )
    else:
        observed = {o.sample_id for o in observations}
        missing = sorted(observed - delta.keys())
        if missing:
            raise RulerCoverageError(missing, anchor_id=anchor_id)
        graded = {sid: ruler_entry(delta[sid]) for sid in observed}

    by_c: dict[str, list[tuple[float, float, float]]] = {}
    for o in observations:
        d, a = graded[o.sample_id]
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
            step = float(_newton_step(grad, info))
            theta += step
            if abs(step) < tol:
                break
        p = 1.0 / (1.0 + np.exp(-np.clip(a_arr * (theta - d_arr), -50, 50)))
        info = float(np.sum(a_arr * a_arr * p * (1.0 - p))) + inv_var
        # Dispersion shrunk toward the nominal 1.0, the same inverse-gamma the two σ above use.
        # Never a pooled φ̄: that makes an arm's SE depend on which arms shared its round.
        var = np.clip(p * (1.0 - p), 1e-6, None)
        dof = max(len(rows) - 1, 1)
        raw_phi = float(np.sum((h_arr - p) ** 2 / var)) / dof
        phi = (dof * raw_phi + _EB_NU0 * _EB_S0_SQ) / (dof + _EB_NU0)
        out[cid] = (theta, float(np.sqrt(phi) / np.sqrt(max(info, 1e-9))))
    return out


def extend_ruler(
    ruler: DeltaRuler,
    observations: list[Observation],
    *,
    max_iter: int = 50,
    tol: float = 1e-4,
) -> DeltaRuler:
    """δ for cells the ruler does not yet carry, fit against arm abilities read ON the frozen ruler.

    Fixed-common-item calibration: the anchored cells give every arm a θ on the existing scale, and
    each new cell's δ is then the one value that explains its responses at those θ. The anchor never
    moves — existing δ, ``mu_delta``, ``sigma_delta``, ``calibration_model`` and ``anchor_id`` are
    carried verbatim, and ``mean(theta) == 0`` is NEVER re-imposed (that is ``_map_fit``'s job, and
    re-imposing it here is exactly how the scale would drift).

    ONE pass, deliberately — not coordinate ascent. Folding the new δ back into the arms' θ reads as
    "more accurate" and lets the anchor drift a little every round, which is this bug in slow motion.

    Raises ``RulerCoverageError`` when a new cell has no arm carrying an anchored ability to link
    through: the subset walked entirely off the ruler and there is nothing to equate against. A
    provisional δ would be a fabricated value written permanently into the scale — the transient
    read that legitimately needs one is ``DeltaRuler.entries_covering``, not this.
    """
    new_ids = {o.sample_id for o in observations} - ruler.delta.keys()
    if not new_ids:
        return ruler

    anchored = [o for o in observations if o.sample_id in ruler.delta]
    # `_INIT_SIGMA_THETA`, not `ruler.sigma_theta`, so this link is regularized exactly as the
    # election's own θ read is. The ruler CARRIES the fit's converged σ_θ and nothing passes it
    # yet: doing so moves every θ in the repo and belongs in its own commit with its own
    # before/after, not smuggled in here where it would be indistinguishable from the extension.
    theta = fit_theta_given_delta(anchored, ruler.entries(), anchor_id=ruler.anchor_id)

    by_s: dict[int, list[tuple[float, float]]] = {}
    for o in observations:
        arm = theta.get(o.candidate_id)
        if o.sample_id in ruler.delta or arm is None:
            continue
        by_s.setdefault(o.sample_id, []).append((arm[0], o.response))

    unlinkable = sorted(new_ids - by_s.keys())
    if unlinkable:
        raise RulerCoverageError(unlinkable, anchor_id=ruler.anchor_id)

    inv_var = 1.0 / (ruler.sigma_delta * ruler.sigma_delta)
    delta = dict(ruler.delta)
    delta_se = dict(ruler.delta_se)
    for sid, rows in by_s.items():
        t_arr = np.fromiter((t for t, _ in rows), dtype=np.float64)
        y_arr = np.fromiter((y for _, y in rows), dtype=np.float64)
        # Seeded at the ruler's own centre and pulled by N(μ_δ, σ_δ²) — the same prior the anchored
        # cells were fit under, which is why `sigma_delta` had to be carried on the ruler at all.
        d = ruler.mu_delta
        for _ in range(max_iter):
            p = 1.0 / (1.0 + np.exp(-np.clip(t_arr - d, -50, 50)))
            grad = -float(np.sum(y_arr - p)) - inv_var * (d - ruler.mu_delta)
            info = float(np.sum(p * (1.0 - p))) + inv_var
            step = float(_newton_step(grad, info))
            d += step
            if abs(step) < tol:
                break
        p = 1.0 / (1.0 + np.exp(-np.clip(t_arr - d, -50, 50)))
        info = float(np.sum(p * (1.0 - p))) + inv_var
        delta[sid] = d
        delta_se[sid] = float(1.0 / np.sqrt(max(info, 1e-9)))

    # A new cell keeps a ≡ 1 even under 2PL: one round's three-to-six arms cannot identify a
    # discrimination, and `_LOG_A_CLIP` would happily let a separable cell run to ±3.
    return ruler.model_copy(update={"delta": delta, "delta_se": delta_se})


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
        theta = theta + _newton_step(grad_t, info_t)

        # δ step — ∂η/∂δ = −aₛ.
        p = 1.0 / (1.0 + np.exp(-np.clip(a[cols] * (theta[rows] - delta[cols]), -50, 50)))
        grad_d = -np.bincount(
            cols, weights=a[cols] * (responses - p), minlength=n_s
        ) - inv_var_delta * (delta - mu_delta)
        info_d = (
            np.bincount(cols, weights=a[cols] ** 2 * p * (1 - p), minlength=n_s) + inv_var_delta
        )
        delta = delta + _newton_step(grad_d, info_d)

        # log-a step — η = aₛ(θ−δ), ∂η/∂log a = η; Gauss-Newton info ≈ Σ w·η².
        eta = a[cols] * (theta[rows] - delta[cols])
        p = 1.0 / (1.0 + np.exp(-np.clip(eta, -50, 50)))
        grad_la = (
            np.bincount(cols, weights=(responses - p) * eta, minlength=n_s) - inv_var_a * log_a
        )
        info_la = np.bincount(cols, weights=p * (1 - p) * eta * eta, minlength=n_s) + inv_var_a
        log_a = np.clip(log_a + _newton_step(grad_la, info_la), -_LOG_A_CLIP, _LOG_A_CLIP)

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


def observations_from_results(
    results_by_id: Mapping[str, Sequence[Mapping[str, Any]]],
) -> list[Observation]:
    """The ONE walk from ``{candidate_id: rows}`` to observations, skipping unscored and errored
    cells. Four inline copies of it existed; two fits that skip different sets disagree about the
    scale, and nothing anywhere would have said so."""
    return [
        Observation(candidate_id=cid, sample_id=int(sid), response=graded_response(r))
        for cid, results in results_by_id.items()
        for r in results
        if (sid := r.get("sample_id")) is not None and not is_error_result(r)
    ]


def build_observations(rounds: list[RoundResult]) -> list[Observation]:
    return [o for rr in rounds for o in observations_from_results(rr.all_candidate_results)]


def dedup_observations(*groups: Sequence[Observation]) -> list[Observation]:
    """A cell measured twice is one piece of evidence, not two — the second is almost always a cache
    replay, so LAST wins and callers pass groups oldest-first.

    **This is also where the step/item line is held, and it is the same rule seen from the other
    side.** Rasch assumes conditional independence: given θ, responses are independent. Two grades
    of ONE cell are not independent whatever produced them — a replay, or a cell graded in steps —
    so the cell stays the atom, and per-step terms reach θ only through the composite the scoring
    formula builds. Promoting steps to items to buy resolution is a **precision leak, not a fitting
    error**: k per cell claims kN observations where there are N, every SE shrinks by ~√k, and PoBB
    eliminates on confidence it never earned. Nothing raises and the error flatters. Legitimate
    per-step parameters need a testlet or bi-factor model — a person×testlet random effect
    absorbing the within-cell dependence — which is a different posterior and a new ``ruler_id``.
    Owned by ``docs/methods/verdict-resolution.md`` § Phase 3.

    Collapsing rather than raising is deliberate: at this seam a replay and a step-split are
    indistinguishable, so the fit cannot tell them apart and must not try. What keeps the invariant
    real is that every path into a fit passes through here — which was NOT true of
    :func:`candidate_abilities` until it was made so, and that was the θ the election reads."""
    cells: dict[tuple[str, int], Observation] = {}
    for group in groups:
        for o in group:
            cells[(o.candidate_id, o.sample_id)] = o
    return list(cells.values())


def candidate_abilities(
    results_by_id: Mapping[str, list[QueryMeasurement]],
    parent_results: list[QueryMeasurement],
    ruler: DeltaRuler | None,
) -> RaschPosterior:
    """The PARENT is folded in as a pseudo-candidate under ``PARENT_ABILITY_ID`` so it shares the arms'
    scale; holding δ at the bank is what makes θ cross-round and cross-subset comparable.

    ``parent_results`` is the parent RE-SCORED on this round's panel, never C0's banked rows —
    an arm is crowned on a lift over what it must actually beat.

    DEDUPED, and this is the fit that most needed it: ``observations_from_results`` emits one
    observation per ROW, so a cell re-measured within a round (the stale-data ladder re-enters
    ``measure_sample``) reached the θ that decides the election as two independent draws. The
    ruler-fit sites already deduped; this one did not, and it is the one whose SEs PoBB cuts on."""
    obs = dedup_observations(
        observations_from_results({**results_by_id, PARENT_ABILITY_ID: parent_results})
    )
    fit = fit_theta_given_delta(
        obs,
        ruler.entries() if ruler is not None else None,
        anchor_id=ruler.anchor_id if ruler is not None else "",
    )
    split = {sid: ruler_entry(v) for sid, v in (ruler.entries() if ruler else {}).items()}
    return RaschPosterior(
        theta={cid: t for cid, (t, _) in fit.items()},
        theta_se={cid: se for cid, (_, se) in fit.items()},
        delta={sid: d for sid, (d, _) in split.items()},
        delta_se={},
        discrimination={sid: a for sid, (_, a) in split.items() if a != 1.0},
    )


def theta_lift_over_parent(abilities: RaschPosterior, candidate_id: str) -> float | None:
    """``None`` when either arm was never fit — a parent whose samples all errored has no entry, and
    defaulting it to 0.0 invents a logit-0 floor nobody measured. Absent is not a number."""
    theta_c = abilities.theta.get(candidate_id)
    theta_parent = abilities.theta.get(PARENT_ABILITY_ID)
    if theta_c is None or theta_parent is None:
        return None
    return theta_c - theta_parent


def select_round_subset(
    bank: list[Sample],
    observations: list[Observation],
    budget: int,
    *,
    ruler: DeltaRuler | None = None,
    anchor_floor: int = 0,
    leader_ids: Collection[str] | None = None,
) -> list[Sample]:
    """Which cells this round buys, ordered on the cycle's LOCKED ruler.

    Never ``fit_rasch`` here: a fresh re-anchoring per round makes the δ that CHOOSES the samples
    a different scale from the δ that SCORES them. The L1 panel is already forbidden that
    (``optimization/CLAUDE.md``); selection is bound by the same rule.

    Cold ruler ⇒ the deterministic bank prefix, unchanged: a δ fit needs at least TWO arms or
    selecting on it is a difficulty ratchet, and freezing the subset is what lets the ruler warm.

    ``leader_ids`` names the arms this round is DECIDING BETWEEN, and the target θ is the best of
    those. *observations* deliberately carries the whole archive — a θ fit wants every arm — but the
    max over it is the best searchpoint ever run on the dataset, which is not in this race, and
    targeting it calibrates the panel to an arm nobody ran.
    """
    if budget <= 0 or not bank:
        return []
    if budget >= len(bank):
        return list(bank)
    if ruler is None or not ruler.delta:
        return list(bank[:budget])

    by_id = {int(s.id): s for s in bank}
    # A cell the ruler has not absorbed stands at the ruler's own centre with the population SE.
    delta_map = {sid: ruler.delta.get(sid, ruler.mu_delta) for sid in by_id}
    delta_se_map = {sid: ruler.delta_se.get(sid, ruler.sigma_delta) for sid in by_id}
    anchored = [o for o in observations if o.sample_id in ruler.delta]
    theta = fit_theta_given_delta(anchored, ruler.entries(), anchor_id=ruler.anchor_id)
    in_race = (
        [ts for cid, ts in theta.items() if cid in leader_ids]
        if leader_ids is not None
        else list(theta.values())
    )
    if in_race:
        leader_theta, leader_se = max(in_race, key=lambda ts: ts[0])
        leader_var = leader_se**2
    else:
        leader_theta, leader_var = 0.0, _INIT_SIGMA_THETA**2
    decided = decision_order(
        leader_theta,
        _INIT_SIGMA_THETA**2,
        leader_theta,
        leader_var,
        delta_map,
        delta_se_map,
        list(by_id),
    )
    ranked = _with_ruler_learning(decided, budget, delta_se_map)
    return [by_id[sid] for sid in _with_anchor_block(ranked, budget, ruler, anchor_floor)]


def _with_ruler_learning(
    decided: list[int], budget: int, delta_se_map: dict[int, float]
) -> list[int]:
    """Reserve the last ``_RULER_LEARNING_SLOTS`` of the panel for the cells δ is least sure of.

    The panel's job is to separate the arms, so the bulk is bought on decision information alone —
    but a pure-decision panel converges on one difficulty and STOPS BEING A READING (a band under
    ``BAND_COLLAPSE_LOGITS``, where θ is logit-accuracy plus a constant). A handful of max-SE cells
    holds the band open; more buys no further span and only makes the panel easier."""
    slots = min(_RULER_LEARNING_SLOTS, max(budget - 1, 0))
    if slots <= 0 or budget >= len(decided):
        return decided
    keep = decided[: budget - slots]
    held = set(keep)
    explore = sorted(
        (sid for sid in decided if sid not in held), key=lambda sid: (-delta_se_map[sid], sid)
    )
    return [*keep, *explore[:slots], *(sid for sid in decided[budget - slots :] if sid not in held)]


def _with_anchor_block(
    ranked: list[int], budget: int, ruler: DeltaRuler, anchor_floor: int
) -> list[int]:
    """Reserve enough already-anchored cells for the next extension to equate against.

    ``delta_learning_gain`` rises with δ's SE, so an unmeasured cell outranks a measured one by
    construction — correct while the ruler is cold, catastrophic once it is locked: the subset
    walks off the ruler entirely and extension has nothing left to link through. The acquisition
    ordering is left alone; only the tail is repaired, lowest-ranked first."""
    picked = ranked[:budget]
    floor = min(anchor_floor, len(ruler.delta.keys() & set(ranked)), budget)
    have = sum(1 for sid in picked if sid in ruler.delta)
    if have >= floor:
        return picked
    spare = [sid for sid in ranked[budget:] if sid in ruler.delta]
    out = list(picked)
    for i in range(len(out) - 1, -1, -1):
        if have >= floor or not spare:
            break
        if out[i] not in ruler.delta:
            out[i] = spare.pop(0)
            have += 1
    return out
