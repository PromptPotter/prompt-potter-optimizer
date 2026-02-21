"""Tests for incremental write functionality during replay."""
import json

import pytest

from api.models.backend import Execution


def _make_result(query="q", status="success"):
    return {
        "query": query, "ground_truth": "gt", "predicted": "pred",
        "confidence": 0.5, "ranked_candidates": [], "latency_ms": 100.0,
        "status": status,
    }


def test_append_and_load(tmp_store):
    for i in range(3):
        tmp_store.append_result("b1", "exec1", _make_result(f"q{i}"))

    results = tmp_store.load_partial_results("b1", "exec1")
    assert len(results) == 3
    assert results[2]["query"] == "q2"


def test_finalize(tmp_store):
    tmp_store.append_result("b1", "exec1", _make_result("q0", "success"))
    tmp_store.append_result("b1", "exec1", _make_result("q1", "error"))

    execution = Execution(execution_id="exec1", backend_id="b1", experiment_id="exp1")
    json_path = tmp_store.finalize_execution(execution)

    data = json.loads(json_path.read_text())
    assert data["query_count"] == 2
    assert data["successful_count"] == 1
    assert data["error_count"] == 1

    jsonl_path = tmp_store._executions_dir("b1") / "exec1.jsonl"
    assert not jsonl_path.exists()
