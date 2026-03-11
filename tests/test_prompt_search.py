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
from api.services.pipeline_discovery import parse_pipeline_response


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_eval_data(n: int) -> list:
    return [
        {"query": f"query_{i}", "ground_truth": f"gt_{i}", "pipeline_data": {}}
        for i in range(n)
    ]


def _make_baseline_results(eval_data: list, hit_indices: set) -> list:
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
    results = _make_baseline_results(eval_data, {0, 1, 2, 3, 4, 5, 6})

    diagnostic, summary = build_diagnostic_set(eval_data, results, n_queries=6)

    assert len(diagnostic) == 6
    assert summary["n_queries"] == 6
    assert summary["n_hits"] > 0
    assert summary["n_misses"] > 0


def test_build_diagnostic_set_too_few():
    """< MIN_DIAGNOSTIC_QUERIES raises ValueError."""
    eval_data = _make_eval_data(2)
    results = _make_baseline_results(eval_data, {0})

    with pytest.raises(ValueError, match="at least"):
        build_diagnostic_set(eval_data, results, n_queries=6)


def test_build_diagnostic_set_empty_baseline():
    """Empty baseline_results returns random sample with stratified=False."""
    eval_data = _make_eval_data(20)

    diagnostic, summary = build_diagnostic_set(eval_data, [], n_queries=6, seed=42)

    assert len(diagnostic) == 6
    assert summary["stratified"] is False
    eval_queries = {d["query"] for d in eval_data}
    assert all(d["query"] in eval_queries for d in diagnostic)


# ---------------------------------------------------------------------------
# classify_axis tests
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("cardinality,sensitivity_range,kwargs,expected", [
    (4, 0.35, {}, "high"),
    (4, 0.20, {}, "medium"),
    (3, 0.10, {"n_diagnostic": 6}, "skip"),
])
def test_classify_axis(cardinality, sensitivity_range, kwargs, expected):
    assert classify_axis(
        cardinality=cardinality, sensitivity_range=sensitivity_range, **kwargs,
    ) == expected


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
    assert "persona" in lib["prompt_fields"]
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
    assert profiles[0]["axis"] == "temp"  # range 0.2
    assert profiles[1]["axis"] == "persona"  # range 0.15
    assert profiles[0]["sensitivity_range"] == 0.2
    assert profiles[1]["best_delta"] == 0.1


# ---------------------------------------------------------------------------
# scan rows include errors field
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_scan_rows_include_errors():
    """Scan rows include 'errors' field for both baseline and perturbed variants."""
    from api.models.prompt_state import PromptState
    from api.models.search_point import SearchPoint

    baseline = PromptState(
        instruction="test",
        persona="default_persona",
    )
    sp = SearchPoint(
        prompt_state=baseline,
        pipeline_params={"steps": ["llm_ranking"]},
    )

    scan_variants = {"persona": ["default_persona", "expert"]}
    eval_data = _make_eval_data(4)

    import api.services.search.smart_search as _ss
    _orig = _ss.evaluate_prompt_cached

    async def _mock_eval(search_point, data, client, **kw):
        ps = search_point.prompt_state
        if ps.persona != baseline.persona:
            return (
                [{"hit": False, "query": d["query"]} for d in data],
                {"accuracy": 0.0, "hits": 0, "total": 4, "errors": 2},
                False,
            )
        return (
            [{"hit": True, "query": d["query"]} for d in data],
            {"accuracy": 1.0, "hits": 4, "total": 4},
            False,
        )

    _ss.evaluate_prompt_cached = _mock_eval

    try:
        df, profiles = await sensitivity_scan(
            sp, scan_variants, eval_data, backend_client=None,
        )

        # Both baseline and perturbed rows should have 'errors' field
        assert "errors" in df.columns
        baseline_row = df[df["value_preview"] == "default_persona"].iloc[0]
        assert baseline_row["errors"] == 0
        perturbed_row = df[df["value_preview"] == "expert"].iloc[0]
        assert perturbed_row["errors"] == 2

    finally:
        _ss.evaluate_prompt_cached = _orig


# ---------------------------------------------------------------------------
# scan skips all-error axis
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_scan_skips_all_error_axis():
    """All-error axis rows still appear in results."""
    from api.models.prompt_state import PromptState
    from api.models.search_point import SearchPoint

    baseline = PromptState(
        instruction="test",
        persona="default_persona",
        task_intent="default_intent",
    )
    sp = SearchPoint(
        prompt_state=baseline,
        pipeline_params={"steps": ["llm_ranking"]},
    )

    scan_variants = {
        "persona": ["default_persona", "expert"],
        "task_intent": ["default_intent", "classify"],
    }
    eval_data = _make_eval_data(4)

    import api.services.search.smart_search as _ss
    _orig = _ss.evaluate_prompt_cached

    async def _mock_eval(search_point, data, client, **kw):
        ps = search_point.prompt_state
        # persona variants all error out
        if ps.persona != baseline.persona:
            return (
                [{"hit": False, "query": d["query"]} for d in data],
                {"accuracy": 0.0, "hits": 0, "total": 4, "errors": 4},
                False,
            )
        # task_intent works fine
        if ps.task_intent != baseline.task_intent:
            return (
                [{"hit": True, "query": d["query"]} for d in data],
                {"accuracy": 0.75, "hits": 3, "total": 4, "errors": 0},
                False,
            )
        return (
            [{"hit": True, "query": d["query"]} for d in data],
            {"accuracy": 1.0, "hits": 4, "total": 4},
            False,
        )

    _ss.evaluate_prompt_cached = _mock_eval

    try:
        df, profiles = await sensitivity_scan(
            sp, scan_variants, eval_data, backend_client=None,
        )

        # Both axes should still be in the results (rows are preserved)
        assert set(df["axis"].unique()) == {"persona", "task_intent"}

    finally:
        _ss.evaluate_prompt_cached = _orig


# ---------------------------------------------------------------------------
# scan circuit breaker: baseline all-errors
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_scan_aborts_on_baseline_all_errors():
    """Scan aborts immediately when baseline eval returns all errors."""
    from api.models.prompt_state import PromptState
    from api.models.search_point import SearchPoint

    baseline = PromptState(instruction="test", persona="default_persona")
    sp = SearchPoint(
        prompt_state=baseline,
        pipeline_params={"steps": ["llm_ranking"]},
    )
    scan_variants = {"persona": ["default_persona", "expert", "analyst"]}
    eval_data = _make_eval_data(4)

    call_count = 0
    import api.services.search.smart_search as _ss
    _orig = _ss.evaluate_prompt_cached

    async def _mock_eval(search_point, data, client, **kw):
        nonlocal call_count
        call_count += 1
        return (
            [{"hit": False, "query": d["query"]} for d in data],
            {"accuracy": 0.0, "hits": 0, "total": 4, "errors": 4},
            False,
        )

    _ss.evaluate_prompt_cached = _mock_eval
    try:
        df, profiles = await sensitivity_scan(
            sp, scan_variants, eval_data, backend_client=None,
        )
        assert df.empty
        assert profiles == []
        assert call_count == 1  # only baseline eval, no variants
    finally:
        _ss.evaluate_prompt_cached = _orig


# ---------------------------------------------------------------------------
# scan circuit breaker: consecutive all-error variants
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_scan_aborts_after_consecutive_all_error_variants():
    """Scan aborts after 2 consecutive non-cached all-error variant evals."""
    from api.models.prompt_state import PromptState
    from api.models.search_point import SearchPoint

    baseline = PromptState(instruction="test", persona="default_persona")
    sp = SearchPoint(
        prompt_state=baseline,
        pipeline_params={"steps": ["llm_ranking"]},
    )
    scan_variants = {"persona": ["default_persona", "expert", "analyst", "reviewer"]}
    eval_data = _make_eval_data(4)

    call_count = 0
    import api.services.search.smart_search as _ss
    _orig = _ss.evaluate_prompt_cached

    async def _mock_eval(search_point, data, client, **kw):
        nonlocal call_count
        call_count += 1
        ps = search_point.prompt_state
        if ps.persona == baseline.persona:
            # Baseline succeeds
            return (
                [{"hit": True, "query": d["query"]} for d in data],
                {"accuracy": 1.0, "hits": 4, "total": 4, "errors": 0},
                False,
            )
        # All variants error out
        return (
            [{"hit": False, "query": d["query"]} for d in data],
            {"accuracy": 0.0, "hits": 0, "total": 4, "errors": 4},
            False,
        )

    _ss.evaluate_prompt_cached = _mock_eval
    try:
        df, profiles = await sensitivity_scan(
            sp, scan_variants, eval_data, backend_client=None,
        )
        # 1 baseline + 2 variants (circuit breaker fires on 2nd all-error)
        assert call_count == 3
        # Partial results still present
        assert len(df) > 0
    finally:
        _ss.evaluate_prompt_cached = _orig


# ---------------------------------------------------------------------------
# scan baseline row reflects actual errors
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_scan_baseline_row_reflects_actual_errors():
    """Baseline-value rows carry actual error count, not hardcoded 0."""
    from api.models.prompt_state import PromptState
    from api.models.search_point import SearchPoint

    baseline = PromptState(instruction="test", persona="default_persona")
    sp = SearchPoint(
        prompt_state=baseline,
        pipeline_params={"steps": ["llm_ranking"]},
    )
    scan_variants = {"persona": ["default_persona", "expert"]}
    eval_data = _make_eval_data(4)

    import api.services.search.smart_search as _ss
    _orig = _ss.evaluate_prompt_cached

    async def _mock_eval(search_point, data, client, **kw):
        ps = search_point.prompt_state
        if ps.persona == baseline.persona:
            return (
                [{"hit": True, "query": d["query"]} for d in data],
                {"accuracy": 0.75, "hits": 3, "total": 4, "errors": 1},
                False,
            )
        return (
            [{"hit": True, "query": d["query"]} for d in data],
            {"accuracy": 1.0, "hits": 4, "total": 4, "errors": 0},
            False,
        )

    _ss.evaluate_prompt_cached = _mock_eval
    try:
        df, profiles = await sensitivity_scan(
            sp, scan_variants, eval_data, backend_client=None,
        )
        baseline_row = df[df["value_preview"] == "default_persona"].iloc[0]
        assert baseline_row["errors"] == 1
    finally:
        _ss.evaluate_prompt_cached = _orig


# ---------------------------------------------------------------------------
# parse_pipeline_response parses available_models
# ---------------------------------------------------------------------------

def test_parse_pipeline_response_available_models():
    """available_models from pipeline response is parsed into PipelineSchema."""
    response = {
        "name": "testpipe",
        "version": "1.0",
        "available_models": [
            "meta-llama/llama-4-maverick-17b-128e-instruct",
            "qwen/qwen3-32b",
        ],
        "nodes": {
            "step_a": {
                "type": "tool",
                "config": {"threshold": 0.5},
            },
        },
    }
    schema = parse_pipeline_response(response)
    assert schema.available_models == [
        "meta-llama/llama-4-maverick-17b-128e-instruct",
        "qwen/qwen3-32b",
    ]


def test_parse_pipeline_response_no_available_models():
    """Missing available_models defaults to empty list."""
    response = {
        "name": "testpipe",
        "version": "1.0",
        "nodes": {
            "step_a": {"type": "tool", "config": {}},
        },
    }
    schema = parse_pipeline_response(response)
    assert schema.available_models == []
