"""Tests for Langfuse cloud push of dataset runs.

Verifies:
- Run origin classification by prefix
- Dataset-first structure: items registered with ground truth
- Per-query pipeline traces (one trace per query, flat pipeline step children)
- Idempotency (second call skips all)
- Incremental push (only new runs pushed)
- Graceful handling when Langfuse is disabled
- State file persistence (format_version=3)
- Old state format reset
- push_run() single-run idempotency and trace format
"""

import json

import pytest

from api.services.obs.langfuse_push import (
    DATASET_NAME,
    classify_run_origin,
    push_all_runs,
    push_run,
)
from api.services.obs.langfuse_client import LangfuseLogger
from api.services.project_store import ProjectStore

from _helpers import MockLangfuseLogger


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
# Tests — classify_run_origin
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


# ---------------------------------------------------------------------------
# Tests — push_run (single-run, per-query traces)
# ---------------------------------------------------------------------------


class TestPushRun:
    def test_push_run_returns_list(self, tmp_path):
        """push_run returns a list of per-query trace IDs."""
        store = ProjectStore(tmp_path)
        backend_id = "test-backend"

        items = [
            {"query": "q1", "predicted": "p1", "ground_truth": "p1", "hit": True},
            {"query": "q2", "predicted": "wrong", "ground_truth": "p2", "hit": False},
        ]
        runs = [_make_run("baseline_001", 0.5, items=items)]
        _seed_runs(store, backend_id, runs)

        mock = MockLangfuseLogger()
        result = push_run(mock, store, backend_id, "baseline_001")

        assert isinstance(result, list)
        assert len(result) == 2  # one trace per query
        assert all(tid.startswith("mock_trace_") for tid in result)
        assert len(mock.traces) == 2  # 2 rooted traces

    def test_push_run_idempotent(self, tmp_path):
        """push_run creates traces first time, returns None on second call."""
        store = ProjectStore(tmp_path)
        backend_id = "test-backend"

        items = [
            {"query": "q1", "predicted": "p1", "ground_truth": "p1", "hit": True},
        ]
        runs = [_make_run("baseline_001", 1.0, items=items)]
        _seed_runs(store, backend_id, runs)

        mock = MockLangfuseLogger()

        # First push
        result = push_run(mock, store, backend_id, "baseline_001")
        assert result is not None
        assert len(mock.traces) == 1

        # Second push — idempotent
        result_2 = push_run(mock, store, backend_id, "baseline_001")
        assert result_2 is None
        assert len(mock.traces) == 1

    def test_push_run_no_pipeline_data_format(self, tmp_path):
        """Without pipeline_data, creates rooted trace with no child spans."""
        store = ProjectStore(tmp_path)
        backend_id = "test-backend"

        items = [
            {"query": "q1", "predicted": "p1", "ground_truth": "p1", "hit": True},
            {"query": "q2", "predicted": "wrong", "ground_truth": "p2", "hit": False},
        ]
        runs = [_make_run("grid_001", 0.5, items=items)]
        _seed_runs(store, backend_id, runs)

        mock = MockLangfuseLogger()
        result = push_run(mock, store, backend_id, "grid_001")
        assert len(result) == 2

        # Rooted traces
        assert len(mock.traces) == 2
        for t in mock.traces:
            assert t["name"] == "termnorm_pipeline"
            assert "eval" in t["tags"]
            assert "grid_search" in t["tags"]
            assert "pipeline" in t["tags"]
            assert t["session_id"] == f"dataset_{backend_id}"

        # No pipeline step spans (no pipeline_data)
        assert len(mock.spans) == 0

        # Each trace gets a hit score
        assert len(mock.scores) == 2
        for s in mock.scores:
            assert s["name"] == "hit"

        # Trace output via update_trace + end_trace
        assert len(mock.trace_updates) == 2
        for tu in mock.trace_updates:
            assert "predicted" in tu["output"]
            assert "ground_truth" in tu["output"]
            assert "hit" in tu["output"]
        assert len(mock.end_trace_calls) == 2

    def test_push_run_with_dataset_linking(self, tmp_path):
        """push_run links traces to dataset items when query_to_item_id given."""
        store = ProjectStore(tmp_path)
        backend_id = "test-backend"

        items = [
            {"query": "q1", "predicted": "p1", "ground_truth": "p1", "hit": True},
        ]
        runs = [_make_run("baseline_001", 1.0, items=items)]
        _seed_runs(store, backend_id, runs)

        mock = MockLangfuseLogger()
        push_run(
            mock, store, backend_id, "baseline_001",
            query_to_item_id={"q1": "item_abc"},
        )

        assert len(mock.dataset_run_links) == 1
        link = mock.dataset_run_links[0]
        assert link["dataset_item_id"] == "item_abc"
        assert link["run_name"] == "baseline_001"
        # Links to trace, not observation
        assert link["trace_id"].startswith("mock_trace_")

    def test_push_run_saves_state(self, tmp_path):
        """push_run persists state to backfill_state.json."""
        store = ProjectStore(tmp_path)
        backend_id = "test-backend"

        runs = [_make_run("baseline_001", 0.5)]
        _seed_runs(store, backend_id, runs)

        mock = MockLangfuseLogger()
        push_run(mock, store, backend_id, "baseline_001")

        state_path = (
            tmp_path / backend_id / "obs" / "langfuse" / "backfill_state.json"
        )
        assert state_path.exists()
        state = json.loads(state_path.read_text(encoding="utf-8"))
        assert "baseline_001" in state["backfilled_run_ids"]
        # langfuse_trace_ids values are lists now
        assert isinstance(state["langfuse_trace_ids"]["baseline_001"], list)


# ---------------------------------------------------------------------------
# Tests — push_all_runs (batch)
# ---------------------------------------------------------------------------


class TestPushAllRunsDatasetFirst:
    def test_registers_dataset_items_with_ground_truth(self, tmp_path, monkeypatch):
        """push_all_runs registers dataset items with expectedOutput set."""
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

        stats = push_all_runs(store, backend_id)

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

    def test_creates_per_query_traces(self, tmp_path, monkeypatch):
        """Trace count = sum of queries across all runs (one trace per query)."""
        store = ProjectStore(tmp_path)
        backend_id = "test-backend"

        # 4 runs, each with 1 query = 4 traces total
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

        stats = push_all_runs(store, backend_id)

        assert stats["total_on_disk"] == 4
        assert stats["new_runs"] == 4
        assert stats["already_done"] == 0

        # 4 rooted traces (one per query, each run has 1 query)
        assert len(mock.traces) == 4
        for t in mock.traces:
            assert t["name"] == "termnorm_pipeline"
            assert "eval" in t["tags"]
            assert "pipeline" in t["tags"]
            assert t["session_id"] == f"dataset_{backend_id}"

        # Each trace gets a hit score
        assert len(mock.scores) == 4
        for s in mock.scores:
            assert s["name"] == "hit"

        # Grid search origin stats
        grid_stats = stats["origins"]["grid_search"]
        assert grid_stats["n_runs"] == 2
        assert grid_stats["best_accuracy"] == 0.8
        assert grid_stats["avg_accuracy"] == pytest.approx(0.7)

    def test_multi_query_run_creates_multiple_traces(self, tmp_path, monkeypatch):
        """A run with N queries creates N traces."""
        store = ProjectStore(tmp_path)
        backend_id = "test-backend"

        items = [
            {"query": "q1", "predicted": "p1", "ground_truth": "p1", "hit": True},
            {"query": "q2", "predicted": "wrong", "ground_truth": "p2", "hit": False},
            {"query": "q3", "predicted": "p3", "ground_truth": "p3", "hit": True},
        ]
        runs = [_make_run("baseline_001", 0.67, items=items)]
        _seed_runs(store, backend_id, runs)

        mock = MockLangfuseLogger()
        monkeypatch.setattr(
            LangfuseLogger, "get_instance", classmethod(lambda cls: mock),
        )

        push_all_runs(store, backend_id)

        # 3 rooted traces (one per query)
        assert len(mock.traces) == 3
        assert len(mock.scores) == 3

    def test_per_query_traces_linked_to_dataset(self, tmp_path, monkeypatch):
        """Each per-query trace is linked to a dataset item."""
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

        push_all_runs(store, backend_id)

        # 2 dataset run links (one per query trace)
        assert len(mock.dataset_run_links) == 2
        for link in mock.dataset_run_links:
            assert link["run_name"] == "baseline_001"
            assert link["dataset_item_id"] is not None
            # Links to trace, not observation
            assert link["trace_id"].startswith("mock_trace_")


class TestPushAllRunsIdempotent:
    def test_second_call_skips_all(self, tmp_path, monkeypatch):
        """After a successful push, running again pushes nothing new."""
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

        # First push
        stats1 = push_all_runs(store, backend_id)
        assert stats1["new_runs"] == 2

        # Reset mock counters
        mock.traces.clear()
        mock.spans.clear()
        mock.scores.clear()
        mock.trace_updates.clear()
        mock.end_trace_calls.clear()

        # Second push — should be a no-op for runs
        stats2 = push_all_runs(store, backend_id)
        assert stats2["new_runs"] == 0
        assert stats2["already_done"] == 2
        assert len(mock.traces) == 0
        assert len(mock.spans) == 0


class TestPushAllRunsIncremental:
    def test_only_new_runs_pushed(self, tmp_path, monkeypatch):
        """Adding runs after first push: only new ones get pushed."""
        store = ProjectStore(tmp_path)
        backend_id = "test-backend"

        runs1 = [_make_run("baseline_001", 0.5)]
        _seed_runs(store, backend_id, runs1)

        mock = MockLangfuseLogger()
        monkeypatch.setattr(
            LangfuseLogger, "get_instance", classmethod(lambda cls: mock),
        )

        stats1 = push_all_runs(store, backend_id)
        assert stats1["new_runs"] == 1

        # Add more runs
        runs2 = [_make_run("grid_001", 0.9), _make_run("grid_002", 0.7)]
        _seed_runs(store, backend_id, runs2)

        mock.traces.clear()
        mock.spans.clear()
        mock.scores.clear()
        mock.trace_updates.clear()
        mock.end_trace_calls.clear()

        stats2 = push_all_runs(store, backend_id)
        assert stats2["new_runs"] == 2
        assert stats2["already_done"] == 1
        assert stats2["total_on_disk"] == 3

        # Only new run traces created (baseline already done)
        assert len(mock.traces) == 2


class TestPushAllRunsDisabledLangfuse:
    def test_returns_error(self, tmp_path, monkeypatch):
        """When Langfuse is disabled, returns error dict."""
        store = ProjectStore(tmp_path)
        backend_id = "test-backend"

        mock = MockLangfuseLogger(enabled=False)
        monkeypatch.setattr(
            LangfuseLogger, "get_instance", classmethod(lambda cls: mock),
        )

        stats = push_all_runs(store, backend_id)
        assert "error" in stats
        assert "disabled" in stats["error"].lower()


class TestStateFileWritten:
    def test_state_persisted_v3(self, tmp_path, monkeypatch):
        """backfill_state.json is written with format_version=3 and per-run trace ID lists."""
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

        push_all_runs(store, backend_id)

        state_path = (
            tmp_path / backend_id / "obs" / "langfuse" / "backfill_state.json"
        )
        assert state_path.exists()

        state = json.loads(state_path.read_text(encoding="utf-8"))
        assert state["format_version"] == 3
        assert set(state["backfilled_run_ids"]) == {"baseline_001", "scan_001"}
        assert state["last_backfill_at"] is not None
        # Per-run trace IDs are lists
        assert isinstance(state["langfuse_trace_ids"]["baseline_001"], list)
        assert isinstance(state["langfuse_trace_ids"]["scan_001"], list)
        assert len(state["langfuse_trace_ids"]["baseline_001"]) >= 1
        # Dataset items tracked
        assert len(state["dataset_items"]) > 0

    def test_old_state_format_reset(self, tmp_path, monkeypatch):
        """Old state format (format_version != 3) gets reset."""
        store = ProjectStore(tmp_path)
        backend_id = "test-backend"

        # Write old-format state (v2)
        state_path = (
            tmp_path / backend_id / "obs" / "langfuse" / "backfill_state.json"
        )
        state_path.parent.mkdir(parents=True, exist_ok=True)
        old_state = {
            "format_version": 2,
            "backfilled_run_ids": ["baseline_001"],
            "last_backfill_at": "2026-01-01T00:00:00Z",
            "langfuse_trace_ids": {"baseline_001": "old_trace_id"},
        }
        state_path.write_text(json.dumps(old_state), encoding="utf-8")

        runs = [_make_run("baseline_001", 0.5)]
        _seed_runs(store, backend_id, runs)

        mock = MockLangfuseLogger()
        monkeypatch.setattr(
            LangfuseLogger, "get_instance", classmethod(lambda cls: mock),
        )

        stats = push_all_runs(store, backend_id)

        # Old state was reset, so baseline_001 gets re-pushed
        assert stats["new_runs"] == 1
        assert len(mock.traces) == 1


# ---------------------------------------------------------------------------
# Tests — pipeline step observations (per-query traces)
# ---------------------------------------------------------------------------


def _make_pipeline_items():
    """Build items with pipeline_data for testing pipeline observations."""
    return [
        {
            "query": "aspirin",
            "predicted": "Aspirin",
            "ground_truth": "Aspirin",
            "hit": True,
            "pipeline_data": {
                "entity_profile": {
                    "core_concept": "pain reliever",
                    "entity_name": "Aspirin",
                },
                "token_matched_candidates": [
                    ("Aspirin", 5), ("Aspirin Tablet", 3),
                ],
                "ranked_candidates": [
                    {"candidate": "Aspirin", "relevance_score": 0.95},
                    {"candidate": "Aspirin Tablet", "relevance_score": 0.70},
                ],
                "step_timings": {
                    "entity_profiling": 1.2,
                    "token_matching": 0.05,
                    "llm_ranking": 0.8,
                },
                "llm_provider": "groq/llama-4",
                "total_time": 2.1,
                "web_search_status": "success",
                "web_search_error": None,
                "web_sources": [
                    {"title": "Aspirin - Wikipedia", "url": "https://example.com/aspirin"},
                ],
                "pipeline_params": {
                    "max_sites": 3,
                    "ranking_temperature": 0.0,
                    "max_token_candidates": 20,
                },
            },
        },
        {
            "query": "ibuprofen",
            "predicted": "Ibuprofen",
            "ground_truth": "Ibuprofen",
            "hit": True,
            "pipeline_data": {
                "entity_profile": {
                    "core_concept": "NSAID",
                    "entity_name": "Ibuprofen",
                },
                "token_matched_candidates": [
                    ("Ibuprofen", 4),
                ],
                "ranked_candidates": [
                    {"candidate": "Ibuprofen", "relevance_score": 0.9},
                ],
                "step_timings": {
                    "entity_profiling": 1.0,
                    "token_matching": 0.04,
                    "llm_ranking": 0.6,
                },
                "llm_provider": "groq/llama-4",
                "total_time": 1.7,
                "web_search_status": "success",
                "web_search_error": None,
                "web_sources": [],
                "pipeline_params": {
                    "max_sites": 3,
                    "ranking_temperature": 0.0,
                },
            },
        },
    ]


class TestPushRunPipelineSteps:
    def test_per_query_pipeline_traces(self, tmp_path):
        """Each query with pipeline_data creates its own rooted trace."""
        store = ProjectStore(tmp_path)
        backend_id = "test-backend"

        items = _make_pipeline_items()
        runs = [_make_run("baseline_001", 1.0, items=items)]
        _seed_runs(store, backend_id, runs)

        mock = MockLangfuseLogger()
        result = push_run(mock, store, backend_id, "baseline_001")

        # 2 rooted traces (one per query)
        assert len(result) == 2
        assert len(mock.traces) == 2

        # Trace metadata set directly on create_trace
        for t in mock.traces:
            assert t["name"] == "termnorm_pipeline"
            assert "pipeline" in t["tags"]
            assert "eval" in t["tags"]
            assert t["metadata"]["run_id"] == "baseline_001"

    def test_pipeline_steps_are_child_spans(self, tmp_path):
        """Pipeline steps are child spans of the rooted trace."""
        store = ProjectStore(tmp_path)
        backend_id = "test-backend"

        items = _make_pipeline_items()
        runs = [_make_run("baseline_001", 1.0, items=items)]
        _seed_runs(store, backend_id, runs)

        mock = MockLangfuseLogger()
        push_run(mock, store, backend_id, "baseline_001")

        # 4 steps per query × 2 queries = 8 total child spans
        web_spans = [s for s in mock.spans if s["name"] == "web_search"]
        entity_spans = [s for s in mock.spans if s["name"] == "entity_profiling"]
        token_spans = [s for s in mock.spans if s["name"] == "token_matching"]
        ranking_spans = [s for s in mock.spans if s["name"] == "llm_ranking"]

        assert len(web_spans) == 2
        assert len(entity_spans) == 2
        assert len(token_spans) == 2
        assert len(ranking_spans) == 2

        # All spans belong to a rooted trace
        all_trace_ids = {t["id"] for t in mock.traces}
        for s in web_spans + entity_spans + token_spans + ranking_spans:
            assert s["trace_id"] in all_trace_ids

    def test_pipeline_step_types(self, tmp_path):
        """Typed as_type per pipeline step (tool/generation/retriever)."""
        store = ProjectStore(tmp_path)
        backend_id = "test-backend"

        items = _make_pipeline_items()
        runs = [_make_run("baseline_001", 1.0, items=items)]
        _seed_runs(store, backend_id, runs)

        mock = MockLangfuseLogger()
        push_run(mock, store, backend_id, "baseline_001")

        web_spans = [s for s in mock.spans if s["name"] == "web_search"]
        entity_spans = [s for s in mock.spans if s["name"] == "entity_profiling"]
        token_spans = [s for s in mock.spans if s["name"] == "token_matching"]
        ranking_spans = [s for s in mock.spans if s["name"] == "llm_ranking"]

        # web_search: tool type
        for s in web_spans:
            assert s["as_type"] == "tool"

        # token_matching: retriever type
        for s in token_spans:
            assert s["as_type"] == "retriever"

        # LLM steps: generation type
        for s in entity_spans:
            assert s["as_type"] == "generation"
        for s in ranking_spans:
            assert s["as_type"] == "generation"

    def test_pipeline_steps_created_via_create_span(self, tmp_path):
        """Pipeline steps are created via create_span() (child spans of root)."""
        store = ProjectStore(tmp_path)
        backend_id = "test-backend"

        items = _make_pipeline_items()
        runs = [_make_run("baseline_001", 1.0, items=items)]
        _seed_runs(store, backend_id, runs)

        mock = MockLangfuseLogger()
        push_run(mock, store, backend_id, "baseline_001")

        # 8 child spans (4 steps × 2 queries), no top-level observations
        assert len(mock.spans) == 8
        assert len(mock.top_level_observations) == 0

        pipeline_names = {s["name"] for s in mock.spans}
        assert pipeline_names == {
            "web_search", "entity_profiling", "token_matching", "llm_ranking",
        }

    def test_pipeline_step_content(self, tmp_path):
        """Pipeline step observations contain correct input/output data."""
        store = ProjectStore(tmp_path)
        backend_id = "test-backend"

        items = _make_pipeline_items()
        runs = [_make_run("baseline_001", 1.0, items=items)]
        _seed_runs(store, backend_id, runs)

        mock = MockLangfuseLogger()
        push_run(mock, store, backend_id, "baseline_001")

        entity_obs = [s for s in mock.spans if s["name"] == "entity_profiling"]
        token_obs = [s for s in mock.spans if s["name"] == "token_matching"]
        ranking_obs = [s for s in mock.spans if s["name"] == "llm_ranking"]
        web_obs = [s for s in mock.spans if s["name"] == "web_search"]

        # Entity profiling output is the full profile dict
        for s in entity_obs:
            assert "core_concept" in s["output"]
            assert "entity_name" in s["output"]

        # Token matching output has full candidates list
        for s in token_obs:
            assert "n_candidates" in s["output"]
            assert s["output"]["n_candidates"] > 0
            assert "candidates" in s["output"]
            assert len(s["output"]["candidates"]) == s["output"]["n_candidates"]

        # LLM ranking output has full ranked candidates list
        for s in ranking_obs:
            assert "n_candidates" in s["output"]
            assert "candidates" in s["output"]
            assert len(s["output"]["candidates"]) > 0
            assert s["output"]["candidates"][0]["candidate"] != ""

        # Web search output has sources
        for s in web_obs:
            assert "status" in s["output"]
            assert "sources" in s["output"]
            assert "n_sources" in s["output"]

    def test_per_query_hit_scores(self, tmp_path):
        """Each per-query trace gets a hit score (1.0 or 0.0)."""
        store = ProjectStore(tmp_path)
        backend_id = "test-backend"

        items = _make_pipeline_items()
        runs = [_make_run("baseline_001", 1.0, items=items)]
        _seed_runs(store, backend_id, runs)

        mock = MockLangfuseLogger()
        push_run(mock, store, backend_id, "baseline_001")

        assert len(mock.scores) == 2
        for s in mock.scores:
            assert s["name"] == "hit"
            assert s["value"] in (0.0, 1.0)

        # Both items are hits
        assert all(s["value"] == 1.0 for s in mock.scores)

    def test_trace_output_contains_prediction(self, tmp_path):
        """Each trace gets enriched output via update_trace."""
        store = ProjectStore(tmp_path)
        backend_id = "test-backend"

        items = _make_pipeline_items()
        runs = [_make_run("baseline_001", 1.0, items=items)]
        _seed_runs(store, backend_id, runs)

        mock = MockLangfuseLogger()
        push_run(mock, store, backend_id, "baseline_001")

        # Trace output set via update_trace
        assert len(mock.trace_updates) == 2
        for tu in mock.trace_updates:
            out = tu["output"]
            # Core prediction fields
            assert "predicted" in out
            assert "ground_truth" in out
            assert "hit" in out
            # Enriched pipeline summary
            assert "entity_profile" in out
            assert "n_token_candidates" in out
            assert "n_ranked_candidates" in out
            assert "web_search_status" in out
            assert "web_sources" in out
            assert "total_time" in out

        # Aspirin trace has entity_profile populated
        aspirin_tu = [
            tu for tu in mock.trace_updates
            if tu["output"]["predicted"] == "Aspirin"
        ][0]
        assert aspirin_tu["output"]["entity_profile"]["core_concept"] == "pain reliever"
        assert aspirin_tu["output"]["n_token_candidates"] == 2
        assert aspirin_tu["output"]["n_ranked_candidates"] == 2

        # Traces ended
        assert len(mock.end_trace_calls) == 2

    def test_trace_metadata_contains_pipeline_params(self, tmp_path):
        """Trace metadata includes pipeline_params when present."""
        store = ProjectStore(tmp_path)
        backend_id = "test-backend"

        items = _make_pipeline_items()
        runs = [_make_run("baseline_001", 1.0, items=items)]
        _seed_runs(store, backend_id, runs)

        mock = MockLangfuseLogger()
        push_run(mock, store, backend_id, "baseline_001")

        # Aspirin item has pipeline_params
        aspirin_trace = [t for t in mock.traces if t["input"]["query"] == "aspirin"][0]
        assert "pipeline_params" in aspirin_trace["metadata"]
        assert aspirin_trace["metadata"]["pipeline_params"]["max_sites"] == 3

    def test_fallback_no_pipeline_data(self, tmp_path):
        """Without pipeline_data, creates rooted trace with no child spans."""
        store = ProjectStore(tmp_path)
        backend_id = "test-backend"

        items = [
            {"query": "q1", "predicted": "p1", "ground_truth": "p1", "hit": True},
            {"query": "q2", "predicted": "wrong", "ground_truth": "p2", "hit": False},
        ]
        runs = [_make_run("baseline_002", 0.5, items=items)]
        _seed_runs(store, backend_id, runs)

        mock = MockLangfuseLogger()
        result = push_run(mock, store, backend_id, "baseline_002")

        assert len(result) == 2
        assert len(mock.traces) == 2

        # No child spans (no pipeline_data)
        assert len(mock.spans) == 0

        # Trace output via update_trace
        assert len(mock.trace_updates) == 2
        assert len(mock.end_trace_calls) == 2

    def test_mixed_items(self, tmp_path):
        """Handles mix of items with and without pipeline_data — all termnorm_pipeline."""
        store = ProjectStore(tmp_path)
        backend_id = "test-backend"

        items = [
            _make_pipeline_items()[0],  # has pipeline_data
            {"query": "q_old", "predicted": "p_old", "ground_truth": "p_old",
             "hit": True},  # no pipeline_data
        ]
        runs = [_make_run("grid_001", 1.0, items=items)]
        _seed_runs(store, backend_id, runs)

        mock = MockLangfuseLogger()
        result = push_run(mock, store, backend_id, "grid_001")

        # 2 rooted traces, both termnorm_pipeline
        assert len(result) == 2
        assert len(mock.traces) == 2
        for t in mock.traces:
            assert t["name"] == "termnorm_pipeline"
            assert "pipeline" in t["tags"]

        # First trace (with pipeline_data) has 4 child spans
        trace_with_steps_id = mock.traces[0]["id"]
        step_spans = [
            s for s in mock.spans
            if s["trace_id"] == trace_with_steps_id
        ]
        assert len(step_spans) == 4  # web_search + entity + token + ranking

        # Second trace (no pipeline_data) has 0 child spans
        trace_no_steps_id = mock.traces[1]["id"]
        fallback_spans = [
            s for s in mock.spans
            if s["trace_id"] == trace_no_steps_id
        ]
        assert len(fallback_spans) == 0
