"""Regression tests for the stale-data protocol.

Guards against the samplescan probe erasing the campaign's pipeline identity
by passing ``pipeline_params=None`` to ``measure_sample``. When that happens,
the wire payload has no ``steps`` key, and the backend falls back to its full
default pipeline — for TermNorm that means ``fuzzy_matching → web_search → …``
instead of the campaign's declared single node.
"""

from __future__ import annotations

from typing import Any

import pytest

from promptpotter.application.scoring import stale_data
from promptpotter.application.scoring.stale_data import execute_stale_data_protocol
from promptpotter.domain.pipeline_schema import NodePromptMeta, PipelineNode, PipelineSchema
from promptpotter.domain.scoring import ScoringEnv


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
