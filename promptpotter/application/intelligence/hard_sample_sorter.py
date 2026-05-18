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

import math
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

ARTIFACT_SCHEMA_VERSION = 3


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


def _chernoff_bernoulli(p: float, q: float) -> float:
    """Chernoff information between two Bernoulli distributions.

    ``C(p, q) = max_{λ∈[0,1]} -log[ p^λ q^{1-λ} + (1-p)^λ (1-q)^{1-λ} ]``.

    Zero iff ``p == q``; positive otherwise. The exponent of the
    asymptotic error rate in optimal binary hypothesis testing — the
    Track-and-Stop (Garivier-Kaufmann 2016) information measure for
    best-arm identification on Bernoulli arms. No closed form for λ*;
    99-point grid is sub-1% precision and trivially cheap at the
    per-candidate fire rate.
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


def _chernoff_information(
    observations: list[Observation],
    seed_hits: dict[int, list[bool]] | None,
) -> dict[int, float]:
    """Per-sample Chernoff info distinguishing the seed prior from the population.

    For each sample, we ask: how much does measuring this sample
    discriminate "this new candidate behaves like the seed prior" from
    "this new candidate behaves like the cross-candidate population"?
    That is the Bernoulli Chernoff information ``C(p_seed, p_pop)``.

    Both rates use a Laplace ``Beta(1,1)`` prior so always-miss + never-
    seen samples still carry signal (Nieena: an always-miss sample where
    a new candidate hits is a huge surprise; the smoothed estimate
    preserves that asymmetry). Without smoothing the formula collapses
    to 0 on the always-miss tier — which is the bug the old
    ``p*(1-p)`` formula encoded and that this picker exists to fix.

    Cold-start fallback: when no seed history is provided (``seed_hits``
    empty or ``None``), drops to ``p_pop·(1-p_pop)`` — the population-
    variance picker, equivalent to the old behaviour. Origin is always
    scored before R1's candidates so the live loop never hits this path;
    it's there for standalone artifact builds with no seed declared.
    """
    pop_totals: dict[int, int] = {}
    pop_hits: dict[int, int] = {}
    for o in observations:
        pop_totals[o.sample_id] = pop_totals.get(o.sample_id, 0) + 1
        if o.hit:
            pop_hits[o.sample_id] = pop_hits.get(o.sample_id, 0) + 1

    out: dict[int, float] = {}
    if not seed_hits:
        for sid, n in pop_totals.items():
            p = pop_hits.get(sid, 0) / n
            out[sid] = p * (1.0 - p)
        return out

    for sid, n in pop_totals.items():
        p_pop = (pop_hits.get(sid, 0) + 1.0) / (n + 2.0)
        seq = seed_hits.get(sid, [])
        p_seed = (sum(1 for h in seq if h) + 1.0) / (len(seq) + 2.0)
        out[sid] = _chernoff_bernoulli(p_seed, p_pop)
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


def _resolve_pick_order(
    pick_score: dict[int, float],
    rasch_delta: dict[int, float],
) -> list[int]:
    """Sort sample IDs by descending Chernoff info against the seed prior.

    Ties break by descending |δ_s| (hardest within the same pick-score
    bucket — preserves the diagnostic-first intent for samples no prior
    has split yet), then by ascending sample id for determinism.

    Consumed by ``score_population`` to drive PoBB's iteration order;
    distinct from the heatmap's hardest-first ``sample_order`` because
    PoBB needs sample-by-sample evidence that separates the candidate
    from the seed prior, not absolute difficulty.
    """
    return sorted(
        pick_score.keys(),
        key=lambda sid: (-pick_score[sid], -abs(rasch_delta.get(sid, 0.0)), sid),
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
        "pick_score": {"per_sample": {}, "sample_order": []},
        "cells": [],
    }


def build_hard_samples_artifact(
    rounds: list[RoundResult],
    *,
    cycle_id: str | None = None,
    top_k_candidates: int | None = 40,
    top_k_samples: int | None = 40,
    posterior: RaschPosterior | None = None,
    seed_hits: dict[int, list[bool]] | None = None,
) -> dict:
    """Build the hard-samples artifact dict from round observations.

    Thin wrapper over :func:`build_hard_samples_artifact_from_observations`
    that projects ``cycle.rounds`` into ``(candidate, sample, hit)`` triples
    via :func:`build_observations` first. If ``posterior`` is supplied (e.g.
    cached from the round-end ``evolve_scoring_set`` call), the redundant
    refit is skipped — the heatmap and the scoring-set evolution share one
    Rasch fit per round.

    ``seed_hits`` is the seed prior's per-sample hit history (origin in
    R1, prior round winner R2+); when supplied, the ``pick_score`` block
    carries Chernoff info against the seed instead of the cold-start
    ``p·(1-p)`` fallback.

    Pure function: no I/O.
    """
    return build_hard_samples_artifact_from_observations(
        build_observations(rounds),
        cycle_id=cycle_id,
        top_k_candidates=top_k_candidates,
        top_k_samples=top_k_samples,
        posterior=posterior,
        seed_hits=seed_hits,
    )


def build_hard_samples_artifact_from_observations(
    observations: list[Observation],
    *,
    cycle_id: str | None = None,
    top_k_candidates: int | None = 40,
    top_k_samples: int | None = 40,
    posterior: RaschPosterior | None = None,
    seed_hits: dict[int, list[bool]] | None = None,
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
    pick_score_map = _chernoff_information(observations, seed_hits)

    full_candidate_order = _resolve_candidate_order(posterior, hit_rates)
    full_sample_order = _resolve_sample_order(posterior, miss_rates)
    full_pick_order = _resolve_pick_order(pick_score_map, posterior.delta)

    total_candidates = len(full_candidate_order)
    total_samples = len(full_sample_order)

    candidate_order = (
        full_candidate_order
        if top_k_candidates is None
        else full_candidate_order[:top_k_candidates]
    )
    sample_order = full_sample_order if top_k_samples is None else full_sample_order[:top_k_samples]
    pick_order = full_pick_order if top_k_samples is None else full_pick_order[:top_k_samples]

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
        "pick_score": {
            "per_sample": {str(sid): float(pick_score_map.get(sid, 0.0)) for sid in sample_order},
            "sample_order": [int(sid) for sid in pick_order],
        },
        "cells": cells,
    }
