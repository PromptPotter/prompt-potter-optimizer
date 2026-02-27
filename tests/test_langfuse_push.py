"""Tests for Langfuse cloud push of dataset runs.

Verifies:
- Run origin classification by prefix
- Dataset-first structure: items registered with ground truth
- Per-run traces with per-query spans linked to dataset items
- Idempotency (second call skips all)
- Incremental push (only new runs pushed)
- Graceful handling when Langfuse is disabled
- State file persistence (format_version=2)
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
# Tests — push_run (single-run)
# ---------------------------------------------------------------------------


class TestPushRun:
    def test_push_run_idempotent(self, tmp_path):
        """push_run creates trace first time, returns None on second call."""
        store = ProjectStore(tmp_path)
        backend_id = "test-backend"

        items = [
            {"query": "q1", "predicted": "p1", "ground_truth": "p1", "hit": True},
            {"query": "q2", "predicted": "wrong", "ground_truth": "p2", "hit": False},
        ]
        runs = [_make_run("baseline_001", 0.5, items=items)]
        _seed_runs(store, backend_id, runs)

        mock = MockLangfuseLogger()

        # First push — creates trace
        trace_id = push_run(mock, store, backend_id, "baseline_001")
        assert trace_id is not None
        assert trace_id.startswith("mock_trace_")
        assert len(mock.traces) == 1

        # Second push — idempotent, returns None
        trace_id_2 = push_run(mock, store, backend_id, "baseline_001")
        assert trace_id_2 is None
        assert len(mock.traces) == 1  # no new trace

    def test_push_run_format(self, tmp_path):
        """push_run creates trace named eval_{run_id} with per-query spans."""
        store = ProjectStore(tmp_path)
        backend_id = "test-backend"

        items = [
            {"query": "q1", "predicted": "p1", "ground_truth": "p1", "hit": True},
            {"query": "q2", "predicted": "wrong", "ground_truth": "p2", "hit": False},
        ]
        runs = [_make_run("grid_001", 0.5, items=items)]
        _seed_runs(store, backend_id, runs)

        mock = MockLangfuseLogger()
        assert push_run(mock, store, backend_id, "grid_001") is not None

        # Trace format
        assert len(mock.traces) == 1
        trace = mock.traces[0]
        assert trace["name"] == "eval_grid_001"
        assert "eval" in trace["tags"]
        assert "grid_search" in trace["tags"]
        assert trace["session_id"] == f"dataset_{backend_id}"

        # Per-query spans (filter out score_accuracy observation)
        query_spans = [s for s in mock.spans if s["name"] != "score_accuracy"]
        assert len(query_spans) == 2
        span_queries = {s["input"]["query"] for s in query_spans}
        assert span_queries == {"q1", "q2"}
        for s in query_spans:
            assert "predicted" in s["output"]
            assert "hit" in s["output"]

        # score_accuracy observation present
        score_obs = [s for s in mock.spans if s["name"] == "score_accuracy"]
        assert len(score_obs) == 1
        assert score_obs[0]["output"]["accuracy"] == 0.5

        # Fallback: no pipeline_data → as_type="tool"
        for s in query_spans:
            assert s.get("as_type") == "tool"

        # Score + trace output + end_trace
        assert len(mock.scores) == 1
        assert mock.scores[0]["name"] == "accuracy"
        assert len(mock.trace_updates) == 1
        assert mock.trace_updates[0]["output"]["accuracy"] == 0.5
        assert len(mock.end_trace_calls) == 1

    def test_push_run_with_dataset_linking(self, tmp_path):
        """push_run links spans to dataset items when query_to_item_id given."""
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
        assert mock.dataset_run_links[0]["dataset_item_id"] == "item_abc"
        assert mock.dataset_run_links[0]["run_name"] == "baseline_001"

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
        assert "baseline_001" in state["langfuse_trace_ids"]


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

        stats = push_all_runs(store, backend_id)

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

        push_all_runs(store, backend_id)

        # Filter to per-query spans (exclude score_accuracy)
        query_spans = [s for s in mock.spans if s["name"] != "score_accuracy"]
        assert len(query_spans) == 2

        # Spans have query/predicted/hit structure
        span_inputs = {s["input"]["query"] for s in query_spans}
        assert span_inputs == {"q1", "q2"}
        for s in query_spans:
            assert "predicted" in s["output"]
            assert "hit" in s["output"]

        # 2 dataset run links (one per query)
        assert len(mock.dataset_run_links) == 2
        for link in mock.dataset_run_links:
            assert link["run_name"] == "baseline_001"
            assert link["dataset_item_id"] is not None


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

        stats2 = push_all_runs(store, backend_id)
        assert stats2["new_runs"] == 2
        assert stats2["already_done"] == 1
        assert stats2["total_on_disk"] == 3

        # Only new run traces created (baseline already done)
        assert len(mock.traces) == 2
        trace_names = {t["name"] for t in mock.traces}
        assert "eval_grid_001" in trace_names
        assert "eval_grid_002" in trace_names


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

        push_all_runs(store, backend_id)

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

        stats = push_all_runs(store, backend_id)

        # Old state was reset, so baseline_001 gets re-pushed
        assert stats["new_runs"] == 1
        assert len(mock.traces) == 1


# ---------------------------------------------------------------------------
# Tests — pipeline step observations
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
            },
        },
    ]


class TestPushRunPipelineSteps:
    def test_push_run_pipeline_steps(self, tmp_path):
        """push_run creates pipeline step observations when pipeline_data present."""
        store = ProjectStore(tmp_path)
        backend_id = "test-backend"

        items = _make_pipeline_items()
        runs = [_make_run("baseline_001", 1.0, items=items)]
        _seed_runs(store, backend_id, runs)

        mock = MockLangfuseLogger()
        trace_id = push_run(mock, store, backend_id, "baseline_001")
        assert trace_id is not None

        # Per-query chain observations (opened via start_span)
        chains = [s for s in mock.spans if s.get("as_type") == "chain"]
        assert len(chains) == 2
        for c in chains:
            assert c.get("open") is False  # closed via end_observation
            assert "predicted" in c["output"]

        # Pipeline step observations per query (3 steps × 2 queries = 6)
        entity_obs = [s for s in mock.spans if s["name"] == "entity_profiling"]
        token_obs = [s for s in mock.spans if s["name"] == "token_matching"]
        ranking_obs = [s for s in mock.spans if s["name"] == "llm_ranking"]

        assert len(entity_obs) == 2
        assert len(token_obs) == 2
        assert len(ranking_obs) == 2

        # Correct as_type for each step
        for s in entity_obs:
            assert s["as_type"] == "tool"
        for s in token_obs:
            assert s["as_type"] == "tool"
        for s in ranking_obs:
            assert s["as_type"] == "generation"

        # Entity profiling output contains profile data
        for s in entity_obs:
            assert "core_concept" in s["output"]

        # Token matching output contains candidate count
        for s in token_obs:
            assert "n_candidates" in s["output"]
            assert s["output"]["n_candidates"] > 0

        # LLM ranking output has top candidate
        for s in ranking_obs:
            assert "top_candidate" in s["output"]
            assert s["output"]["top_candidate"] != ""

        # Pipeline steps are parented to the chain
        chain_ids = {c["obs_id"] for c in chains}
        for s in entity_obs + token_obs + ranking_obs:
            assert s.get("parent_observation_id") in chain_ids

        # score_accuracy observation present
        score_obs = [s for s in mock.spans if s["name"] == "score_accuracy"]
        assert len(score_obs) == 1
        assert score_obs[0]["as_type"] == "tool"

    def test_push_run_fallback_no_pipeline_data(self, tmp_path):
        """push_run falls back to single tool per query when no pipeline_data."""
        store = ProjectStore(tmp_path)
        backend_id = "test-backend"

        items = [
            {"query": "q1", "predicted": "p1", "ground_truth": "p1", "hit": True},
            {"query": "q2", "predicted": "wrong", "ground_truth": "p2", "hit": False},
        ]
        runs = [_make_run("baseline_002", 0.5, items=items)]
        _seed_runs(store, backend_id, runs)

        mock = MockLangfuseLogger()
        trace_id = push_run(mock, store, backend_id, "baseline_002")
        assert trace_id is not None

        # Per-query spans use as_type="tool" (fallback)
        query_spans = [s for s in mock.spans if s["name"] != "score_accuracy"]
        assert len(query_spans) == 2
        for s in query_spans:
            assert s["as_type"] == "tool"
            assert "predicted" in s["output"]

        # No pipeline step observations
        pipeline_steps = [
            s for s in mock.spans
            if s["name"] in ("entity_profiling", "token_matching", "llm_ranking")
        ]
        assert len(pipeline_steps) == 0

        # score_accuracy still present
        score_obs = [s for s in mock.spans if s["name"] == "score_accuracy"]
        assert len(score_obs) == 1

    def test_push_run_mixed_items(self, tmp_path):
        """push_run handles mix of items with and without pipeline_data."""
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
        push_run(mock, store, backend_id, "grid_001")

        # 1 chain (pipeline) + 3 pipeline steps + 1 tool (fallback) + 1 score
        chains = [s for s in mock.spans if s.get("as_type") == "chain"]
        tools = [
            s for s in mock.spans
            if s.get("as_type") == "tool" and s["name"] == "q_old"
        ]
        assert len(chains) == 1  # aspirin query
        assert len(tools) == 1   # old query fallback
