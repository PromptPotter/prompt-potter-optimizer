"""Tests for Langfuse backfill of historical dataset runs.

Verifies:
- Run origin classification by prefix
- Dataset-first structure: items registered with ground truth
- Per-run traces with per-query spans linked to dataset items
- Idempotency (second call skips all)
- Incremental backfill (only new runs pushed)
- Graceful handling when Langfuse is disabled
- State file persistence (format_version=2)
- Old state format reset
"""

import json

import pytest

from api.services.obs.langfuse_backfill import (
    DATASET_NAME,
    backfill_to_langfuse,
    classify_run_origin,
)
from api.services.obs.langfuse_client import LangfuseLogger
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
        self._rate_limit_until = 0.0

        # Dataset API tracking
        self.datasets_created: list[dict] = []
        self.dataset_items_created: list[dict] = []
        self.dataset_items_updated: list[dict] = []
        self.dataset_gets: list[str] = []
        self.dataset_run_links: list[dict] = []
        self._item_counter = 0

    @property
    def rate_limited(self) -> bool:
        import time
        return time.time() < self._rate_limit_until

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

    # Dataset API

    def create_dataset(self, name, description=None, metadata=None):
        self.datasets_created.append({
            "name": name, "description": description, "metadata": metadata,
        })
        return True

    def create_dataset_item(self, dataset_name, input, expected_output=None,
                            metadata=None):
        self._item_counter += 1
        item_id = f"item_{self._item_counter:03d}"
        self.dataset_items_created.append({
            "id": item_id, "dataset_name": dataset_name,
            "input": input, "expected_output": expected_output,
        })
        return item_id

    def get_dataset(self, name):
        self.dataset_gets.append(name)
        # Return empty dataset (no existing items)
        return type("Dataset", (), {"name": name, "items": []})()

    def update_dataset_item(self, item_id, expected_output=None, metadata=None):
        self.dataset_items_updated.append({
            "item_id": item_id, "expected_output": expected_output,
        })
        return True

    def link_item_to_run(self, dataset_item_id, trace_id,
                         observation_id=None, run_name="", run_metadata=None):
        self.dataset_run_links.append({
            "dataset_item_id": dataset_item_id,
            "trace_id": trace_id,
            "observation_id": observation_id,
            "run_name": run_name,
            "run_metadata": run_metadata,
        })
        return True


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
        "dataset_run_items": items,
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


class TestBackfillDatasetFirst:
    def test_registers_dataset_items_with_ground_truth(self, tmp_path, monkeypatch):
        """Backfill registers dataset items with expectedOutput set."""
        store = ProjectStore(tmp_path)
        backend_id = "test-backend"

        items = [
            {"query": "aspirin", "predicted": "Aspirin",
             "ground_truth": "Aspirin", "hit": True},
            {"query": "ibuprofen", "predicted": "wrong",
             "ground_truth": "Ibuprofen", "hit": False},
        ]
        runs = [_make_run("baseline_001", 0.5, items=items)]
        _seed_runs(store, backend_id, runs)

        mock = MockLangfuseLogger()
        monkeypatch.setattr(
            LangfuseLogger, "get_instance", classmethod(lambda cls: mock),
        )

        stats = backfill_to_langfuse(store, backend_id)

        # Dataset created
        assert len(mock.datasets_created) == 1
        assert mock.datasets_created[0]["name"] == DATASET_NAME

        # Dataset items created with ground truth
        assert len(mock.dataset_items_created) == 2
        queries = {it["input"]["query"] for it in mock.dataset_items_created}
        assert queries == {"aspirin", "ibuprofen"}
        for it in mock.dataset_items_created:
            assert it["expected_output"] is not None

        assert stats["dataset_items"] == 2
        assert stats["dataset_name"] == DATASET_NAME

    def test_creates_per_run_traces(self, tmp_path, monkeypatch):
        """One trace per run (not per origin group)."""
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

        # 4 traces (one per run), not 3 (one per origin)
        assert len(mock.traces) == 4
        trace_names = [t["name"] for t in mock.traces]
        assert "eval_baseline_001" in trace_names
        assert "eval_grid_001" in trace_names
        assert "eval_grid_002" in trace_names
        assert "eval_scan_001" in trace_names

        # All traces have "eval" tag + origin tag
        for t in mock.traces:
            assert "eval" in t["tags"]

        # Session IDs are dataset-based
        for t in mock.traces:
            assert t["session_id"] == f"dataset_{backend_id}"

        # Each run gets an accuracy score
        assert len(mock.scores) == 4
        for s in mock.scores:
            assert s["name"] == "accuracy"

        # Each trace gets ended
        assert len(mock.end_trace_calls) == 4

        # Grid search origin stats
        grid_stats = stats["origins"]["grid_search"]
        assert grid_stats["n_runs"] == 2
        assert grid_stats["best_accuracy"] == 0.8
        assert grid_stats["avg_accuracy"] == pytest.approx(0.7)

    def test_per_query_spans_linked_to_dataset(self, tmp_path, monkeypatch):
        """Each query in a run creates a span linked to a dataset item."""
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

        # 2 per-query spans under the trace
        assert len(mock.spans) == 2

        # Spans have query/predicted/hit structure
        span_inputs = {s["input"]["query"] for s in mock.spans}
        assert span_inputs == {"q1", "q2"}
        for s in mock.spans:
            assert "predicted" in s["output"]
            assert "hit" in s["output"]

        # 2 dataset run links (one per query)
        assert len(mock.dataset_run_links) == 2
        for link in mock.dataset_run_links:
            assert link["run_name"] == "baseline_001"
            assert link["dataset_item_id"] is not None


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

        # Second backfill — should be a no-op for runs
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

        # Only new run traces created (baseline already done)
        assert len(mock.traces) == 2
        trace_names = {t["name"] for t in mock.traces}
        assert "eval_grid_001" in trace_names
        assert "eval_grid_002" in trace_names


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
    def test_state_persisted_v2(self, tmp_path, monkeypatch):
        """backfill_state.json is written with format_version=2 and per-run trace IDs."""
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
        assert state["format_version"] == 2
        assert set(state["backfilled_run_ids"]) == {"baseline_001", "scan_001"}
        assert state["last_backfill_at"] is not None
        # Per-run trace IDs (not per-origin)
        assert "baseline_001" in state["langfuse_trace_ids"]
        assert "scan_001" in state["langfuse_trace_ids"]
        # Dataset items tracked
        assert len(state["dataset_items"]) > 0

    def test_old_state_format_reset(self, tmp_path, monkeypatch):
        """Old state format (without format_version) gets reset."""
        store = ProjectStore(tmp_path)
        backend_id = "test-backend"

        # Write old-format state
        state_path = (
            tmp_path / backend_id / "obs" / "langfuse" / "backfill_state.json"
        )
        state_path.parent.mkdir(parents=True, exist_ok=True)
        old_state = {
            "backfilled_run_ids": ["baseline_001"],
            "last_backfill_at": "2026-01-01T00:00:00Z",
            "langfuse_trace_ids": {"baseline": "old_trace_id"},
        }
        state_path.write_text(json.dumps(old_state), encoding="utf-8")

        runs = [_make_run("baseline_001", 0.5)]
        _seed_runs(store, backend_id, runs)

        mock = MockLangfuseLogger()
        monkeypatch.setattr(
            LangfuseLogger, "get_instance", classmethod(lambda cls: mock),
        )

        stats = backfill_to_langfuse(store, backend_id)

        # Old state was reset, so baseline_001 gets re-backfilled
        assert stats["new_runs"] == 1
        assert len(mock.traces) == 1
