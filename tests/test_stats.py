"""Tests for _stats module: Wilson CI, proportion test, MDE."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "notebooks"))
from _campaign_lib._stats import (
    fmt_ci, fmt_pvalue, min_detectable_effect, proportion_test, wilson_ci,
)


class TestWilsonCI:
    def test_zero_total(self):
        assert wilson_ci(0, 0) == (0.0, 0.0)

    def test_all_hits(self):
        lo, hi = wilson_ci(15, 15)
        assert hi <= 1.0
        assert lo > 0.7

    def test_no_hits(self):
        lo, hi = wilson_ci(0, 15)
        assert lo >= 0.0
        assert hi < 0.3

    def test_known_value(self):
        lo, hi = wilson_ci(2, 15)
        assert 0.02 < lo < 0.06
        assert 0.28 < hi < 0.42

    def test_bounds_valid(self):
        for hits in range(0, 21):
            lo, hi = wilson_ci(hits, 20)
            assert 0.0 <= lo <= hi <= 1.0


class TestProportionTest:
    def test_identical(self):
        p = proportion_test(5, 10, 5, 10)
        assert p >= 0.99

    def test_very_different(self):
        p = proportion_test(90, 100, 10, 100)
        assert p < 0.001

    def test_zero_total(self):
        assert proportion_test(0, 0, 5, 10) == 1.0

    def test_small_sample_not_significant(self):
        p = proportion_test(2, 15, 1, 15)
        assert p > 0.05


class TestMDE:
    def test_small_sample(self):
        mde = min_detectable_effect(15)
        assert 0.2 < mde < 0.6

    def test_large_sample(self):
        mde = min_detectable_effect(1000)
        assert mde < 0.1

    def test_zero(self):
        assert min_detectable_effect(0) == 1.0


class TestFormatting:
    def test_fmt_ci(self):
        s = fmt_ci(0.037, 0.307)
        assert "3.7%" in s
        assert "30.7%" in s

    def test_fmt_pvalue_three_stars(self):
        assert "***" in fmt_pvalue(0.0001)

    def test_fmt_pvalue_two_stars(self):
        assert "**" in fmt_pvalue(0.003)

    def test_fmt_pvalue_one_star(self):
        result = fmt_pvalue(0.04)
        assert "*" in result
        assert "**" not in result

    def test_fmt_pvalue_ns(self):
        assert "(ns)" in fmt_pvalue(0.42)
