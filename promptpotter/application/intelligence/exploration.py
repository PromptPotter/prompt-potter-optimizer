"""Rasch + KG primitives + round-level scoring-set evolution.

Two related concerns share this module so the Rasch posterior and the
sample-swap policy that consumes it stay co-located:

1. **Rasch model + Knowledge Gradient** — fitting the IRT posterior
   (``RaschPosterior`` / ``fit_rasch_irt``) and computing per-sample KG
   used to rank exploration value. Pure-math primitives.

2. **Round-level scoring-set evolution** (``evolve_scoring_set``) —
   between rounds, swap understood samples (low δ_s SE) out of the active
   scoring set in favor of high-KG samples. Exploration / exploitation
   on which sample IDs are in play next round.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, NamedTuple

import numpy as np

if TYPE_CHECKING:
    from promptpotter.application.config import ExplorationConfig
    from promptpotter.domain.results import RoundResult
    from promptpotter.domain.sample import Sample

logger = logging.getLogger(__name__)


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


def _sigmoid(x: float) -> float:
    if x >= 0:
        ex = np.exp(-x)
        return float(1.0 / (1.0 + ex))
    ex = np.exp(x)
    return float(ex / (1.0 + ex))


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


def knowledge_gradient(
    posterior: RaschPosterior,
    candidate_id: str,
    sample_id: int,
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
    # Already-measured pairs are naturally discounted by the 1/info_new factor.
    expected_shift = p * abs(delta_if_hit) + (1.0 - p) * abs(delta_if_miss)
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


# ===========================================================================
# Round-level scoring-set evolution via Rasch + KG
# ===========================================================================

__all__ = [
    "EvolveResult",
    "build_observations",
    "build_scoring_set_event",
    "evolve_scoring_set",
    "seed_initial_scoring_set",
]


def seed_initial_scoring_set(
    observations: list[Observation],
    dataset: list[Sample],
    n: int,
) -> list[Sample] | None:
    """Pick the ``n`` hardest samples in ``dataset`` by δ_s from a Rasch fit on ``observations``.

    Returns ``None`` when ``observations`` is too shallow to yield a stable
    fit (caller falls back to the dataset-order prefix). Pure: no I/O.
    """
    if n <= 0 or not dataset or not observations:
        return None
    posterior = fit_rasch(observations)
    if not posterior.delta:
        return None
    by_id = {s.id: s for s in dataset}
    ranked_ids = [sid for sid, _ in sorted(posterior.delta.items(), key=lambda kv: -kv[1])]
    picked: list[Sample] = []
    for sid in ranked_ids:
        s = by_id.get(int(sid))
        if s is None:
            continue
        picked.append(s)
        if len(picked) >= n:
            break
    if not picked:
        return None
    if len(picked) < n:
        seen = {s.id for s in picked}
        for s in dataset:
            if s.id in seen:
                continue
            picked.append(s)
            if len(picked) >= n:
                break
    return picked


@dataclass
class EvolveResult:
    """Outcome of one ``evolve_scoring_set()`` call."""

    new_scoring_set: list[Sample]
    swapped_out: list[Sample] = field(default_factory=list)
    swapped_in: list[Sample] = field(default_factory=list)
    rasch: RaschPosterior | None = None
    reason: str = ""


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


def _select_swap_outs(
    posterior: RaschPosterior,
    scoring_set_sample_ids: set[int],
    config: ExplorationConfig,
    min_scoring_set_size: int,
    max_swaps: int,
) -> list[int]:
    """Samples in the scoring set whose δ_s SE is below the swap-out threshold.

    Sorted ascending by SE (most-understood first). Capped at
    ``max_swaps`` and bounded by ``min_scoring_set_size`` floor.
    """
    candidates = []
    for sid in scoring_set_sample_ids:
        se = posterior.delta_se.get(sid)
        if se is None:
            continue
        if se <= config.swap_out_delta_se:
            candidates.append((se, sid))
    candidates.sort()  # smallest SE first
    max_removable = max(0, len(scoring_set_sample_ids) - min_scoring_set_size)
    take = min(len(candidates), max_swaps, max_removable)
    return [sid for _, sid in candidates[:take]]


def _select_swap_ins(
    posterior: RaschPosterior,
    scoring_set_sample_ids: set[int],
    full_dataset: list[Sample],
    surviving_candidates: list[str],
    config: ExplorationConfig,
    n_slots: int,
) -> list[int]:
    """Samples not in the scoring set, ranked by max-over-candidates KG.

    Only considers samples already observed at least once (have a δ_s
    estimate). Cold sampling — pulling never-measured samples in via KG —
    is out of scope for v1 since their KG is undefined; future iterations
    can add a uniform-exploration tier.
    """
    if n_slots <= 0 or not surviving_candidates:
        return []

    scored: list[tuple[float, int]] = []
    for s in full_dataset:
        if s.id in scoring_set_sample_ids:
            continue
        if s.id not in posterior.delta:
            continue
        kg = sample_kg_max(posterior, s.id, surviving_candidates)
        if kg < config.swap_in_kg_threshold:
            continue
        scored.append((kg, s.id))

    scored.sort(reverse=True)
    return [sid for _, sid in scored[:n_slots]]


def build_scoring_set_event(
    *,
    round_num: int,
    result: EvolveResult,
    hardness_top_k: int = 5,
) -> dict:
    """Serialize an ``EvolveResult`` into the persistable per-round event dict."""
    rasch = result.rasch
    hardness: list[dict] = []
    rasch_summary: dict = {}
    if rasch is not None:
        sorted_by_delta = sorted(rasch.delta.items(), key=lambda kv: -kv[1])
        for sid, d in sorted_by_delta[:hardness_top_k]:
            se = rasch.delta_se.get(sid, 0.0)
            hardness.append(
                {
                    "sample_id": int(sid),
                    "delta": float(d),
                    "ci_width": float(2.0 * 1.96 * se),
                    "n_obs": int(rasch.n_obs_per_sample.get(sid, 0)),
                }
            )
        rasch_summary = {
            "n_candidates": len(rasch.theta),
            "n_samples": len(rasch.delta),
            "iterations": int(rasch.n_iterations),
            "converged": bool(rasch.converged),
        }
    return {
        "round": int(round_num),
        "reason": result.reason,
        "swapped_out": [int(s.id) for s in result.swapped_out],
        "swapped_in": [int(s.id) for s in result.swapped_in],
        "new_scoring_set_size": len(result.new_scoring_set),
        "rasch": rasch_summary,
        "hardness_top": hardness,
    }


def evolve_scoring_set(
    full_dataset: list[Sample],
    current_scoring_set: list[Sample],
    rounds: list[RoundResult],
    config: ExplorationConfig,
    *,
    elimination_n_min: int,
    surviving_candidates: list[str] | None = None,
    extra_observations: list[Observation] = (),  # type: ignore[assignment]
) -> EvolveResult:
    """Decide the next round's scoring set. Pure — no I/O, no mutation of inputs.

    When there are no completed rounds yet, or when the dataset has
    nothing left to swap in, returns the current scoring set unchanged
    with ``reason`` populated for telemetry.

    ``extra_observations`` (default empty) is prepended to the live
    observations before the Rasch fit — used to fold cross-cycle archive
    evidence into the per-round swap decisions when
    ``exploration.seed_evolve_from_archive`` is on.
    """
    if not rounds:
        return EvolveResult(new_scoring_set=list(current_scoring_set), reason="no_rounds_yet")

    observations = list(extra_observations) + build_observations(rounds)
    if not observations:
        return EvolveResult(new_scoring_set=list(current_scoring_set), reason="no_observations")

    posterior = fit_rasch(
        observations,
        theta_prior_sigma=config.cold_start_prior_sigma,
    )

    if surviving_candidates is None:
        surviving_candidates = list(posterior.theta.keys())

    min_size = elimination_n_min
    scoring_set_ids = {s.id for s in current_scoring_set}

    swap_out_ids = _select_swap_outs(
        posterior, scoring_set_ids, config, min_size, config.max_swaps_per_round
    )
    swap_in_ids = _select_swap_ins(
        posterior,
        scoring_set_ids,
        full_dataset,
        surviving_candidates,
        config,
        n_slots=len(swap_out_ids),
    )

    # Pair-wise swap: only swap as many as we have viable replacements for.
    k = min(len(swap_out_ids), len(swap_in_ids))
    if k == 0:
        return EvolveResult(
            new_scoring_set=list(current_scoring_set),
            rasch=posterior,
            reason="no_viable_swap",
        )
    swap_out_ids = swap_out_ids[:k]
    swap_in_ids = swap_in_ids[:k]

    out_ids_set = set(swap_out_ids)
    by_id = {s.id: s for s in full_dataset}
    new_scoring_set = [s for s in current_scoring_set if s.id not in out_ids_set]
    swapped_in_samples = [by_id[sid] for sid in swap_in_ids if sid in by_id]
    new_scoring_set.extend(swapped_in_samples)
    swapped_out_samples = [s for s in current_scoring_set if s.id in out_ids_set]

    logger.info(
        "ScoringSet: round-end swap of %d sample(s). out=%s in=%s",
        k,
        [s.id for s in swapped_out_samples],
        [s.id for s in swapped_in_samples],
    )
    return EvolveResult(
        new_scoring_set=new_scoring_set,
        swapped_out=swapped_out_samples,
        swapped_in=swapped_in_samples,
        rasch=posterior,
        reason="swapped",
    )
