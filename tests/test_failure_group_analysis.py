"""Tests for failure group sensitivity analysis and SearchMemory integration."""

from promptpotter.application.intelligence.search_memory import SearchMemory
from promptpotter.application.recon.failure_groups import (
    FailureGroupResult,
    failure_group_sensitivity,
)


class _FakeCluster:
    def __init__(self, failure_mode: str, example_queries: list[str]):
        self.failure_mode = failure_mode
        self.example_queries = example_queries


def _make_scan_rows():
    """Synthetic scan rows with per_query_hits."""
    return [
        # Axis: max_sites, baseline value (delta=0)
        {
            "axis": "max_sites",
            "delta": 0.0,
            "value_preview": "5",
            "per_query_hits": {"q1": True, "q2": False, "q3": False, "q4": True},
        },
        # Axis: max_sites, variant (delta != 0)
        {
            "axis": "max_sites",
            "delta": 0.1,
            "value_preview": "10",
            "per_query_hits": {"q1": True, "q2": True, "q3": False, "q4": True},
        },
        # Axis: temperature, baseline
        {
            "axis": "temperature",
            "delta": 0.0,
            "value_preview": "0.3",
            "per_query_hits": {"q1": True, "q2": False, "q3": True, "q4": False},
        },
        # Axis: temperature, variant
        {
            "axis": "temperature",
            "delta": -0.05,
            "value_preview": "0.7",
            "per_query_hits": {"q1": True, "q2": False, "q3": False, "q4": False},
        },
    ]


def _make_clusters():
    """Two failure groups: web_search (q2, q3) and token_matching (q4)."""
    return [
        _FakeCluster("web_search", ["q2", "q3"]),
        _FakeCluster("token_matching", ["q4"]),
    ]


def test_failure_group_sensitivity_basic():
    rows = _make_scan_rows()
    clusters = _make_clusters()
    result = failure_group_sensitivity(rows, clusters)

    assert isinstance(result, FailureGroupResult)
    assert len(result.groups) == 2
    assert "web_search" in result.groups
    assert "token_matching" in result.groups

    # max_sites helps web_search group: baseline q2=F,q3=F (0%), variant q2=T,q3=F (50%)
    ws_max_sites = [
        s for s in result.sensitivities if s.axis == "max_sites" and s.failure_group == "web_search"
    ]
    assert len(ws_max_sites) == 1
    assert ws_max_sites[0].delta == 0.5  # 50% - 0% = 50%
    assert ws_max_sites[0].best_value == "10"


def test_failure_group_sensitivity_empty_inputs():
    assert failure_group_sensitivity([], []).sensitivities == []
    assert failure_group_sensitivity(_make_scan_rows(), []).sensitivities == []


def test_failure_group_sensitivity_skips_negligible():
    """Deltas <= 0.01 should be filtered out."""
    rows = [
        {"axis": "x", "delta": 0.0, "value_preview": "a", "per_query_hits": {"q1": True}},
        {"axis": "x", "delta": 0.01, "value_preview": "b", "per_query_hits": {"q1": True}},
    ]
    clusters = [_FakeCluster("mode", ["q1"])]
    result = failure_group_sensitivity(rows, clusters)
    assert result.sensitivities == []  # delta = 0, filtered


def test_search_memory_ingest_and_accessors():
    """Test the full pipeline: produce → ingest → query."""
    rows = _make_scan_rows()
    clusters = _make_clusters()

    result = failure_group_sensitivity(rows, clusters)

    sm = SearchMemory()
    sm.ingest_failure_groups(result)

    # parameter_failure_correlation should return deltas for max_sites
    corr = sm.parameter_failure_correlation("max_sites")
    assert "web_search" in corr
    assert corr["web_search"] == 0.5

    # query_sensitive_axes for q2 (in web_search group) should include max_sites
    axes = sm.query_sensitive_axes("q2")
    assert "max_sites" in axes

    # Unknown query returns empty
    assert sm.query_sensitive_axes("unknown") == []

    # Unknown axis returns empty
    assert sm.parameter_failure_correlation("unknown") == {}
