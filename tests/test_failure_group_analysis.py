"""Tests for failure group sensitivity analysis and SearchMemory integration."""

from promptpotter.application.search.failure_group_analysis import (
    FailureGroupResult,
    failure_group_sensitivity,
)
from promptpotter.application.search.search_memory import SearchMemory


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
    sm.ingest_failure_group_analysis(result)

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


def test_persistent_failures():
    """Test failure streak detection."""
    sm = SearchMemory()

    # Simulate query history: q1 always fails, q2 fails recently, q3 intermittent
    sm._query_hits["q1"] = [False, False, False, False, False]
    sm._query_hits["q2"] = [True, True, False, False, False]
    sm._query_hits["q3"] = [False, True, False, True, False]
    sm._query_hits["q4"] = [True, True, True]  # always hits
    sm._query_failure_modes["q1"] = ["web_search"] * 5
    sm._query_failure_modes["q2"] = ["token_matching"] * 3

    persistent = sm.persistent_failures(min_streak=3)

    # q1 (intractable) and q2 (chronic) should be returned
    queries = {r.query for r in persistent}
    assert "q1" in queries
    assert "q2" in queries
    assert "q3" not in queries  # intermittent — has a True in last 3
    assert "q4" not in queries  # always hits

    # q1 should be first (hit_rate=0, intractable)
    assert persistent[0].query == "q1"
    assert persistent[0].hit_rate == 0.0
    assert persistent[0].dominant_failure_mode == "web_search"

    # q2 chronic — hit_rate > 0
    q2 = next(r for r in persistent if r.query == "q2")
    assert q2.hit_rate > 0
    assert q2.dominant_failure_mode == "token_matching"
