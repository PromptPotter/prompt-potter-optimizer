"""Bayesian Posterior-of-Being-Best (PoBB): population-aware mid-round abortion."""

from __future__ import annotations

import numpy as np
import pytest

scipy = pytest.importorskip("scipy")  # transitively required by other math helpers

from promptpotter.application.optimization.elimination import (  # noqa: E402
    PoBBCheck,
    PoBBConfig,
    PoBBSnapshot,
)
from promptpotter.domain.analysis import EscalationTarget  # noqa: E402
from promptpotter.shared.statistics import (  # noqa: E402
    pobb_should_stop,
    posterior_best_probabilities,
)


def test_posterior_best_probabilities_sums_to_one():
    rng = np.random.default_rng(42)
    histories = {
        "a": [0.6, 0.7, 0.8, 0.6, 0.7],
        "b": [0.5, 0.5, 0.5, 0.5, 0.5],
        "c": [0.4, 0.3, 0.5, 0.3, 0.4],
    }
    probs = posterior_best_probabilities(histories, rng=rng)
    assert set(probs) == {"a", "b", "c"}
    assert abs(sum(probs.values()) - 1.0) < 1e-9


def test_high_signal_collapses_to_clear_winner():
    """Clear-leader regime: P(loser is best) → 0; P(winner is best) → 1."""
    rng = np.random.default_rng(0)
    histories = {
        "winner": [1.0] * 8,
        "loser": [0.0] * 8,
    }
    probs = posterior_best_probabilities(histories, n_samples=2000, rng=rng)
    assert probs["winner"] >= 0.99
    assert probs["loser"] <= 0.01


def test_low_signal_diffuses_around_uniform():
    """Indistinguishable candidates regime: P(c is best) hovers near 1/K."""
    rng = np.random.default_rng(0)
    histories = {f"c{i}": [0.5] * 5 for i in range(3)}
    probs = posterior_best_probabilities(histories, n_samples=2000, rng=rng)
    for p in probs.values():
        assert 0.20 <= p <= 0.50  # uniform ± MC noise


def test_pobb_should_stop_threshold():
    assert pobb_should_stop(0.04, 0.05) is True
    assert pobb_should_stop(0.05, 0.05) is False  # strict < threshold
    assert pobb_should_stop(0.50, 0.05) is False


def test_pobb_check_n_min_floor():
    """Below n_min queries, no signal is emitted regardless of separation."""
    check = PoBBCheck(PoBBConfig(n_min=4, epsilon=0.05), n_queries=10)
    check.register_completed([1.0] * 10, candidate_id="winner")
    check.set_current("loser")
    # 3 queries < n_min=4 — too early to fire even though signal is huge.
    sig = check.check([{"score": 0.0}] * 3, candidate_idx=1, n_total_candidates=2)
    assert sig is None


def test_pobb_check_no_priors_returns_none():
    """Without any completed prior, P(best) is undefined for the current candidate."""
    check = PoBBCheck(PoBBConfig(n_min=2, epsilon=0.05), n_queries=10)
    check.set_current("alone")
    sig = check.check([{"score": 0.5}] * 5, candidate_idx=0, n_total_candidates=1)
    assert sig is None


def test_pobb_check_high_signal_stops_inferior():
    """Loser candidate vs strong prior fires within ≤5 queries at ε=0.05."""
    check = PoBBCheck(PoBBConfig(n_min=4, epsilon=0.05), n_queries=20)
    check.register_completed([1.0] * 20, candidate_id="winner")
    check.set_current("loser")
    sig = check.check([{"score": 0.0}] * 5, candidate_idx=1, n_total_candidates=2)
    assert sig is not None
    assert sig.check_name == "elimination"
    cr = sig.check_result
    assert cr["leader_id"] == "winner"
    assert cr["p_best"] < 0.05
    assert "p_best_snapshot" in cr


def test_pobb_check_low_signal_does_not_stop():
    """Indistinguishable candidates: no abort even past budget cap."""
    check = PoBBCheck(PoBBConfig(n_min=4, epsilon=0.05), n_queries=20)
    check.register_completed([0.5] * 20, candidate_id="prior")
    check.set_current("similar")
    sig = check.check([{"score": 0.5}] * 20, candidate_idx=1, n_total_candidates=2)
    assert sig is None


def test_pobb_check_fires_snapshot_callback_per_query():
    """The on_snapshot hook fires every time the check computes a posterior."""
    check = PoBBCheck(PoBBConfig(n_min=4, epsilon=0.05), n_queries=10)
    check.register_completed([1.0] * 10, candidate_id="winner")
    received: list[PoBBSnapshot] = []
    check.set_current("loser", on_snapshot=received.append)
    # 4 queries — first call past n_min — should produce one snapshot.
    check.check([{"score": 0.0}] * 4, candidate_idx=1, n_total_candidates=2)
    assert len(received) == 1
    assert received[0].current_id == "loser"
    assert received[0].n_queries == 4
    assert "winner" in received[0].p_best
    assert "loser" in received[0].p_best


def test_pobb_locks_in_dominant_leader():
    """Current candidate dominating prior past lock_in_n_min fires LEADER_LOCKED."""
    check = PoBBCheck(PoBBConfig(n_min=4, epsilon=0.05, lock_in=0.95, lock_in_n_min=8), n_queries=20)
    # Prior: weak candidate. Current: clear leader.
    check.register_completed([0.0] * 20, candidate_id="weak_prior")
    check.set_current("strong_current")
    sig = check.check([{"score": 1.0}] * 8, candidate_idx=1, n_total_candidates=3)
    assert sig is not None
    assert sig.target == EscalationTarget.LEADER_LOCKED
    cr = sig.check_result
    assert cr["leader_id"] == "strong_current"
    assert cr["p_best"] >= 0.95
    assert cr["queries_scored"] == 8
    # Two candidates remain unscored (idx=1 of 3).
    assert sig.candidates_skipped == 1


def test_pobb_no_lock_in_below_n_min():
    """Even with overwhelming signal, lock-in waits for lock_in_n_min queries."""
    check = PoBBCheck(PoBBConfig(n_min=4, epsilon=0.05, lock_in=0.95, lock_in_n_min=8), n_queries=20)
    check.register_completed([0.0] * 20, candidate_id="weak_prior")
    check.set_current("strong_current")
    # Only 5 queries — past elim n_min (4) but below lock_in_n_min (8).
    sig = check.check([{"score": 1.0}] * 5, candidate_idx=1, n_total_candidates=2)
    assert sig is None  # leader p_best is high but n < lock_in_n_min


def test_pobb_lock_in_disabled_at_threshold_one():
    """``lock_in=1.0`` disables the lock-in branch entirely."""
    check = PoBBCheck(PoBBConfig(n_min=4, epsilon=0.05, lock_in=1.0, lock_in_n_min=4), n_queries=20)
    check.register_completed([0.0] * 20, candidate_id="weak_prior")
    check.set_current("strong_current")
    # Past lock_in_n_min and dominating — would lock in if enabled.
    sig = check.check([{"score": 1.0}] * 10, candidate_idx=1, n_total_candidates=2)
    assert sig is None  # lock_in=1.0 short-circuits the branch
