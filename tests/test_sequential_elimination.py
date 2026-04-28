"""Wilcoxon signed-rank + Holm-Bonferroni: sequential candidate elimination math."""

from __future__ import annotations

import pytest

scipy = pytest.importorskip("scipy")

from promptpotter.application.optimization.elimination import EliminationCheck  # noqa: E402
from promptpotter.shared.statistics import (  # noqa: E402
    _holm_bonferroni,
    _paired_signed_rank_pvalue,
    should_stop_early,
)


def test_paired_signed_rank_pvalue_directional():
    prior = [0.8] * 30
    assert _paired_signed_rank_pvalue([0.3] * 30, prior) < 0.001  # clearly worse
    assert _paired_signed_rank_pvalue([0.8] * 30, prior) == 1.0  # identical


def test_paired_signed_rank_pvalue_too_few_returns_one():
    assert _paired_signed_rank_pvalue([0.5], [0.8]) == 1.0


def test_holm_bonferroni_step_down():
    # sorted [0.01, 0.03, 0.10] vs thresholds 0.05/3, 0.05/2, 0.05/1.
    # 0.01 < 0.0167 reject; 0.03 > 0.025 stop → both subsequent stay False.
    assert _holm_bonferroni([0.03, 0.01, 0.10], 0.05) == [False, True, False]


def test_should_stop_early_triggers_on_inferior_candidate():
    stop, ctx = should_stop_early([0.3] * 50, [[0.8] * 50])
    assert stop is True
    assert ctx["triggered_by_prior"] == 0


def test_should_stop_early_no_priors_returns_false():
    stop, _ = should_stop_early([0.5] * 30, [])
    assert stop is False


def test_elimination_check_gates_on_n_min_and_inferiority():
    check = EliminationCheck(n_min=20, alpha=0.05, n_queries=100)
    check.register_completed([0.9] * 100)
    # Below n_min queries → no signal yet.
    assert check.check([{"score": 0.1}] * 10, candidate_idx=1, n_total_candidates=3) is None
    # Past n_min, an inferior candidate against a strong prior gets eliminated.
    signal = check.check([{"score": 0.1}] * 30, candidate_idx=1, n_total_candidates=3)
    assert signal is not None and signal.check_name == "elimination"
