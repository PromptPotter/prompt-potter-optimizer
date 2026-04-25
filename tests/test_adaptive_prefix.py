"""Rasch fit numerical correctness + AdaptivePrefix mutation safety.

Two contracts:
  1. Rasch joint MLE recovers known θ/δ on synthetic Bernoulli data.
  2. evolve_prefix() preserves min_prefix_size, is a no-op when disabled,
     and mutates exclusively the scoring slice (round-boundary mutation
     invariant).
"""

from __future__ import annotations

import numpy as np
import pytest

from promptpotter.application.campaign.config import AdaptivePrefixConfig
from promptpotter.application.intelligence.adaptive_prefix import (
    build_observations,
    evolve_prefix,
)
from promptpotter.application.intelligence.rasch import (
    Observation,
    fit_rasch,
    knowledge_gradient,
)
from promptpotter.application.optimization.results import RoundResult
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


def test_evolve_prefix_disabled_is_noop() -> None:
    samples = [Sample(id=i, query=f"q{i}", ground_truth="g") for i in range(5)]
    rounds = [
        _make_round(
            0,
            {"c1": [{"sample_id": i, "hit": i % 2 == 0} for i in range(5)]},
        )
    ]
    cfg = AdaptivePrefixConfig(enabled=False)
    out = evolve_prefix(
        full_dataset=[*samples, Sample(id=99, query="q99", ground_truth="g")],
        current_prefix=samples,
        rounds=rounds,
        config=cfg,
        elimination_n_min=4,
    )
    assert out.reason == "disabled"
    assert out.new_prefix == samples
    assert out.swapped_in == [] and out.swapped_out == []


def test_evolve_prefix_respects_min_prefix_size() -> None:
    # Tiny prefix at the floor → swap-out must yield zero, returning current.
    prefix = [Sample(id=i, query=f"q{i}", ground_truth="g") for i in range(4)]
    extra = [Sample(id=10 + i, query=f"q{10 + i}", ground_truth="g") for i in range(5)]
    full = prefix + extra

    # Many candidates measuring all prefix samples consistently → narrow δ SE
    # → swap-out would be eligible, but min_prefix_size==4 must block it.
    candidate_results = {
        f"c{ci}": [{"sample_id": s.id, "hit": True} for s in prefix] for ci in range(8)
    }
    rounds = [_make_round(0, candidate_results)]

    cfg = AdaptivePrefixConfig(
        enabled=True,
        swap_out_delta_se=10.0,  # extremely permissive — every prefix sample qualifies
        swap_in_kg_threshold=0.0,
        max_swaps_per_round=10,
    )
    out = evolve_prefix(
        full_dataset=full,
        current_prefix=prefix,
        rounds=rounds,
        config=cfg,
        elimination_n_min=4,  # floor matches len(prefix)
    )
    assert len(out.new_prefix) >= 4
    # No prefix sample dropped below the floor.
    assert {s.id for s in out.new_prefix} >= {s.id for s in prefix} - {-1}


def test_build_observations_skips_errors_and_missing_sample_id() -> None:
    rounds = [
        _make_round(
            0,
            {
                "c1": [
                    {"sample_id": 1, "hit": True},
                    {"sample_id": None, "hit": True},  # missing id — skip
                    {"sample_id": 2, "hit": False, "error": "boom"},  # error — skip
                    {"sample_id": 3, "hit": False},
                ]
            },
        )
    ]
    obs = build_observations(rounds)
    assert {(o.sample_id, o.hit) for o in obs} == {(1, True), (3, False)}


def test_build_prefix_event_and_collector_roundtrip() -> None:
    from promptpotter.application.intelligence.adaptive_prefix import (
        EvolveResult,
        build_prefix_event,
    )
    from promptpotter.presentation.views import collect_prefix_events, render_adaptive_prefix

    samples = [Sample(id=i, query=f"q{i}", ground_truth="g") for i in range(3)]
    result = EvolveResult(
        new_prefix=samples,
        swapped_out=[samples[0]],
        swapped_in=[samples[2]],
        rasch=None,
        reason="swapped",
    )
    event = build_prefix_event(round_num=2, result=result)
    assert event["round"] == 2
    assert event["swapped_out"] == [0]
    assert event["swapped_in"] == [2]

    # Trial-shaped dicts: each carries the running event log up to that round.
    trials = [
        {"round": 1, "prefix_events": []},
        {"round": 2, "prefix_events": [event]},
    ]
    collected = collect_prefix_events(trials)
    assert len(collected) == 1 and collected[0]["round"] == 2

    rendered = render_adaptive_prefix(collected)
    assert "ADAPTIVE PREFIX" in rendered
    # Empty input → empty string (caller can append unconditionally).
    assert render_adaptive_prefix([]) == ""


def test_cycle_checkpoint_roundtrips_prefix_events() -> None:
    """Cycle.checkpoint persists prefix_events; restore_from_trial reads them back."""
    from promptpotter.application.optimization.cycle import Cycle
    from promptpotter.application.optimization.results import RoundResult
    from promptpotter.domain.opt_search_point import OptSearchPoint

    cycle = Cycle()
    cycle.opt_sp = OptSearchPoint()
    cycle.prefix_events = [{"round": 1, "swapped_in": [42], "swapped_out": [3], "reason": "x"}]
    rr = RoundResult(
        round=1,
        label="R1",
        accuracy=0.5,
        hits=0,
        total=0,
        improved=False,
        prompt_fields={},
        candidates_scored=0,
    )
    trial = cycle.checkpoint(rr, round_num=1)
    assert trial["prefix_events"] == cycle.prefix_events

    fresh = Cycle()
    fresh.restore_from_trial(trial)
    assert fresh.prefix_events == cycle.prefix_events


@pytest.mark.parametrize("enabled", [True, False])
def test_evolve_prefix_does_not_mutate_inputs(enabled: bool) -> None:
    samples = [Sample(id=i, query=f"q{i}", ground_truth="g") for i in range(6)]
    snapshot = list(samples)
    rounds = [
        _make_round(
            0,
            {f"c{ci}": [{"sample_id": s.id, "hit": True} for s in samples] for ci in range(3)},
        )
    ]
    cfg = AdaptivePrefixConfig(enabled=enabled)
    evolve_prefix(
        full_dataset=samples,
        current_prefix=samples,
        rounds=rounds,
        config=cfg,
        elimination_n_min=4,
    )
    # Inputs untouched — caller is responsible for mutating session.scoring_dataset.
    assert samples == snapshot
