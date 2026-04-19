"""Tests for min_sample_size — inverse of min_detectable_effect."""

from promptpotter.shared.statistics import (
    min_detectable_effect,
    min_sample_size,
)


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
