"""Tests for smart prompt search: diagnostic set, axis classification, variant library."""
import logging

import pandas as pd
import pytest

from api.config.settings import load_variant_library
from api.services.search import select_grid_winner
from api.services.search.smart_search import (
    sensitivity_scan,
    _profiles_from_rows,
)
from api.services.pipeline_discovery import parse_pipeline_response


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


@pytest.fixture
def scan_eval_mock(monkeypatch):
    """Fixture that returns a function to patch evaluate_prompt_cached for scan tests."""
    import api.services.search.smart_search as _ss

    def _apply(mock_eval):
        monkeypatch.setattr(_ss, "evaluate_prompt_cached", mock_eval)

    return _apply


def test_select_grid_winner_warns_small_sample(caplog):
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


def test_load_variant_library():
    lib = load_variant_library()
    assert "prompt_fields" in lib
    assert "persona" in lib["prompt_fields"]
    assert len(lib["prompt_fields"]["persona"]) > 1


def test_profiles_from_rows():
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


@pytest.mark.asyncio
async def test_scan_rows_include_errors(scan_eval_mock):
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

    def _mock_eval(search_point, data, client, **kw):
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

    scan_eval_mock(_mock_eval)

    df, profiles = await sensitivity_scan(
        sp, scan_variants, eval_data, backend_client=None,
    )

    # Both baseline and perturbed rows should have 'errors' field
    assert "errors" in df.columns
    baseline_row = df[df["value_preview"] == "default_persona"].iloc[0]
    assert baseline_row["errors"] == 0
    perturbed_row = df[df["value_preview"] == "expert"].iloc[0]
    assert perturbed_row["errors"] == 2


@pytest.mark.asyncio
async def test_scan_skips_all_error_axis(scan_eval_mock):
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

    def _mock_eval(search_point, data, client, **kw):
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

    scan_eval_mock(_mock_eval)

    df, profiles = await sensitivity_scan(
        sp, scan_variants, eval_data, backend_client=None,
    )

    # Both axes should still be in the results (rows are preserved)
    assert set(df["axis"].unique()) == {"persona", "task_intent"}


@pytest.mark.asyncio
async def test_scan_aborts_on_baseline_all_errors(scan_eval_mock):
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

    def _mock_eval(search_point, data, client, **kw):
        nonlocal call_count
        call_count += 1
        return (
            [{"hit": False, "query": d["query"]} for d in data],
            {"accuracy": 0.0, "hits": 0, "total": 4, "errors": 4},
            False,
        )

    scan_eval_mock(_mock_eval)

    df, profiles = await sensitivity_scan(
        sp, scan_variants, eval_data, backend_client=None,
    )
    assert df.empty
    assert profiles == []
    assert call_count == 1  # only baseline eval, no variants


@pytest.mark.asyncio
async def test_scan_aborts_after_consecutive_all_error_variants(scan_eval_mock):
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

    def _mock_eval(search_point, data, client, **kw):
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

    scan_eval_mock(_mock_eval)

    df, profiles = await sensitivity_scan(
        sp, scan_variants, eval_data, backend_client=None,
    )
    # 1 baseline + 2 variants (circuit breaker fires on 2nd all-error)
    assert call_count == 3
    # Partial results still present
    assert len(df) > 0


@pytest.mark.asyncio
async def test_scan_baseline_row_reflects_actual_errors(scan_eval_mock):
    from api.models.prompt_state import PromptState
    from api.models.search_point import SearchPoint

    baseline = PromptState(instruction="test", persona="default_persona")
    sp = SearchPoint(
        prompt_state=baseline,
        pipeline_params={"steps": ["llm_ranking"]},
    )
    scan_variants = {"persona": ["default_persona", "expert"]}
    eval_data = _make_eval_data(4)

    def _mock_eval(search_point, data, client, **kw):
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

    scan_eval_mock(_mock_eval)

    df, profiles = await sensitivity_scan(
        sp, scan_variants, eval_data, backend_client=None,
    )
    baseline_row = df[df["value_preview"] == "default_persona"].iloc[0]
    assert baseline_row["errors"] == 1


def test_parse_pipeline_response_available_models():
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
    response = {
        "name": "testpipe",
        "version": "1.0",
        "nodes": {
            "step_a": {"type": "tool", "config": {}},
        },
    }
    schema = parse_pipeline_response(response)
    assert schema.available_models == []
