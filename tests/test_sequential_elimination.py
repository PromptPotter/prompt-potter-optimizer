"""Tests for sequential candidate elimination."""

from __future__ import annotations

import pytest

scipy = pytest.importorskip("scipy")

from promptpotter.application.recon.failure_groups import (  # noqa: E402
    EliminationCheck,
    _holm_bonferroni,
    _paired_ttest_pvalue,
    should_stop_early,
)

# ---------------------------------------------------------------------------
# _paired_ttest_pvalue
# ---------------------------------------------------------------------------


class TestPairedTtestPvalue:
    def test_identical_scores_returns_one(self):
        scores = [0.5] * 30
        assert _paired_ttest_pvalue(scores, scores) == 1.0

    def test_current_clearly_worse(self):
        prior = [0.8] * 30
        current = [0.3] * 30
        p = _paired_ttest_pvalue(current, prior)
        assert p < 0.001

    def test_current_clearly_better(self):
        prior = [0.3] * 30
        current = [0.8] * 30
        p = _paired_ttest_pvalue(current, prior)
        assert p > 0.99

    def test_similar_means_high_p(self):
        prior = [0.5 + 0.01 * (i % 3 - 1) for i in range(30)]
        current = [0.5 + 0.01 * ((i + 1) % 3 - 1) for i in range(30)]
        p = _paired_ttest_pvalue(current, prior)
        assert p > 0.05

    def test_too_few_samples(self):
        assert _paired_ttest_pvalue([0.5], [0.8]) == 1.0


# ---------------------------------------------------------------------------
# _holm_bonferroni
# ---------------------------------------------------------------------------


class TestHolmBonferroni:
    def test_single_below_alpha(self):
        assert _holm_bonferroni([0.01], 0.05) == [True]

    def test_single_above_alpha(self):
        assert _holm_bonferroni([0.10], 0.05) == [False]

    def test_empty(self):
        assert _holm_bonferroni([], 0.05) == []

    def test_step_down_logic(self):
        # 3 p-values: sorted [0.01, 0.03, 0.10]
        # thresholds: 0.05/3=0.0167, 0.05/2=0.025, 0.05/1=0.05
        # 0.01 < 0.0167 → reject
        # 0.03 > 0.025  → stop
        result = _holm_bonferroni([0.03, 0.01, 0.10], 0.05)
        assert result == [False, True, False]

    def test_all_rejected(self):
        result = _holm_bonferroni([0.001, 0.002, 0.003], 0.05)
        assert result == [True, True, True]


# ---------------------------------------------------------------------------
# should_stop_early
# ---------------------------------------------------------------------------


class TestShouldStopEarly:
    def test_no_priors_returns_false(self):
        stop, ctx = should_stop_early([0.5] * 30, [])
        assert stop is False
        assert ctx == {}

    def test_worse_candidate_stopped(self):
        prior = [0.8] * 50
        current = [0.3] * 50
        stop, ctx = should_stop_early(current, [prior])
        assert stop is True
        assert "triggered_by_prior" in ctx

    def test_similar_candidate_survives(self):
        prior = [0.5 + 0.01 * (i % 3 - 1) for i in range(50)]
        current = [0.5 + 0.01 * ((i + 1) % 3 - 1) for i in range(50)]
        stop, _ = should_stop_early(current, [prior])
        assert stop is False

    def test_multiple_priors_any_reject_stops(self):
        good_prior = [0.9] * 50
        weak_prior = [0.4] * 50
        current = [0.4] * 50
        stop, ctx = should_stop_early(current, [good_prior, weak_prior])
        assert stop is True
        assert ctx["triggered_by_prior"] == 0


# ---------------------------------------------------------------------------
# EliminationCheck
# ---------------------------------------------------------------------------


class TestEliminationCheck:
    def test_disabled_when_k_too_small(self):
        check = EliminationCheck(n_min=20, alpha=0.05, n_queries=30)
        assert check.enabled is False

    def test_enabled_when_k_sufficient(self):
        check = EliminationCheck(n_min=20, alpha=0.05, n_queries=40)
        assert check.enabled is True

    def test_returns_none_before_n_min(self):
        check = EliminationCheck(n_min=20, alpha=0.05, n_queries=100)
        check.register_completed([0.8] * 100)
        results = [{"score": 0.1}] * 10
        assert check.evaluate(results, candidate_idx=1, n_total_candidates=3) is None

    def test_first_candidate_never_stopped(self):
        check = EliminationCheck(n_min=20, alpha=0.05, n_queries=100)
        results = [{"score": 0.1}] * 50
        assert check.evaluate(results, candidate_idx=0, n_total_candidates=3) is None

    def test_returns_signal_when_inferior(self):
        check = EliminationCheck(n_min=20, alpha=0.05, n_queries=100)
        check.register_completed([0.9] * 100)
        results = [{"score": 0.1}] * 30
        signal = check.evaluate(results, candidate_idx=1, n_total_candidates=3)
        assert signal is not None
        assert signal.check_name == "elimination"
        assert signal.candidate_idx == 1

    def test_protocol_conformance(self):
        check = EliminationCheck(n_min=20, alpha=0.05, n_queries=100)
        assert hasattr(check, "enabled")
        assert hasattr(check, "name")
        assert check.name == "elimination"
        assert callable(check.evaluate)
