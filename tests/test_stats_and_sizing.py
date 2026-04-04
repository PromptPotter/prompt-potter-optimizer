"""Tests for Wave 2a: min_sample_size() and build_diagnostic_set() auto-adjustment."""

from promptpotter.services.search._stats import min_detectable_effect, min_sample_size
from promptpotter.services.search.smart_search import build_diagnostic_set


class TestMinSampleSize:
    def test_inverse_of_mde(self):
        """min_sample_size(mde) should produce n where min_detectable_effect(n) <= mde."""
        for mde in (0.10, 0.15, 0.20, 0.30):
            n = min_sample_size(mde)
            actual_mde = min_detectable_effect(n)
            assert actual_mde <= mde + 0.01, f"n={n}, mde={mde}, actual={actual_mde}"

    def test_known_value_15pct(self):
        # For MDE=0.15: n = ceil((1.96 + 0.84)^2 * 0.25 / 0.15^2)
        # = ceil(7.84 * 0.25 / 0.0225) = ceil(87.1) = 88
        n = min_sample_size(0.15)
        assert 85 <= n <= 90

    def test_zero_mde(self):
        assert min_sample_size(0.0) == 1

    def test_negative_mde(self):
        assert min_sample_size(-0.1) == 1

    def test_large_mde_small_n(self):
        n = min_sample_size(0.5)
        assert n < 20


class TestBuildDiagnosticSetAutoAdjust:
    def _make_data(self, n: int) -> tuple[list, list]:
        eval_data = [{"query": f"q{i}", "ground_truth": f"gt{i}"} for i in range(n)]
        baseline = [
            {"query": f"q{i}", "hit": i % 2 == 0}
            for i in range(n)
        ]
        return eval_data, baseline

    def test_small_n_adjusted_upward(self):
        """When n_queries=3 and pool is large enough, auto-adjust kicks in."""
        eval_data, baseline = self._make_data(100)
        _diagnostic, summary = build_diagnostic_set(eval_data, baseline, n_queries=3)
        # Should have been adjusted upward from 3
        assert summary["n_queries"] > 3

    def test_adjustment_clamped_to_pool(self):
        """When pool is smaller than min_n, clamp to pool size."""
        eval_data, baseline = self._make_data(10)
        _diagnostic, summary = build_diagnostic_set(eval_data, baseline, n_queries=3)
        assert summary["n_queries"] <= 10

    def test_large_n_not_adjusted(self):
        """When n_queries already exceeds min_n, no adjustment."""
        eval_data, baseline = self._make_data(200)
        _diagnostic, summary = build_diagnostic_set(eval_data, baseline, n_queries=100)
        assert summary["n_queries"] == 100
