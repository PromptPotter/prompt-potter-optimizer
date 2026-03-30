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
    def test_fires_above_threshold(self):
        check = DegradationCheck(threshold=0.4)
        results = [
            _make_result(warnings=[_make_warning()]),
            _make_result(warnings=[_make_warning()]),
            _make_result(warnings=[_make_warning()]),
            _make_result(),
            _make_result(),
        ]
        signal = check.evaluate(results, candidate_idx=0, n_total_candidates=5)
        assert signal is not None
        assert signal.check_name == "degradation"
        assert signal.context["degraded_rate"] == pytest.approx(0.6)
        assert signal.context["degraded_count"] == 3

    def test_no_signal_below_threshold(self):
        check = DegradationCheck(threshold=0.4)
        results = [
            _make_result(warnings=[_make_warning()]),
            _make_result(),
            _make_result(),
            _make_result(),
            _make_result(),
        ]
        signal = check.evaluate(results, candidate_idx=0, n_total_candidates=5)
        assert signal is None

    def test_disabled_when_threshold_zero(self):
        from api.services.campaign.config import CycleConfig
        from api.services.campaign.escalation import build_escalation_checks

        config = CycleConfig(
            backend_url="http://mock:8000",
            degradation_threshold=0.0,
        )
        checks = build_escalation_checks(config)
        assert len(checks) == 0

    def test_uses_strategy_for_dominant_warning(self):
        check = DegradationCheck(
            threshold=0.3,
            strategies={
                "web_search:partial_scrape": EscalationStrategy(target="l2"),
                "entity_profiling:schema_error": EscalationStrategy(target="abort"),
            },
        )
        results = [
            _make_result(warnings=[_make_warning("web_search", "partial_scrape")]),
            _make_result(warnings=[_make_warning("web_search", "partial_scrape")]),
        ]
        signal = check.evaluate(results, candidate_idx=0, n_total_candidates=3)
        assert signal is not None
        assert signal.target == "l2"

    def test_empty_results(self):
        check = DegradationCheck(threshold=0.4)
        signal = check.evaluate([], candidate_idx=0, n_total_candidates=1)
        assert signal is None

    def test_signal_to_dict(self):
        check = DegradationCheck(threshold=0.3)
        results = [_make_result(warnings=[_make_warning()])]
        signal = check.evaluate(results, candidate_idx=2, n_total_candidates=5)
        assert signal is not None
        d = signal.to_dict()
        assert d["check_name"] == "degradation"
        assert d["candidate_idx"] == 2
        assert d["candidates_skipped"] == 2


class TestCollectWarningTypes:
    def test_counts_types(self):
        results = [
            _make_result(warnings=[
                _make_warning("web_search", "partial_scrape"),
                _make_warning("web_search", "timeout"),
            ]),
            _make_result(warnings=[_make_warning("web_search", "partial_scrape")]),
        ]
        types = collect_warning_types(results)
        assert types["web_search:partial_scrape"] == 2
        assert types["web_search:timeout"] == 1

    def test_handles_non_dict_warnings(self):
        results = [_make_result(warnings=["some string warning"])]
        types = collect_warning_types(results)
        assert types["some string warning"] == 1


