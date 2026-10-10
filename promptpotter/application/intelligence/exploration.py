from __future__ import annotations

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
from promptpotter.shared.hashing import stable_hash

if TYPE_CHECKING:
    from promptpotter.domain.results import RoundResult
    from promptpotter.domain.sample import Sample
    from promptpotter.domain.scoring import CellSheet, GradedCell

logger = logging.getLogger(__name__)

# C0 inside the δ-calibrating fit; the archive's copies of the origin fold onto this one id.
ORIGIN_ABILITY_ID = "__origin__"

__all__ = [
    "ORIGIN_ABILITY_ID",
    "Observation",
    "RaschPosterior",
    "RoundAbilities",
    "build_observations",
    "candidate_abilities",
    "dedup_observations",
    "extend_ruler",
    "fit_rasch",
    "fit_rasch_2pl",
    "fit_theta",
    "fit_theta_given_delta",
    "graded_response",
    "graduate_ruler_model",
    "observations_from_results",
    "parent_level_trajectory",
    "responses_of",
    "select_round_subset",
    "theta_bounds_given_delta",
    "theta_lift_over_parent",
]


class Observation(NamedTuple):
    """``response`` is the GRADED fitness in [0,1], never a binarized hit; ``sample_id`` is a CELL id."""

    candidate_id: str
    sample_id: int
    response: float


def graded_response(cell: GradedCell) -> float:
    """``objective``, never ``fitness``: the one place a formula's cost term reaches the election."""
    if cell.grade.objective is None:
        raise KeyError(
            f"graded_response: the cell at slot {cell.sample_id} carries no objective. Only a "
            "cell ``Scorer.grade`` scored may be read here."
        )
    return min(max(cell.grade.objective, 0.0), 1.0)


def responses_of(sheet: CellSheet) -> dict[int, float]:
    return {cell.ruler_key: graded_response(cell) for cell in sheet.scoreable}


def parent_level_trajectory(
    origin: AbilityReading | None,
    winners: Sequence[AbilityReading | None],
    ruler: DeltaRuler | None,
) -> tuple[tuple[float, float] | None, list[tuple[float, float]]]:
    """The PARENT's level per round; a round off the origin's scale carries the previous one forward."""
    if origin is None or origin.se is None or ruler is None or not ruler.delta:
        return None, []
    if origin.ruler_id != ruler.anchor_id:
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


_INIT_SIGMA_THETA = 1.5
_INIT_SIGMA_DELTA = 2.0

# Weak inverse-gamma hyperprior on each variance: stops σ → 0 under sparse data.
_EB_NU0 = 1.0
_EB_S0_SQ = 1.0

# Where p saturates, an unbounded step jumps into the opposite saturation, hardest on the arms furthest above centre.
_MAX_NEWTON_STEP = 1.0

# Panel slots held for the cells δ is least sure of; a first estimate off one ruler.
_RULER_LEARNING_SLOTS = 4


def _newton_step(
    grad: NDArray[np.floating[Any]] | float, info: NDArray[np.floating[Any]] | float
) -> NDArray[np.floating[Any]]:
    return np.clip(grad / np.maximum(info, 1e-9), -_MAX_NEWTON_STEP, _MAX_NEWTON_STEP)


@dataclass
class RaschPosterior:
    """MAP + Laplace-SE for a hierarchical Rasch fit. ``mean(theta) == 0``; ``sigma_*`` / ``mu_delta`` are EB-estimated."""

    theta: dict[str, float]
    theta_se: dict[str, float]
    delta: dict[int, float]
    delta_se: dict[int, float]
    n_iterations: int = 0
    converged: bool = False
    sigma_theta: float = _INIT_SIGMA_THETA
    sigma_delta: float = _INIT_SIGMA_DELTA
    mu_delta: float = 0.0
    # Empty under 1PL (a ≡ 1).
    discrimination: dict[int, float] = field(default_factory=dict)
    discrimination_se: dict[int, float] = field(default_factory=dict)

    def anchored(self, calibration_model: CalibrationModel) -> DeltaRuler:
        """Carries the fit's priors, without which an extension bends the scale; the anchor id is stamped once, here."""
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


@dataclass
class RoundAbilities(RaschPosterior):
    """``parent`` is its ``(θ, se)`` on the arms' δ; ``None`` is an absence, never a logit-0 floor."""

    parent: tuple[float, float] | None = None


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

        grad_theta = np.bincount(rows, weights=responses - p, minlength=n_c) - inv_var_theta * theta
        info_theta = np.bincount(rows, weights=w, minlength=n_c) + inv_var_theta
        theta = theta + _newton_step(grad_theta, info_theta)

        eta = theta[rows] - delta[cols]
        p = 1.0 / (1.0 + np.exp(-np.clip(eta, -50, 50)))
        w = p * (1.0 - p)

        grad_delta = -np.bincount(cols, weights=responses - p, minlength=n_s) - inv_var_delta * (
            delta - mu_delta
        )
        info_delta = np.bincount(cols, weights=w, minlength=n_s) + inv_var_delta
        delta = delta + _newton_step(grad_delta, info_delta)

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

    theta, delta, se_theta, se_delta, iteration, converged = _map_fit(
        rows, cols, responses, n_c, n_s, sigma_theta, sigma_delta, mu_delta, max_iter, tol
    )

    return RaschPosterior(
        theta=dict(zip(candidate_ids, theta.tolist(), strict=True)),
        theta_se=dict(zip(candidate_ids, se_theta.tolist(), strict=True)),
        delta=dict(zip(sample_ids, delta.tolist(), strict=True)),
        delta_se=dict(zip(sample_ids, se_delta.tolist(), strict=True)),
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
    max_iter: int = 50,
    tol: float = 1e-4,
) -> dict[str, tuple[float, float]]:
    """``delta=None`` is the cold ruler (θ = logit-accuracy); a cell off a live ruler enters no fit, never at δ=0."""
    graded = _cell_parameters({o.sample_id for o in observations}, delta)

    by_c: dict[str, list[tuple[float, float, float]]] = {}
    for o in observations:
        if (cell := graded.get(o.sample_id)) is None:
            continue
        by_c.setdefault(o.candidate_id, []).append((*cell, o.response))

    return {
        cid: _fit_one(rows, sigma_theta=sigma_theta, max_iter=max_iter, tol=tol)
        for cid, rows in by_c.items()
    }


def fit_theta(
    responses: Mapping[int, float],
    delta: Ruler | None,
    *,
    sigma_theta: float = _INIT_SIGMA_THETA,
) -> tuple[float, float] | None:
    graded = _cell_parameters(responses.keys(), delta)
    rows = [(*graded[sid], y) for sid, y in responses.items() if sid in graded]
    return _fit_one(rows, sigma_theta=sigma_theta, max_iter=50, tol=1e-4) if rows else None


def _fit_one(
    rows: Sequence[tuple[float, float, float]], *, sigma_theta: float, max_iter: int, tol: float
) -> tuple[float, float]:
    inv_var = 1.0 / (sigma_theta * sigma_theta)
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
    # Never a pooled φ̄: that makes an arm's SE depend on which arms shared its round.
    var = np.clip(p * (1.0 - p), 1e-6, None)
    dof = max(len(rows) - 1, 1)
    raw_phi = float(np.sum((h_arr - p) ** 2 / var)) / dof
    phi = (dof * raw_phi + _EB_NU0 * _EB_S0_SQ) / (dof + _EB_NU0)
    return theta, float(np.sqrt(phi) / np.sqrt(max(info, 1e-9)))


def _cell_parameters(
    sample_ids: Collection[int], delta: Ruler | None
) -> dict[int, tuple[float, float]]:
    if delta is None:
        return dict.fromkeys(sample_ids, (0.0, 1.0))
    return {sid: ruler_entry(delta[sid]) for sid in sample_ids if sid in delta}


# The Newton fit stops within `tol` of its root, so two fits are never compared tighter than this.
_FIT_SLACK = 1e-3


def theta_bounds_given_delta(
    measured: Mapping[int, float],
    open_cells: Collection[int],
    delta: Ruler | None,
) -> tuple[float, float, float]:
    """The MAP rises with every response, so the all-0 and all-1 fills bound θ; the SE is only FLOORED."""
    params = _cell_parameters({*measured, *open_cells}, delta)

    def fit(fill: float) -> float:
        filled = {**measured, **dict.fromkeys(open_cells, fill)}
        fitted = fit_theta(filled, delta)
        if fitted is None:
            raise ValueError("theta_bounds_given_delta: no cell on the ruler to bound θ over")
        return fitted[0]

    low, high = fit(0.0) - _FIT_SLACK, fit(1.0) + _FIT_SLACK

    def p_at(theta: NDArray[np.float64], d: NDArray[np.float64], a: NDArray[np.float64]) -> Any:
        return 1.0 / (1.0 + np.exp(-np.clip(a * (theta - d), -50, 50)))

    d, a = (np.array([params[s][k] for s in params], dtype=np.float64) for k in (0, 1))
    p = p_at(np.clip(d, low, high), d, a)
    info = 1.0 / (_INIT_SIGMA_THETA * _INIT_SIGMA_THETA) + float(np.sum(a * a * p * (1.0 - p)))
    on = [s for s in measured if s in params]
    y = np.fromiter((measured[s] for s in on), dtype=np.float64, count=len(on))
    dm, am = (np.array([params[s][k] for s in on], dtype=np.float64) for k in (0, 1))
    ends = p_at(np.array([low, high]), dm[:, None], am[:, None])
    pm = np.clip(y, ends.min(axis=1), ends.max(axis=1))
    misfit = float(np.sum((y - pm) ** 2 / np.clip(pm * (1.0 - pm), 1e-6, None)))
    dof = max(len(params) - 1, 1)
    phi = (misfit + _EB_NU0 * _EB_S0_SQ) / (dof + _EB_NU0)
    return low, high, float(np.sqrt(phi) / np.sqrt(info))


def extend_ruler(
    ruler: DeltaRuler,
    observations: list[Observation],
    *,
    history: list[Observation],
    max_iter: int = 50,
    tol: float = 1e-4,
) -> DeltaRuler:
    """Fixed-common-item link: a written δ never moves or re-anchors, and a cell no anchored arm answered stays OFF it."""
    seen = dedup_observations(history, observations)
    inv_var = 1.0 / (ruler.sigma_delta * ruler.sigma_delta)
    while True:
        # `_INIT_SIGMA_THETA`, not `ruler.sigma_theta`: regularized as the election's own θ read is.
        theta = fit_theta_given_delta(seen, ruler.entries())

        by_s: dict[int, list[tuple[float, float]]] = {}
        for o in seen:
            arm = theta.get(o.candidate_id)
            if o.sample_id in ruler.delta or arm is None:
                continue
            by_s.setdefault(o.sample_id, []).append((arm[0], o.response))
        if not by_s:
            return ruler

        delta = dict(ruler.delta)
        delta_se = dict(ruler.delta_se)
        for sid, rows in by_s.items():
            t_arr = np.fromiter((t for t, _ in rows), dtype=np.float64)
            y_arr = np.fromiter((y for _, y in rows), dtype=np.float64)
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

        # A new cell keeps a ≡ 1 even under 2PL: one round's arms cannot identify a discrimination.
        ruler = ruler.model_copy(update={"delta": delta, "delta_se": delta_se})


# log(aₛ) ~ N(0, σ_a²): collapses 2PL to 1PL absent evidence and identifies a against θ/δ spread.
_SIGMA_LOG_A = 0.5
_LOG_A_CLIP = 3.0


def fit_rasch_2pl(
    observations: list[Observation],
    *,
    max_iter: int = 100,
    tol: float = 1e-4,
) -> RaschPosterior:
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

        p = 1.0 / (1.0 + np.exp(-np.clip(a[cols] * (theta[rows] - delta[cols]), -50, 50)))
        grad_t = (
            np.bincount(rows, weights=a[cols] * (responses - p), minlength=n_c)
            - inv_var_theta * theta
        )
        info_t = (
            np.bincount(rows, weights=a[cols] ** 2 * p * (1 - p), minlength=n_c) + inv_var_theta
        )
        theta = theta + _newton_step(grad_t, info_t)

        p = 1.0 / (1.0 + np.exp(-np.clip(a[cols] * (theta[rows] - delta[cols]), -50, 50)))
        grad_d = -np.bincount(
            cols, weights=a[cols] * (responses - p), minlength=n_s
        ) - inv_var_delta * (delta - mu_delta)
        info_d = (
            np.bincount(cols, weights=a[cols] ** 2 * p * (1 - p), minlength=n_s) + inv_var_delta
        )
        delta = delta + _newton_step(grad_d, info_d)

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
    se_a = a * se_log_a

    return RaschPosterior(
        theta=dict(zip(candidate_ids, theta.tolist(), strict=True)),
        theta_se=dict(zip(candidate_ids, se_theta.tolist(), strict=True)),
        delta=dict(zip(sample_ids, delta.tolist(), strict=True)),
        delta_se=dict(zip(sample_ids, se_delta.tolist(), strict=True)),
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
            post.theta[o.candidate_id],
            post.delta[o.sample_id],
            post.discrimination.get(o.sample_id, 1.0),
        )
        for o in observations
    )


def _fold_of(o: Observation, n_folds: int) -> int:
    """Hashed on the observation's identity, never its position: stride folds follow walk order."""
    return int(stable_hash([o.candidate_id, o.sample_id]), 16) % n_folds


def _cv_loglik(observations: list[Observation], n_folds: int) -> tuple[float, float] | None:
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
                f2.theta[o.candidate_id],
                f2.delta[o.sample_id],
                f2.discrimination[o.sample_id],
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
    base = fit_rasch(observations)
    if not enable or len(base.delta) < 2:
        return "1PL", base

    full_2pl = fit_rasch_2pl(observations)
    n_obs, n_s = len(observations), len(full_2pl.delta)
    bic_gain = 2.0 * (_full_loglik(observations, full_2pl) - _full_loglik(observations, base))
    if bic_gain <= n_s * float(np.log(max(n_obs, 2))):
        return "1PL", base

    cv = _cv_loglik(observations, n_folds)
    if cv is None:
        return "1PL", base
    ll_1, ll_2 = cv
    n_eval_proxy = max(n_obs // n_folds, 1)
    if (ll_2 - ll_1) > margin * n_eval_proxy:
        return "2PL", full_2pl
    return "1PL", base


def observations_from_results(sheets_by_id: Mapping[str, CellSheet]) -> list[Observation]:
    return [
        Observation(candidate_id=cid, sample_id=sid, response=response)
        for cid, sheet in sheets_by_id.items()
        for sid, response in responses_of(sheet).items()
    ]


def build_observations(rounds: list[RoundResult]) -> list[Observation]:
    return [o for rr in rounds for o in observations_from_results(rr.all_candidate_results)]


def dedup_observations(*groups: Sequence[Observation]) -> list[Observation]:
    """LAST wins, so callers pass groups oldest-first; every path into a fit passes through here."""
    cells: dict[tuple[str, int], Observation] = {}
    for group in groups:
        for o in group:
            cells[(o.candidate_id, o.sample_id)] = o
    return list(cells.values())


def candidate_abilities(
    results_by_id: Mapping[str, CellSheet],
    parent_results: CellSheet,
    ruler: DeltaRuler | None,
) -> RoundAbilities:
    """``parent_results`` is the parent RE-SCORED on this round's panel, never C0's banked rows."""
    entries = ruler.entries() if ruler is not None else None
    fit = fit_theta_given_delta(observations_from_results(results_by_id), entries)
    split = {sid: ruler_entry(v) for sid, v in (entries or {}).items()}
    return RoundAbilities(
        theta={cid: t for cid, (t, _) in fit.items()},
        theta_se={cid: se for cid, (_, se) in fit.items()},
        delta={sid: d for sid, (d, _) in split.items()},
        delta_se={},
        discrimination={sid: a for sid, (_, a) in split.items() if a != 1.0},
        parent=fit_theta(responses_of(parent_results), entries),
    )


def theta_lift_over_parent(abilities: RoundAbilities, candidate_id: str) -> float | None:
    theta_c = abilities.theta.get(candidate_id)
    if theta_c is None or abilities.parent is None:
        return None
    return theta_c - abilities.parent[0]


def select_round_subset(
    bank: list[Sample],
    observations: list[Observation],
    budget: int,
    *,
    ruler: DeltaRuler | None = None,
    anchor_floor: int = 0,
    leader_ids: Collection[str] | None = None,
) -> list[Sample]:
    """Never ``fit_rasch`` here: the δ that CHOOSES the cells is the locked δ that SCORES them."""
    # `leader_ids` bounds the target θ to this race: *observations* carries the whole archive.
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
    theta = fit_theta_given_delta(observations, ruler.entries())
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
    """A pure-decision panel converges on one difficulty, where θ is logit-accuracy plus a constant."""
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
    """Unmeasured cells outrank anchored ones by construction, so a locked ruler's subset walks off it."""
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
