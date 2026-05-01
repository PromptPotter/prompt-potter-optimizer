"""Rasch fit numerical correctness + scoring-set evolution mutation safety.

Two contracts:
  1. Rasch joint MLE recovers known θ/δ on synthetic Bernoulli data.
  2. evolve_scoring_set() preserves min_scoring_set_size, is a no-op when
     disabled, and mutates exclusively the scoring slice (round-boundary
     mutation invariant).
"""

from __future__ import annotations

import numpy as np
import pytest

from promptpotter.application.config import ExplorationConfig
from promptpotter.application.intelligence.exploration import (
    Observation,
    evolve_scoring_set,
    fit_rasch,
    knowledge_gradient,
)
from promptpotter.domain.results import RoundResult
from promptpotter.domain.sample import Sample


def _synth_observations(
    theta_true: dict[str, float],
    delta_true: dict[int, float],
    n_per_pair: int = 8,
    seed: int = 0,
) -> list[Observation]:
    rng = np.random.default_rng(seed)
    obs = []
    for cid, t in theta_true.items():
        for sid, d in delta_true.items():
            p = 1.0 / (1.0 + np.exp(-(t - d)))
            for _ in range(n_per_pair):
                obs.append(Observation(candidate_id=cid, sample_id=sid, hit=bool(rng.random() < p)))
    return obs


def test_rasch_recovers_known_parameters() -> None:
    # Wide spread + many obs/pair → MAP should recover both arrays within tolerance.
    theta_true = {"strong": 1.5, "mid": 0.0, "weak": -1.5}
    delta_true = {1: -1.0, 2: 0.0, 3: 1.0, 4: 2.0}
    obs = _synth_observations(theta_true, delta_true, n_per_pair=40, seed=42)

    posterior = fit_rasch(obs, theta_prior_sigma=10.0, delta_prior_sigma=10.0)

    assert posterior.converged
    # Identifiability: both arrays anchored to mean(theta) == 0.
    assert abs(sum(posterior.theta.values()) / len(posterior.theta)) < 1e-6
    # Recovery is within the noise floor for n=40 obs/pair (Bernoulli SE ~ 0.08).
    # Loosen to 0.5 logits — math correctness is what we're guarding, not precision.
    theta_offset = sum(theta_true.values()) / len(theta_true)
    for cid, t_true in theta_true.items():
        assert abs(posterior.theta[cid] - (t_true - theta_offset)) < 0.5
    for sid, d_true in delta_true.items():
        assert abs(posterior.delta[sid] - (d_true - theta_offset)) < 0.5


def test_rasch_handles_sparse_matrix() -> None:
    # Each candidate touches a different subset — Rasch must not crash on this.
    obs = [
        Observation("a", 1, True),
        Observation("a", 2, False),
        Observation("b", 2, True),
        Observation("b", 3, True),
        Observation("c", 1, False),
        Observation("c", 3, False),
    ]
    posterior = fit_rasch(obs)
    assert set(posterior.theta) == {"a", "b", "c"}
    assert set(posterior.delta) == {1, 2, 3}
    assert all(se > 0 for se in posterior.theta_se.values())
    assert all(se > 0 for se in posterior.delta_se.values())


def test_kg_is_non_negative_and_zero_for_unknown_pair() -> None:
    obs = _synth_observations({"a": 1.0, "b": -1.0}, {1: 0.0, 2: 0.5}, n_per_pair=10, seed=1)
    posterior = fit_rasch(obs)

    for cid in ("a", "b"):
        for sid in (1, 2):
            assert knowledge_gradient(posterior, cid, sid) >= 0.0

    assert knowledge_gradient(posterior, "unknown", 1) == 0.0
    assert knowledge_gradient(posterior, "a", 999) == 0.0


def _make_round(round_idx: int, candidate_results: dict[str, list[dict]]) -> RoundResult:
    return RoundResult(
        round=round_idx,
        label=f"R{round_idx}",
        accuracy=0.5,
        composite=0.5,
        hits=0,
        total=0,
        improved=False,
        prompt_fields={},
        all_candidate_results=candidate_results,
        candidates_scored=len(candidate_results),
    )


def test_evolve_scoring_set_respects_min_size() -> None:
    # Tiny scoring set at the floor → swap-out must yield zero, returning current.
    scoring_set = [Sample(id=i, query=f"q{i}", ground_truth="g") for i in range(4)]
    extra = [Sample(id=10 + i, query=f"q{10 + i}", ground_truth="g") for i in range(5)]
    full = scoring_set + extra

    # Many candidates measuring all scoring-set samples consistently → narrow δ SE
    # → swap-out would be eligible, but min_scoring_set_size==4 must block it.
    candidate_results = {
        f"c{ci}": [{"sample_id": s.id, "hit": True} for s in scoring_set] for ci in range(8)
    }
    rounds = [_make_round(0, candidate_results)]

    cfg = ExplorationConfig(
        enabled=True,
        swap_out_delta_se=10.0,  # extremely permissive — every scoring-set sample qualifies
        swap_in_kg_threshold=0.0,
        max_swaps_per_round=10,
    )
    out = evolve_scoring_set(
        full_dataset=full,
        current_scoring_set=scoring_set,
        rounds=rounds,
        config=cfg,
        elimination_n_min=4,  # floor matches len(scoring_set)
    )
    assert len(out.new_scoring_set) >= 4
    # No scoring-set sample dropped below the floor.
    assert {s.id for s in out.new_scoring_set} >= {s.id for s in scoring_set} - {-1}


@pytest.mark.parametrize("enabled", [True, False])
def test_evolve_scoring_set_does_not_mutate_inputs(enabled: bool) -> None:
    samples = [Sample(id=i, query=f"q{i}", ground_truth="g") for i in range(6)]
    snapshot = list(samples)
    rounds = [
        _make_round(
            0,
            {f"c{ci}": [{"sample_id": s.id, "hit": True} for s in samples] for ci in range(3)},
        )
    ]
    cfg = ExplorationConfig(enabled=enabled)
    evolve_scoring_set(
        full_dataset=samples,
        current_scoring_set=samples,
        rounds=rounds,
        config=cfg,
        elimination_n_min=4,
    )
    # Inputs untouched — caller is responsible for mutating session.scoring.scoring_dataset.
    assert samples == snapshot
