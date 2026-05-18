"""Hard-sample-sorter — cross-cut view over (candidate, sample, hit) observations.

First-class capability: given a dataset + a handful of candidate prompts, produce
a θ_c-ranked candidate list and a δ_s-ranked sample difficulty list, joined into
a candidate × sample hit/miss/unmeasured matrix. Works standalone — no optimizer
loop required. Full spec: ``docs/specs/hard-sample-sorter.md``.

``build_hard_samples_artifact(rounds, ...)`` fits Rasch, resolves the spec's
axis sort contract, and emits the dict that the CLI renderer, the FastAPI
endpoint, and the webapp heatmap all consume. Caps to top-K on disk; pass
``top_k_*=None`` for the full matrix.

Pure read-only — no I/O, no mutation of inputs. Reuses ``build_observations``
so the error-flag and missing-sample-id policy is defined in exactly one place.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING

from promptpotter.application.intelligence.exploration import build_observations, fit_rasch

if TYPE_CHECKING:
    from promptpotter.application.intelligence.exploration import Observation, RaschPosterior
    from promptpotter.domain.results import RoundResult

__all__ = [
    "ARTIFACT_SCHEMA_VERSION",
    "build_hard_samples_artifact",
    "build_hard_samples_artifact_from_observations",
    "empty_artifact",
]

ARTIFACT_SCHEMA_VERSION = 2


def _candidate_hit_rates(observations: list[Observation]) -> dict[str, float]:
    """Mean hit rate per candidate over measured cells. Tie-breaker for θ_c."""
    totals: dict[str, int] = {}
    hits: dict[str, int] = {}
    for o in observations:
        totals[o.candidate_id] = totals.get(o.candidate_id, 0) + 1
        if o.hit:
            hits[o.candidate_id] = hits.get(o.candidate_id, 0) + 1
    return {cid: hits.get(cid, 0) / n for cid, n in totals.items() if n > 0}


def _sample_miss_rates(observations: list[Observation]) -> dict[int, float]:
    """Mean miss rate per sample over measured cells. Tie-breaker for δ_s."""
    totals: dict[int, int] = {}
    misses: dict[int, int] = {}
    for o in observations:
        totals[o.sample_id] = totals.get(o.sample_id, 0) + 1
        if not o.hit:
            misses[o.sample_id] = misses.get(o.sample_id, 0) + 1
    return {sid: misses.get(sid, 0) / n for sid, n in totals.items() if n > 0}


def _sample_discrimination(observations: list[Observation]) -> dict[int, float]:
    """Per-sample cross-candidate hit-rate variance — ``p*(1-p)``, max at p=0.5.

    The PoBB consumer iterates samples in descending-discrimination order
    so paired posteriors separate fast: samples where candidates split
    ~50/50 carry the most information about which prompt is better.
    Always-hit and always-miss samples produce 0 — scoring them yields
    zero between-candidate signal and the posterior stays overlapping.

    A sample observed only once still gets ``p*(1-p)`` (often 0); the
    tie-breaker by |δ_s| in :func:`_resolve_discrimination_order` keeps
    those samples behind anything with real variance.
    """
    totals: dict[int, int] = {}
    hits: dict[int, int] = {}
    for o in observations:
        totals[o.sample_id] = totals.get(o.sample_id, 0) + 1
        if o.hit:
            hits[o.sample_id] = hits.get(o.sample_id, 0) + 1
    out: dict[int, float] = {}
    for sid, n in totals.items():
        p = hits.get(sid, 0) / n
        out[sid] = p * (1.0 - p)
    return out


def _resolve_candidate_order(
    posterior: RaschPosterior,
    hit_rates: dict[str, float],
) -> list[str]:
    """Y-axis: desc θ_c; tie → desc mean hit rate; final tie → cid lex ascending."""
    return sorted(
        posterior.theta.keys(),
        key=lambda cid: (-posterior.theta[cid], -hit_rates.get(cid, 0.0), cid),
    )


def _resolve_sample_order(
    posterior: RaschPosterior,
    miss_rates: dict[int, float],
) -> list[int]:
    """X-axis: desc δ_s (hardest left); tie → desc miss rate; final tie → sid asc."""
    return sorted(
        posterior.delta.keys(),
        key=lambda sid: (-posterior.delta[sid], -miss_rates.get(sid, 0.0), sid),
    )


def _resolve_discrimination_order(
    discrimination: dict[int, float],
    rasch_delta: dict[int, float],
) -> list[int]:
    """Sort sample IDs by descending discrimination (``p*(1-p)``).

    Ties break by descending |δ_s| (hardest within the same discrimination
    bucket — preserves the diagnostic-first intent for samples no prior
    has split yet), then by ascending sample id for determinism.

    Consumed by ``score_population`` to drive PoBB's iteration order;
    distinct from the heatmap's hardest-first ``sample_order`` because
    PoBB needs between-candidate variance, not absolute difficulty.
    """
    return sorted(
        discrimination.keys(),
        key=lambda sid: (-discrimination[sid], -abs(rasch_delta.get(sid, 0.0)), sid),
    )


def empty_artifact(
    *,
    cycle_id: str | None = None,
    disabled: bool = False,
) -> dict:
    """Schema-valid stub artifact for zero-observation or disabled campaigns.

    Renderers short-circuit on ``n_observations == 0``; persisting a stub keeps
    ``CAMPAIGN_ARTIFACTS`` parity without a special-case read path.
    """
    return {
        "schema_version": ARTIFACT_SCHEMA_VERSION,
        "cycle_id": cycle_id,
        "generated_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "disabled": disabled,
        "truncated": False,
        "total_candidates": 0,
        "total_samples": 0,
        "n_candidates": 0,
        "n_samples": 0,
        "n_observations": 0,
        "candidate_order": [],
        "sample_order": [],
        "rasch": {},
        "discrimination": {"per_sample": {}, "sample_order": []},
        "cells": [],
    }


def build_hard_samples_artifact(
    rounds: list[RoundResult],
    *,
    cycle_id: str | None = None,
    top_k_candidates: int | None = 40,
    top_k_samples: int | None = 40,
    posterior: RaschPosterior | None = None,
) -> dict:
    """Build the hard-samples artifact dict from round observations.

    Thin wrapper over :func:`build_hard_samples_artifact_from_observations`
    that projects ``cycle.rounds`` into ``(candidate, sample, hit)`` triples
    via :func:`build_observations` first. If ``posterior`` is supplied (e.g.
    cached from the round-end ``evolve_scoring_set`` call), the redundant
    refit is skipped — the heatmap and the scoring-set evolution share one
    Rasch fit per round.

    Pure function: no I/O.
    """
    return build_hard_samples_artifact_from_observations(
        build_observations(rounds),
        cycle_id=cycle_id,
        top_k_candidates=top_k_candidates,
        top_k_samples=top_k_samples,
        posterior=posterior,
    )


def build_hard_samples_artifact_from_observations(
    observations: list[Observation],
    *,
    cycle_id: str | None = None,
    top_k_candidates: int | None = 40,
    top_k_samples: int | None = 40,
    posterior: RaschPosterior | None = None,
) -> dict:
    """Same as :func:`build_hard_samples_artifact` but takes pre-built
    observations directly. Used by the cross-cycle archive aggregator
    (``hard_sample_archive``) which builds observations from the
    ``MeasurementArchive`` instead of a single cycle's ``rounds``.

    Fits Rasch on all observations (so θ_c and δ_s reflect the full evidence)
    unless a pre-computed ``posterior`` is supplied; then truncates the
    persisted axes to the top-K on the spec's sort contract. Cells are
    restricted to the intersection ``(c ∈ candidate_order × s ∈
    sample_order)``. Pass ``top_k_*=None`` to disable capping — the caller
    gets the full matrix without any second code path.

    Pure function: no I/O, no mutation of ``observations``.
    """
    if not observations:
        return empty_artifact(cycle_id=cycle_id)

    if posterior is None:
        posterior = fit_rasch(observations)
    hit_rates = _candidate_hit_rates(observations)
    miss_rates = _sample_miss_rates(observations)
    discrimination_map = _sample_discrimination(observations)

    full_candidate_order = _resolve_candidate_order(posterior, hit_rates)
    full_sample_order = _resolve_sample_order(posterior, miss_rates)
    full_discrimination_order = _resolve_discrimination_order(discrimination_map, posterior.delta)

    total_candidates = len(full_candidate_order)
    total_samples = len(full_sample_order)

    candidate_order = (
        full_candidate_order
        if top_k_candidates is None
        else full_candidate_order[:top_k_candidates]
    )
    sample_order = full_sample_order if top_k_samples is None else full_sample_order[:top_k_samples]
    discrimination_order = (
        full_discrimination_order
        if top_k_samples is None
        else full_discrimination_order[:top_k_samples]
    )

    cand_set = set(candidate_order)
    samp_set = set(sample_order)

    cells = [
        {"c": o.candidate_id, "s": int(o.sample_id), "hit": bool(o.hit)}
        for o in observations
        if o.candidate_id in cand_set and o.sample_id in samp_set
    ]

    rasch_view = {
        "converged": bool(posterior.converged),
        "iterations": int(posterior.n_iterations),
        "theta": {cid: float(posterior.theta[cid]) for cid in candidate_order},
        "theta_se": {cid: float(posterior.theta_se.get(cid, 0.0)) for cid in candidate_order},
        # JSON object keys must be strings — consumers cast back to int.
        "delta": {str(sid): float(posterior.delta[sid]) for sid in sample_order},
        "delta_se": {str(sid): float(posterior.delta_se.get(sid, 0.0)) for sid in sample_order},
        "n_obs_per_candidate": {
            cid: int(posterior.n_obs_per_candidate.get(cid, 0)) for cid in candidate_order
        },
        "n_obs_per_sample": {
            str(sid): int(posterior.n_obs_per_sample.get(sid, 0)) for sid in sample_order
        },
    }

    truncated = len(candidate_order) < total_candidates or len(sample_order) < total_samples

    return {
        "schema_version": ARTIFACT_SCHEMA_VERSION,
        "cycle_id": cycle_id,
        "generated_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "disabled": False,
        "truncated": truncated,
        "total_candidates": total_candidates,
        "total_samples": total_samples,
        "n_candidates": len(candidate_order),
        "n_samples": len(sample_order),
        "n_observations": len(cells),
        "candidate_order": candidate_order,
        "sample_order": sample_order,
        "rasch": rasch_view,
        "discrimination": {
            "per_sample": {
                str(sid): float(discrimination_map.get(sid, 0.0)) for sid in sample_order
            },
            "sample_order": [int(sid) for sid in discrimination_order],
        },
        "cells": cells,
    }
