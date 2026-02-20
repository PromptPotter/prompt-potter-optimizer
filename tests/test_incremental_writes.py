"""Tests for incremental write functionality during replay."""
import json
from unittest.mock import AsyncMock, patch

import pytest

from api.models.backend import Execution, ExecutionResultItem
from api.services.project_store import ProjectStore


@pytest.fixture
def tmp_store(tmp_path):
    """ProjectStore with a temporary base directory."""
    return ProjectStore(base_dir=tmp_path)


def _make_result(query="q", status="success"):
    return {
        "query": query,
        "ground_truth": "gt",
        "predicted": "pred",
        "confidence": 0.5,
        "ranked_candidates": [],
        "latency_ms": 100.0,
        "status": status,
    }


class TestAppendResult:
    def test_creates_jsonl_file(self, tmp_store):
        path = tmp_store.append_result("b1", "exec1", _make_result())
        assert path.exists()
        assert path.suffix == ".jsonl"

    def test_appends_multiple_lines(self, tmp_store):
        for i in range(3):
            tmp_store.append_result("b1", "exec1", _make_result(f"q{i}"))
        path = tmp_store._executions_dir("b1") / "exec1.jsonl"
        lines = path.read_text().strip().split("\n")
        assert len(lines) == 3
        assert json.loads(lines[2])["query"] == "q2"

    def test_each_line_is_valid_json(self, tmp_store):
        for i in range(5):
            tmp_store.append_result("b1", "exec1", _make_result(f"q{i}"))
        path = tmp_store._executions_dir("b1") / "exec1.jsonl"
        for line in path.read_text().strip().split("\n"):
            parsed = json.loads(line)
            assert "query" in parsed


class TestLoadPartialResults:
    def test_returns_empty_for_missing_file(self, tmp_store):
        assert tmp_store.load_partial_results("b1", "nope") == []

    def test_reads_all_lines(self, tmp_store):
        for i in range(4):
            tmp_store.append_result("b1", "exec1", _make_result(f"q{i}"))
        results = tmp_store.load_partial_results("b1", "exec1")
        assert len(results) == 4
        assert results[3]["query"] == "q3"


class TestFinalizeExecution:
    def test_writes_json_and_deletes_jsonl(self, tmp_store):
        for i in range(3):
            tmp_store.append_result("b1", "exec1", _make_result(f"q{i}"))

        execution = Execution(
            execution_id="exec1",
            backend_id="b1",
            experiment_id="exp1",
        )
        json_path = tmp_store.finalize_execution(execution)

        assert json_path.exists()
        data = json.loads(json_path.read_text())
        assert data["query_count"] == 3
        assert len(data["results"]) == 3

        jsonl_path = tmp_store._executions_dir("b1") / "exec1.jsonl"
        assert not jsonl_path.exists()

    def test_uses_existing_results_if_populated(self, tmp_store):
        execution = Execution(
            execution_id="exec2",
            backend_id="b1",
            experiment_id="exp1",
            query_count=1,
            successful_count=1,
            results=[
                ExecutionResultItem(
                    query="q1", ground_truth="gt", predicted="pred"
                )
            ],
        )
        json_path = tmp_store.finalize_execution(execution)
        data = json.loads(json_path.read_text())
        assert len(data["results"]) == 1

    def test_counts_success_and_errors(self, tmp_store):
        tmp_store.append_result("b1", "exec3", _make_result("q0", "success"))
        tmp_store.append_result("b1", "exec3", _make_result("q1", "error"))
        tmp_store.append_result("b1", "exec3", _make_result("q2", "success"))

        execution = Execution(
            execution_id="exec3",
            backend_id="b1",
            experiment_id="exp1",
        )
        json_path = tmp_store.finalize_execution(execution)
        data = json.loads(json_path.read_text())
        assert data["successful_count"] == 2
        assert data["error_count"] == 1


class TestReplayCallback:
    """Test that replay_queries invokes the on_result callback."""

    @pytest.mark.asyncio
    async def test_callback_called_for_each_query(self):
        from api.services.backend_client import BackendClient

        client = BackendClient("http://fake:8000")

        with patch.object(client, "init_session", new_callable=AsyncMock):
            with patch.object(
                client,
                "run_match",
                new_callable=AsyncMock,
                return_value={
                    "data": {
                        "ranked_candidates": [
                            {"candidate": "x", "relevance_score": 0.9}
                        ]
                    }
                },
            ):
                callback_calls = []

                async def mock_callback(result, index, total):
                    callback_calls.append((index, total, result["status"]))

                queries = [
                    {
                        "query": "q1",
                        "bom_material": "b",
                        "process": "",
                        "ground_truth": "x",
                        "original_predicted": "",
                        "original_latency_ms": 0,
                        "original_confidence": 0,
                    },
                    {
                        "query": "q2",
                        "bom_material": "b",
                        "process": "",
                        "ground_truth": "y",
                        "original_predicted": "",
                        "original_latency_ms": 0,
                        "original_confidence": 0,
                    },
                ]

                results = await client.replay_queries(
                    queries=queries,
                    terms=["t1"],
                    delay_between=0.0,
                    on_result=mock_callback,
                )

                assert len(results) == 2
                assert len(callback_calls) == 2
                assert callback_calls[0] == (0, 2, "success")
                assert callback_calls[1] == (1, 2, "success")

    @pytest.mark.asyncio
    async def test_no_callback_still_works(self):
        """Backward compatibility — replay_queries works without on_result."""
        from api.services.backend_client import BackendClient

        client = BackendClient("http://fake:8000")

        with patch.object(client, "init_session", new_callable=AsyncMock):
            with patch.object(
                client,
                "run_match",
                new_callable=AsyncMock,
                return_value={
                    "data": {
                        "ranked_candidates": [
                            {"candidate": "x", "relevance_score": 0.9}
                        ]
                    }
                },
            ):
                queries = [
                    {
                        "query": "q1",
                        "bom_material": "b",
                        "process": "",
                        "ground_truth": "x",
                        "original_predicted": "",
                        "original_latency_ms": 0,
                        "original_confidence": 0,
                    },
                ]

                results = await client.replay_queries(
                    queries=queries,
                    terms=["t1"],
                    delay_between=0.0,
                )

                assert len(results) == 1
                assert results[0]["status"] == "success"
