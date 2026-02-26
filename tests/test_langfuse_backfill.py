"""Tests for Langfuse backfill of historical dataset runs.

Verifies:
- Run origin classification by prefix
- Trace/span/score creation per origin group
- Idempotency (second call skips all)
- Incremental backfill (only new runs pushed)
- Graceful handling when Langfuse is disabled
- State file persistence
"""

import json

import pytest

from api.services.langfuse_backfill import (
    backfill_to_langfuse,
    classify_run_origin,
)
from api.services.langfuse_client import LangfuseLogger
from api.services.project_store import ProjectStore


# ---------------------------------------------------------------------------
# Mock Langfuse logger (same pattern as test_langfuse_integration.py)
# ---------------------------------------------------------------------------


class MockLangfuseLogger:
    """Records all Langfuse calls for test verification."""

    def __init__(self, *, enabled=True):
        self.enabled = enabled
        self.traces: list[dict] = []
        self.spans: list[dict] = []
        self.scores: list[dict] = []
        self.trace_updates: list[dict] = []
        self.end_trace_calls: list[str] = []
        self.flush_count = 0
        self._counter = 0

    def create_trace(self, name, input, metadata=None, user_id=None,
                     session_id=None, tags=None):
        if not self.enabled:
            return None
        self._counter += 1
        tid = f"mock_trace_{self._counter:03d}"
        self.traces.append({
            "id": tid, "name": name, "input": input,
            "session_id": session_id, "tags": tags,
        })
        return tid

    def create_span(self, trace_id, name, input, output, metadata=None):
        self.spans.append({
            "trace_id": trace_id, "name": name,
            "input": input, "output": output,
        })
        return f"span_{name}"

    def create_score(self, trace_id, name, value, data_type="NUMERIC",
                     comment=None):
        self.scores.append({
            "trace_id": trace_id, "name": name, "value": value,
        })
        return True

    def update_trace(self, trace_id, output=None, metadata=None):
        self.trace_updates.append({
            "trace_id": trace_id, "output": output,
        })
        return True

    def end_trace(self, trace_id):
        self.end_trace_calls.append(trace_id)

    def flush(self):
        self.flush_count += 1


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_run(run_id: str, accuracy: float, items: list[dict] | None = None):
    """Build a minimal dataset_run dict."""
    if items is None:
        items = [
            {"query": "aspirin", "predicted": "Aspirin",
             "ground_truth": "Aspirin", "hit": True},
        ]
    hits = sum(1 for it in items if it.get("hit"))
    return {
        "run_id": run_id,
        "name": run_id,
        "content_hash": f"hash_{run_id}",
        "model": "test-model",
        "prompt_state_id": f"ps_{run_id}",
        "temperature": 0.0,
        "item_count": len(items),
        "items": items,
        "scores": {
            "accuracy": accuracy,
            "hits": hits,
            "total": len(items),
        },
        "created_at": "2026-01-01T00:00:00Z",
    }


def _seed_runs(store: ProjectStore, backend_id: str, runs: list[dict]):
    """Save runs to the store so they appear in list_all / load_by_id."""
    for run in runs:
        store.dataset_runs.save(backend_id, run["run_id"], run)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestClassifyRunOrigin:
    def test_baseline(self):
        assert classify_run_origin("baseline_816203b2") == "baseline"

    def test_grid(self):
        assert classify_run_origin("grid_00230b37") == "grid_search"

    def test_scan(self):
        assert classify_run_origin("scan_05bb3a11") == "sensitivity_scan"

    def test_candidate(self):
        assert classify_run_origin("candidate_0_4101eac4") == "feedback_cycle"

    def test_smart_search_winner(self):
        assert classify_run_origin("smart_search_winner_8a33a6ef") == "smart_search_winner"

    def test_other(self):
        assert classify_run_origin("unknown_run_xyz") == "other"

    def test_empty(self):
        assert classify_run_origin("") == "other"


class TestBackfillCreatesTracesPerOrigin:
    def test_creates_traces_and_spans(self, tmp_path, monkeypatch):
        """One trace per origin group, one span per run, scores on each trace."""
        store = ProjectStore(tmp_path)
        backend_id = "test-backend"

        runs = [
            _make_run("baseline_001", 0.5),
            _make_run("grid_001", 0.8),
            _make_run("grid_002", 0.6),
            _make_run("scan_001", 0.7),
        ]
        _seed_runs(store, backend_id, runs)

        mock = MockLangfuseLogger()
        monkeypatch.setattr(
            LangfuseLogger, "get_instance", classmethod(lambda cls: mock),
        )

        stats = backfill_to_langfuse(store, backend_id)

        assert stats["total_on_disk"] == 4
        assert stats["new_runs"] == 4
        assert stats["already_done"] == 0

        # 3 origin groups: baseline, grid_search, sensitivity_scan
        assert len(mock.traces) == 3
        trace_names = [t["name"] for t in mock.traces]
        assert "backfill_baseline" in trace_names
        assert "backfill_grid_search" in trace_names
        assert "backfill_sensitivity_scan" in trace_names

        # All traces tagged with "backfill"
        for t in mock.traces:
            assert "backfill" in t["tags"]

        # Session IDs
        for t in mock.traces:
            assert t["session_id"] == f"backfill_{backend_id}"

        # 4 spans total (one per run)
        assert len(mock.spans) == 4
        span_names = {s["name"] for s in mock.spans}
        assert "baseline_001" in span_names
        assert "grid_001" in span_names
        assert "grid_002" in span_names
        assert "scan_001" in span_names

        # Each trace gets best_accuracy + avg_accuracy scores (3 groups * 2 = 6)
        assert len(mock.scores) == 6

        # Each trace gets updated with output
        assert len(mock.trace_updates) == 3

        # Each trace gets ended
        assert len(mock.end_trace_calls) == 3

        # Grid search origin stats
        grid_stats = stats["origins"]["grid_search"]
        assert grid_stats["n_runs"] == 2
        assert grid_stats["best_accuracy"] == 0.8
        assert grid_stats["avg_accuracy"] == pytest.approx(0.7)


class TestBackfillIdempotent:
    def test_second_call_skips_all(self, tmp_path, monkeypatch):
        """After a successful backfill, running again pushes nothing new."""
        store = ProjectStore(tmp_path)
        backend_id = "test-backend"

        runs = [
            _make_run("baseline_001", 0.5),
            _make_run("grid_001", 0.8),
        ]
        _seed_runs(store, backend_id, runs)

        mock = MockLangfuseLogger()
        monkeypatch.setattr(
            LangfuseLogger, "get_instance", classmethod(lambda cls: mock),
        )

        # First backfill
        stats1 = backfill_to_langfuse(store, backend_id)
        assert stats1["new_runs"] == 2

        # Reset mock counters
        mock.traces.clear()
        mock.spans.clear()
        mock.scores.clear()
        mock.trace_updates.clear()
        mock.end_trace_calls.clear()

        # Second backfill — should be a no-op
        stats2 = backfill_to_langfuse(store, backend_id)
        assert stats2["new_runs"] == 0
        assert stats2["already_done"] == 2
        assert len(mock.traces) == 0
        assert len(mock.spans) == 0


class TestBackfillIncremental:
    def test_only_new_runs_pushed(self, tmp_path, monkeypatch):
        """Adding runs after first backfill: only new ones get pushed."""
        store = ProjectStore(tmp_path)
        backend_id = "test-backend"

        runs1 = [_make_run("baseline_001", 0.5)]
        _seed_runs(store, backend_id, runs1)

        mock = MockLangfuseLogger()
        monkeypatch.setattr(
            LangfuseLogger, "get_instance", classmethod(lambda cls: mock),
        )

        stats1 = backfill_to_langfuse(store, backend_id)
        assert stats1["new_runs"] == 1

        # Add more runs
        runs2 = [_make_run("grid_001", 0.9), _make_run("grid_002", 0.7)]
        _seed_runs(store, backend_id, runs2)

        mock.traces.clear()
        mock.spans.clear()
        mock.scores.clear()

        stats2 = backfill_to_langfuse(store, backend_id)
        assert stats2["new_runs"] == 2
        assert stats2["already_done"] == 1
        assert stats2["total_on_disk"] == 3

        # Only grid_search trace created (baseline already done)
        assert len(mock.traces) == 1
        assert mock.traces[0]["name"] == "backfill_grid_search"
        assert len(mock.spans) == 2


class TestBackfillDisabledLangfuse:
    def test_returns_error(self, tmp_path, monkeypatch):
        """When Langfuse is disabled, returns error dict."""
        store = ProjectStore(tmp_path)
        backend_id = "test-backend"

        mock = MockLangfuseLogger(enabled=False)
        monkeypatch.setattr(
            LangfuseLogger, "get_instance", classmethod(lambda cls: mock),
        )

        stats = backfill_to_langfuse(store, backend_id)
        assert "error" in stats
        assert "disabled" in stats["error"].lower()


class TestStateFileWritten:
    def test_state_persisted(self, tmp_path, monkeypatch):
        """backfill_state.json is written with run IDs and trace IDs."""
        store = ProjectStore(tmp_path)
        backend_id = "test-backend"

        runs = [
            _make_run("baseline_001", 0.5),
            _make_run("scan_001", 0.7),
        ]
        _seed_runs(store, backend_id, runs)

        mock = MockLangfuseLogger()
        monkeypatch.setattr(
            LangfuseLogger, "get_instance", classmethod(lambda cls: mock),
        )

        backfill_to_langfuse(store, backend_id)

        state_path = (
            tmp_path / backend_id / "obs" / "langfuse" / "backfill_state.json"
        )
        assert state_path.exists()

        state = json.loads(state_path.read_text(encoding="utf-8"))
        assert set(state["backfilled_run_ids"]) == {"baseline_001", "scan_001"}
        assert state["last_backfill_at"] is not None
        assert "baseline" in state["langfuse_trace_ids"]
        assert "sensitivity_scan" in state["langfuse_trace_ids"]


class TestSpanOutputContainsItems:
    def test_items_in_span_output(self, tmp_path, monkeypatch):
        """Each span's output includes per-query items."""
        store = ProjectStore(tmp_path)
        backend_id = "test-backend"

        items = [
            {"query": "q1", "predicted": "p1", "ground_truth": "p1", "hit": True},
            {"query": "q2", "predicted": "wrong", "ground_truth": "p2", "hit": False},
        ]
        runs = [_make_run("baseline_001", 0.5, items=items)]
        _seed_runs(store, backend_id, runs)

        mock = MockLangfuseLogger()
        monkeypatch.setattr(
            LangfuseLogger, "get_instance", classmethod(lambda cls: mock),
        )

        backfill_to_langfuse(store, backend_id)

        assert len(mock.spans) == 1
        output = mock.spans[0]["output"]
        assert output["accuracy"] == 0.5
        assert len(output["items"]) == 2
        assert output["items"][0]["query"] == "q1"
        assert output["items"][0]["hit"] is True
        assert output["items"][1]["hit"] is False
