"""Regression tests for the stale-data protocol.

Guards against the samplescan probe erasing the campaign's pipeline identity
by passing ``pipeline_params=None`` to ``measure_sample``. When that happens,
the wire payload has no ``steps`` key, and the backend falls back to its full
default pipeline — for TermNorm that means ``fuzzy_matching → web_search → …``
instead of the campaign's declared single node.

Also guards the cache-reuse branch of ``_run_query_loop``: a degraded prior
``dataset_runs/`` entry must route through the recovery ladder instead of
being silently recycled into the current round's metrics.
"""

from __future__ import annotations

from typing import Any, cast

import pytest

from promptpotter.application.scoring import search_point_scorer, stale_data
from promptpotter.application.scoring.search_point_scorer import _run_query_loop
from promptpotter.application.scoring.stale_data import execute_stale_data_protocol
from promptpotter.domain.pipeline_schema import NodePromptMeta, PipelineNode, PipelineSchema
from promptpotter.domain.scoring import QueryResult, ScoringEnv
from promptpotter.domain.search_point import JobSearchPoint
from promptpotter.shared.scoring import compile_scorer

_TRIVIAL_SCORER = compile_scorer("float(hit)")


def _llm_only_schema() -> PipelineSchema:
    return PipelineSchema(
        name="bbeh",
        nodes=[PipelineNode(name="llm_only", prompt_meta=NodePromptMeta())],
    )


def _degraded_cached_result() -> dict[str, Any]:
    return {
        "query": "q",
        "ground_truth": "gt",
        "predicted": "wrong",
        "hit": False,
        "score": 0.0,
        "cached": True,
        "pipeline_data": {
            "final_ranking": [{"candidate": "wrong"}],
            "diagnostics": {
                "warnings": [{"step": "llm_only", "code": "empty_content_reasoning_fallback"}]
            },
        },
    }


@pytest.mark.asyncio
async def test_samplescan_probe_preserves_pipeline_steps(monkeypatch):
    """Samplescan must probe within the campaign's declared pipeline.

    Passing ``pipeline_params=None`` (pre-fix behavior) erases the schema-
    derived ``steps`` filter and causes the TermNorm backend to run its full
    default research pipeline. This test captures the ``measure_sample`` call
    args and asserts the probe carries the correct ``steps`` list.
    """
    captured: dict[str, Any] = {}

    async def fake_measure_sample(query_data, env, pipeline_params=None):
        captured["pipeline_params"] = pipeline_params
        return {
            "query": query_data["query"],
            "ground_truth": query_data["ground_truth"],
            "predicted": "ok",
            "hit": True,
            "score": 1.0,
            "pipeline_data": {"final_ranking": [{"candidate": "ok"}], "diagnostics": {}},
        }

    monkeypatch.setattr(stale_data, "measure_sample", fake_measure_sample)

    env = ScoringEnv(
        backend_client=object(),  # type: ignore[arg-type]
        scorer=_TRIVIAL_SCORER,
        pipeline_schema=_llm_only_schema(),
    )
    query_data = {"query": "q", "ground_truth": "gt"}
    cached = _degraded_cached_result()

    result, step_taken = await execute_stale_data_protocol(
        ["samplescan"],
        query_data,
        cached,
        env,
        pipeline_params=None,
    )

    assert step_taken == "samplescan"
    assert result["samplescan_probe"] is True

    probe_params = captured["pipeline_params"]
    assert probe_params is not None, (
        "samplescan must not pass pipeline_params=None — that erases the "
        "steps restriction and makes the backend run its full default pipeline"
    )
    assert probe_params["steps"] == ["llm_only"]


@pytest.mark.asyncio
async def test_degraded_prior_result_routes_through_protocol(monkeypatch):
    """Cache-reuse must not silently recycle degraded prior dataset_runs entries.

    Before the fix, `_run_query_loop` reused any ``prior_results[query]`` entry
    unconditionally. A degraded cached result (e.g. containing
    ``empty_content_reasoning_fallback`` warnings) could be promoted back into
    the current round's metrics without ever running the recovery ladder.
    This test captures the protocol call and confirms degraded cached entries
    are recovered instead of recycled.
    """
    calls: list[dict[str, Any]] = []

    async def fake_protocol(steps, query_data, cached_result, env, *, pipeline_params=None):
        calls.append({"query": query_data["query"], "cached": cached_result})
        recovered = {
            **cached_result,
            "predicted": "recovered",
            "hit": True,
            "score": 1.0,
            "pipeline_data": {"final_ranking": [{"candidate": "recovered"}], "diagnostics": {}},
            "retry_of_degraded": True,
        }
        return recovered, "rerun"

    monkeypatch.setattr(search_point_scorer, "_execute_stale_data_protocol", fake_protocol)

    env = ScoringEnv(
        backend_client=object(),  # type: ignore[arg-type]
        scorer=_TRIVIAL_SCORER,
        pipeline_schema=_llm_only_schema(),
        stale_data_load_protocol=["rerun"],
    )
    sp = JobSearchPoint(pipeline_params={"llm_only": {}})
    dataset = [{"query": "q", "ground_truth": "gt"}]
    prior: dict[str, QueryResult] = {"q": cast(QueryResult, _degraded_cached_result())}

    batch = await _run_query_loop(
        sp,
        dataset,
        env,
        prior_results=prior,
        on_result=None,
        on_start=None,
        degradation_checks=None,
        candidate_idx=0,
        n_total_candidates=1,
        save_run=None,
    )

    assert len(calls) == 1, "degraded cached result must invoke the recovery protocol"
    assert calls[0]["query"] == "q"
    assert batch.results[0]["predicted"] == "recovered"
    assert batch.results[0].get("retry_of_degraded") is True


@pytest.mark.asyncio
async def test_clean_prior_result_skips_protocol(monkeypatch):
    """Non-degraded cached results reuse directly without invoking the ladder."""
    calls: list[Any] = []

    async def fake_protocol(*args, **kwargs):
        calls.append((args, kwargs))
        return {}, "rerun"

    monkeypatch.setattr(search_point_scorer, "_execute_stale_data_protocol", fake_protocol)

    clean: QueryResult = cast(
        QueryResult,
        {
            "query": "q",
            "ground_truth": "gt",
            "predicted": "ok",
            "hit": True,
            "score": 1.0,
            "pipeline_data": {"final_ranking": [{"candidate": "ok"}], "diagnostics": {}},
        },
    )
    env = ScoringEnv(
        backend_client=object(),  # type: ignore[arg-type]
        scorer=_TRIVIAL_SCORER,
        pipeline_schema=_llm_only_schema(),
        stale_data_load_protocol=["rerun"],
    )
    sp = JobSearchPoint(pipeline_params={"llm_only": {}})

    batch = await _run_query_loop(
        sp,
        [{"query": "q", "ground_truth": "gt"}],
        env,
        prior_results={"q": clean},
        on_result=None,
        on_start=None,
        degradation_checks=None,
        candidate_idx=0,
        n_total_candidates=1,
        save_run=None,
    )

    assert calls == [], "clean cached results must not invoke the recovery protocol"
    assert batch.results[0]["predicted"] == "ok"
