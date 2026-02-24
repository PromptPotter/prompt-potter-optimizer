"""Tests for smart prompt search: diagnostic set, axis classification, variant library."""
import logging

import pandas as pd
import pytest

from api.config.settings import load_variant_library
from api.services.grid_search import (
    build_diagnostic_set,
    classify_axis,
    select_grid_winner,
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

    with caplog.at_level(logging.WARNING, logger="api.services.grid_search"):
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
