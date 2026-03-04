"""Tests for smart prompt search: diagnostic set, axis classification, variant library."""
import logging

import pandas as pd
import pytest

from api.config.settings import load_variant_library
from api.services.search import build_diagnostic_set, select_grid_winner
from api.services.search.smart_search import (
    classify_axis,
    sensitivity_scan,
    _profiles_from_rows,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_eval_data(n: int) -> list:
    """Create n synthetic eval data entries."""
    return [
        {"query": f"query_{i}", "ground_truth": f"gt_{i}", "pipeline_data": {}}
        for i in range(n)
    ]


def _make_baseline_results(eval_data: list, hit_indices: set) -> list:
    """Create baseline results where items at hit_indices are hits."""
    results = []
    for i, d in enumerate(eval_data):
        results.append({
            "query": d["query"],
            "ground_truth": d["ground_truth"],
            "predicted": d["ground_truth"] if i in hit_indices else "wrong",
            "hit": i in hit_indices,
            "error": False,
        })
    return results


# ---------------------------------------------------------------------------
# build_diagnostic_set tests
# ---------------------------------------------------------------------------

def test_build_diagnostic_set_basic():
    """Correct hit/miss ratio from 10 queries."""
    eval_data = _make_eval_data(10)
    # 7 hits, 3 misses
    results = _make_baseline_results(eval_data, {0, 1, 2, 3, 4, 5, 6})

    diagnostic, summary = build_diagnostic_set(eval_data, results, n_queries=6)

    assert len(diagnostic) == 6
    assert summary["n_queries"] == 6
    assert summary["n_hits"] + summary["n_misses"] == 6
    # Should have both hits and misses in the set
    assert summary["n_hits"] > 0
    assert summary["n_misses"] > 0


def test_build_diagnostic_set_all_hits():
    """All-hit pool still returns n_queries."""
    eval_data = _make_eval_data(10)
    results = _make_baseline_results(eval_data, set(range(10)))

    diagnostic, summary = build_diagnostic_set(eval_data, results, n_queries=6)

    assert len(diagnostic) == 6
    assert summary["n_hits"] == 6
    assert summary["n_misses"] == 0


def test_build_diagnostic_set_too_few():
    """< MIN_DIAGNOSTIC_QUERIES queries raises ValueError."""
    eval_data = _make_eval_data(2)
    results = _make_baseline_results(eval_data, {0})

    with pytest.raises(ValueError, match="at least"):
        build_diagnostic_set(eval_data, results, n_queries=6)


def test_build_diagnostic_set_empty_baseline():
    """Empty baseline_results returns random sample with stratified=False."""
    eval_data = _make_eval_data(20)

    diagnostic, summary = build_diagnostic_set(eval_data, [], n_queries=6, seed=42)

    assert len(diagnostic) == 6
    assert summary["n_queries"] == 6
    assert summary["n_hits"] == 0
    assert summary["n_misses"] == 0
    assert summary["stratified"] is False
    # All sampled items should come from eval_data
    eval_queries = {d["query"] for d in eval_data}
    assert all(d["query"] in eval_queries for d in diagnostic)


def test_build_diagnostic_set_deterministic():
    """Same seed produces same output."""
    eval_data = _make_eval_data(20)
    results = _make_baseline_results(eval_data, {0, 2, 4, 6, 8, 10, 12, 14})

    d1, _ = build_diagnostic_set(eval_data, results, n_queries=6, seed=42)
    d2, _ = build_diagnostic_set(eval_data, results, n_queries=6, seed=42)

    assert [d["query"] for d in d1] == [d["query"] for d in d2]


# ---------------------------------------------------------------------------
# classify_axis tests
# ---------------------------------------------------------------------------

def test_classify_axis_binary():
    """Cardinality 2 with detectable effect -> budget 'low'."""
    assert classify_axis(cardinality=2, sensitivity_range=0.2) == "low"


def test_classify_axis_high_sensitivity():
    """Range > 0.3 -> budget 'high'."""
    assert classify_axis(cardinality=4, sensitivity_range=0.35) == "high"


def test_classify_axis_skip():
    """Range < 1/n_diagnostic -> budget 'skip'."""
    # n_diagnostic=6, threshold = 1/6 ≈ 0.167
    assert classify_axis(
        cardinality=3, sensitivity_range=0.10, n_diagnostic=6,
    ) == "skip"


# ---------------------------------------------------------------------------
# select_grid_winner sample guard
# ---------------------------------------------------------------------------

def test_select_grid_winner_warns_small_sample(caplog):
    """total < MIN_DIAGNOSTIC_QUERIES logs a warning."""
    from api.models.prompt_state import PromptState

    ps = PromptState(instruction="test")
    grid_df = pd.DataFrame([{
        "prompt_state_id": ps.id,
        "accuracy": 0.5,
        "hits": 1,
        "total": 2,
        "errors": 0,
    }])

    with caplog.at_level(logging.WARNING, logger="api.services.search.grid_core"):
        result = select_grid_winner(grid_df, {ps.id: ps})

    assert result["accuracy"] == 0.5
    assert any("only 2 queries" in r.message for r in caplog.records)


# ---------------------------------------------------------------------------
# load_variant_library
# ---------------------------------------------------------------------------

def test_load_variant_library():
    """Config file loads and has expected top-level keys."""
    lib = load_variant_library()
    assert "prompt_fields" in lib
    assert "pipeline_params" in lib
    assert "persona" in lib["prompt_fields"]
    assert "ranking_temperature" in lib["pipeline_params"]
    assert isinstance(lib["prompt_fields"]["persona"], list)
    assert len(lib["prompt_fields"]["persona"]) > 1


# ---------------------------------------------------------------------------
# _profiles_from_rows
# ---------------------------------------------------------------------------

def test_profiles_from_rows():
    """Build profiles from scan rows."""
    axes = [
        ("persona", "prompt_field", ["a", "b", "c"]),
        ("temp", "pipeline_param", ["0.1", "0.9"]),
    ]
    rows = [
        {"axis": "persona", "delta": 0.0},
        {"axis": "persona", "delta": 0.1},
        {"axis": "persona", "delta": -0.05},
        {"axis": "temp", "delta": 0.0},
        {"axis": "temp", "delta": 0.2},
    ]
    profiles = _profiles_from_rows(rows, axes, n_eval=6)
    assert len(profiles) == 2
    # Sorted by sensitivity descending
    assert profiles[0]["axis"] == "temp"  # range 0.2
    assert profiles[1]["axis"] == "persona"  # range 0.15
    assert profiles[0]["sensitivity_range"] == 0.2
    assert profiles[1]["best_delta"] == 0.1
    assert profiles[1]["worst_delta"] == -0.05


# ---------------------------------------------------------------------------
# sensitivity_scan partial resume
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_sensitivity_scan_partial_resume():
    """Partial scan resumes: completed axis is skipped, rows are preserved."""
    from api.models.prompt_state import PromptState

    baseline = PromptState(
        instruction="test",
        persona="default_persona",
        task_intent="default_intent",
    )

    variant_library = {
        "prompt_fields": {
            "persona": ["default_persona", "expert"],
            "task_intent": ["default_intent", "classify"],
        },
        "pipeline_params": {},
    }
    eval_data = _make_eval_data(4)

    # Track which axes got evaluated
    evaluated_axes: set[str] = set()

    async def _mock_eval(ps, data, client, **kw):
        return (
            [{"hit": True, "query": d["query"]} for d in data],
            {"accuracy": 0.75, "hits": 3, "total": 4},
            False,
        )

    import api.services.search.smart_search as _ss
    _orig = _ss.evaluate_prompt_cached

    async def _patched_eval(ps, data, client, **kw):
        # Detect which field changed from baseline
        if ps.persona != baseline.persona:
            evaluated_axes.add("persona")
        if ps.task_intent != baseline.task_intent:
            evaluated_axes.add("task_intent")
        return await _mock_eval(ps, data, client, **kw)

    _ss.evaluate_prompt_cached = _patched_eval

    try:
        # Simulate partial_scan where "persona" is already done
        partial_scan = {
            "rows": [
                {"axis": "persona", "axis_type": "prompt_field",
                 "value_idx": 0, "value_preview": "default_persona",
                 "hits": 3, "total": 4, "accuracy": 0.75, "delta": 0.0},
                {"axis": "persona", "axis_type": "prompt_field",
                 "value_idx": 1, "value_preview": "expert",
                 "hits": 2, "total": 4, "accuracy": 0.5, "delta": -0.25},
            ],
            "completed_axes": ["persona"],
        }

        df, profiles = await sensitivity_scan(
            baseline, variant_library, eval_data,
            backend_client=None,  # not used since eval is mocked
            partial_scan=partial_scan,
        )

        # persona should NOT have been re-evaluated
        assert "persona" not in evaluated_axes
        # task_intent SHOULD have been evaluated
        assert "task_intent" in evaluated_axes

        # Output should contain rows from both axes
        assert len(df) >= 3  # 2 from persona partial + at least 1 from task_intent
        assert set(df["axis"].unique()) == {"persona", "task_intent"}

        # Profiles should cover both axes
        profile_names = {p["axis"] for p in profiles}
        assert profile_names == {"persona", "task_intent"}

    finally:
        _ss.evaluate_prompt_cached = _orig
