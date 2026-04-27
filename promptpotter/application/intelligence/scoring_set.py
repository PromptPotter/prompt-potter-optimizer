"""Round-level scoring-set evolution via Rasch + KG.

Between rounds, swap understood samples (low δ_s SE) out of the active
scoring set in favor of high-Knowledge-Gradient samples — exploration /
exploitation on which sample IDs are in play next round.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from promptpotter.application.intelligence.rasch import (
    Observation,
    RaschPosterior,
    fit_rasch,
    sample_kg_max,
)

if TYPE_CHECKING:
    from promptpotter.application.campaign.config import ScoringSetConfig
    from promptpotter.application.optimization.results import RoundResult
    from promptpotter.domain.sample import Sample

logger = logging.getLogger(__name__)

__all__ = ["EvolveResult", "build_observations", "build_scoring_set_event", "evolve_scoring_set"]


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
    flagged as errors via the QueryResult contract.
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
    config: ScoringSetConfig,
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
    config: ScoringSetConfig,
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
    config: ScoringSetConfig,
    *,
    elimination_n_min: int,
    surviving_candidates: list[str] | None = None,
) -> EvolveResult:
    """Decide the next round's scoring set. Pure — no I/O, no mutation of inputs.

    When the feature is disabled, when there are no completed rounds yet,
    or when the dataset has nothing left to swap in, returns the current
    scoring set unchanged with ``reason`` populated for telemetry.
    """
    if not config.enabled:
        return EvolveResult(new_scoring_set=list(current_scoring_set), reason="disabled")
    if not rounds:
        return EvolveResult(new_scoring_set=list(current_scoring_set), reason="no_rounds_yet")

    observations = build_observations(rounds)
    if not observations:
        return EvolveResult(new_scoring_set=list(current_scoring_set), reason="no_observations")

    posterior = fit_rasch(
        observations,
        theta_prior_sigma=config.cold_start_prior_sigma,
    )

    if surviving_candidates is None:
        surviving_candidates = list(posterior.theta.keys())

    min_size = (
        config.min_scoring_set_size
        if config.min_scoring_set_size is not None
        else elimination_n_min
    )
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
