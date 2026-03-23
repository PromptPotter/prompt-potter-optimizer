"""Tests for smart prompt search: diagnostic set, axis classification, variant library."""
import logging

import pandas as pd
import pytest

from api.config.settings import load_variant_library
from api.services.prompt_eval import _error_category
from api.services.search import select_grid_winner
from api.services.search.smart_search import (
    sensitivity_scan,
    _profiles_from_rows,
    _dominant_error_category,
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

    async def _mock_eval(search_point, data, client, **kw):
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


# ---------------------------------------------------------------------------
# Error classification tests
# ---------------------------------------------------------------------------

class TestErrorCategory:
    def test_client_prefix(self):
        assert _error_category("[CLIENT] HTTP 400: bad request") == "CLIENT"

    def test_server_prefix(self):
        assert _error_category("[SERVER] HTTP 500: internal") == "SERVER"

    def test_connection_prefix(self):
        assert _error_category("[CONNECTION] timeout") == "CONNECTION"

    def test_no_prefix(self):
        assert _error_category("some old-style error") is None

    def test_none_input(self):
        assert _error_category(None) is None

    def test_empty_string(self):
        assert _error_category("") is None


class TestDominantErrorCategory:
    def test_all_client(self):
        results = [
            {"error": "[CLIENT] HTTP 400: bad"},
            {"error": "[CLIENT] HTTP 400: bad"},
            {"error": "skipped_after_client_error"},
        ]
        assert _dominant_error_category(results) == "CLIENT"

    def test_mixed_with_connection_dominant(self):
        results = [
            {"error": "[CONNECTION] timeout"},
            {"error": "[CONNECTION] timeout"},
            {"error": "[SERVER] HTTP 500: err"},
        ]
        assert _dominant_error_category(results) == "CONNECTION"

    def test_no_categorized_errors(self):
        results = [
            {"error": "skipped_after_consecutive_errors"},
            {"error": "skipped_after_consecutive_errors"},
        ]
        assert _dominant_error_category(results) is None

    def test_no_errors(self):
        results = [{"error": None}, {"error": None}]
        assert _dominant_error_category(results) is None


@pytest.mark.asyncio
async def test_scan_baseline_client_error_message(scan_eval_mock):
    """Baseline all-CLIENT-errors should mention config, not 'backend down'."""
    from api.models.prompt_state import PromptState
    from api.models.search_point import SearchPoint

    baseline = PromptState(instruction="test", persona="default_persona")
    sp = SearchPoint(
        prompt_state=baseline,
        pipeline_params={"steps": ["llm_ranking"]},
    )
    scan_variants = {"persona": ["default_persona", "expert"]}
    eval_data = _make_eval_data(4)

    events: list[dict] = []

    async def _mock_eval(search_point, data, client, **kw):
        return (
            [{"hit": False, "query": d["query"],
              "error": "[CLIENT] HTTP 400: bad"} for d in data],
            {"accuracy": 0.0, "hits": 0, "total": 4, "errors": 4},
            False,
        )

    scan_eval_mock(_mock_eval)

    df, profiles = await sensitivity_scan(
        sp, scan_variants, eval_data, backend_client=None,
        progress_cb=events.append,
    )
    assert df.empty
    abort_events = [e for e in events if e.get("type") == "scan_aborted"]
    assert len(abort_events) == 1
    reason = abort_events[0]["reason"]
    assert "client errors" in reason.lower()
    assert "backend may be down" not in reason.lower()


@pytest.mark.asyncio
async def test_batch_fast_abort_on_client_error(monkeypatch):
    """evaluate_prompt_batch should abort after first CLIENT error."""
    from api.models.prompt_state import PromptState
    from api.models.search_point import SearchPoint

    import api.services.prompt_eval as _pe

    call_count = 0

    async def _counting_eval(query_data, backend_client, rendered, **kw):
        nonlocal call_count
        call_count += 1
        return {
            "query": query_data["query"],
            "predicted": "ERROR",
            "ground_truth": query_data.get("ground_truth", ""),
            "hit": False,
            "score": 0.0,
            "error": "[CLIENT] HTTP 400: Bad Request",
            "pipeline_data": None,
        }

    monkeypatch.setattr(_pe, "backend_reranker_eval", _counting_eval)

    ps = PromptState(instruction="test")
    sp = SearchPoint(prompt_state=ps)
    eval_data = _make_eval_data(10)

    results = await _pe.evaluate_prompt_batch(sp, eval_data, backend_client=None)
    # Only 1 actual backend call — the rest are skipped
    assert call_count == 1
    assert len(results) == 10
    assert results[0]["error"] == "[CLIENT] HTTP 400: Bad Request"
    assert all(
        r["error"] == "skipped_after_client_error"
        for r in results[1:]
    )


@pytest.mark.asyncio
async def test_batch_server_errors_use_consecutive_threshold(monkeypatch):
    """Server errors should still require 3 consecutive failures before abort."""
    from api.models.prompt_state import PromptState
    from api.models.search_point import SearchPoint

    import api.services.prompt_eval as _pe

    call_count = 0

    async def _server_error_eval(query_data, backend_client, rendered, **kw):
        nonlocal call_count
        call_count += 1
        return {
            "query": query_data["query"],
            "predicted": "ERROR",
            "ground_truth": query_data.get("ground_truth", ""),
            "hit": False,
            "score": 0.0,
            "error": "[SERVER] HTTP 500: Internal Server Error",
            "pipeline_data": None,
        }

    monkeypatch.setattr(_pe, "backend_reranker_eval", _server_error_eval)

    ps = PromptState(instruction="test")
    sp = SearchPoint(prompt_state=ps)
    eval_data = _make_eval_data(10)

    results = await _pe.evaluate_prompt_batch(sp, eval_data, backend_client=None)
    # 3 actual calls (consecutive threshold), then 7 skipped
    assert call_count == 3
    assert len(results) == 10
    skipped = [r for r in results if r["error"] == "skipped_after_consecutive_errors"]
    assert len(skipped) == 7
