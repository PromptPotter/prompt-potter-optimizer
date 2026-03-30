"""Tests for EscalationCheck framework and DegradationCheck."""

import pytest

from api.services.campaign.escalation import (
    DegradationCheck,
    EscalationStrategy,
    collect_warning_types,
)


def _make_result(
    warnings: list | None = None,
    hit: bool = False,
) -> dict:
    return {
        "query": "test",
        "predicted": "pred",
        "ground_truth": "gt",
        "hit": hit,
        "score": 1.0 if hit else 0.0,
        "error": None,
        "pipeline_data": {
            "diagnostics": {
                "warnings": warnings or [],
            },
            "terminated_at": "token_matching",
        },
    }


def _make_warning(step: str = "web_search", code: str = "partial_scrape") -> dict:
    return {"step": step, "code": code, "message": "test warning"}


class TestDegradationCheck:
    @pytest.mark.parametrize("n_warned,threshold,expect_signal", [
        (3, 0.4, True),    # 3/5 = 0.6 > 0.4 → fires
        (1, 0.4, False),   # 1/5 = 0.2 < 0.4 → no signal
        (0, 0.4, False),   # empty results
    ])
    def test_threshold_gating(self, n_warned, threshold, expect_signal):
        check = DegradationCheck(threshold=threshold)
        results = [_make_result(warnings=[_make_warning()]) for _ in range(n_warned)]
        results += [_make_result() for _ in range(5 - n_warned)]
        signal = check.evaluate(results[:max(len(results), 0)],
                                candidate_idx=0, n_total_candidates=5)
        if expect_signal:
            assert signal is not None
            assert signal.check_name == "degradation"
        else:
            assert signal is None

    def test_disabled_when_threshold_zero(self):
        from api.services.campaign.config import CycleConfig
        from api.services.campaign.escalation import build_escalation_checks

        config = CycleConfig(backend_url="http://mock:8000", degradation_threshold=0.0)
        assert len(build_escalation_checks(config)) == 0

    def test_uses_strategy_for_dominant_warning(self):
        check = DegradationCheck(
            threshold=0.3,
            strategies={
                "web_search:partial_scrape": EscalationStrategy(target="l2"),
            },
        )
        results = [
            _make_result(warnings=[_make_warning("web_search", "partial_scrape")]),
            _make_result(warnings=[_make_warning("web_search", "partial_scrape")]),
        ]
        signal = check.evaluate(results, candidate_idx=0, n_total_candidates=3)
        assert signal is not None
        assert signal.target == "l2"

    def test_signal_to_dict(self):
        check = DegradationCheck(threshold=0.3)
        results = [_make_result(warnings=[_make_warning()])]
        signal = check.evaluate(results, candidate_idx=2, n_total_candidates=5)
        d = signal.to_dict()
        assert d["check_name"] == "degradation"
        assert d["candidate_idx"] == 2
        assert d["candidates_skipped"] == 2


class TestCollectWarningTypes:
    @pytest.mark.parametrize("warnings_input,expected", [
        (
            [[_make_warning("web_search", "partial_scrape"), _make_warning("web_search", "timeout")],
             [_make_warning("web_search", "partial_scrape")]],
            {"web_search:partial_scrape": 2, "web_search:timeout": 1},
        ),
        (
            [["some string warning"]],
            {"some string warning": 1},
        ),
    ])
    def test_collect(self, warnings_input, expected):
        results = [_make_result(warnings=w) for w in warnings_input]
        types = collect_warning_types(results)
        for k, v in expected.items():
            assert types[k] == v


