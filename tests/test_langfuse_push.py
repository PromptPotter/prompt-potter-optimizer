"""Tests for Langfuse cloud push of dataset runs.

Verifies run origin classification, dataset-first structure, per-query pipeline
traces, idempotency, incremental push, state persistence, and cloud push gating.
"""

import json

import pytest
from _helpers import (
    MockLangfuseLogger,
    apply_eval_mock,
    apply_grow_mock,
    apply_llm_mock,
    make_dataset_run,
)

from api.services.obs.langfuse_client import LangfuseLogger
from api.services.obs.langfuse_push import (
    DATASET_NAME,
    classify_run_origin,
    push_all_runs,
    push_run,
)
from api.services.project_store import ProjectStore


def _make_run(run_id: str, accuracy: float, items: list[dict] | None = None):
    if items is None:
        items = [
            {"query": "aspirin", "predicted": "Aspirin",
             "ground_truth": "Aspirin", "hit": True},
        ]
    return make_dataset_run(run_id, accuracy=accuracy, items=items)


def _seed_runs(store: ProjectStore, backend_id: str, runs: list[dict]):
    for run in runs:
        store.dataset_runs.save(backend_id, run["run_id"], run)


class TestClassifyRunOrigin:
    @pytest.mark.parametrize("run_id,source,expected", [
        ("unknown_run_xyz", "", "other"),
        ("", "", "other"),
        ("run_00230b37", "baseline", "baseline"),
        ("any_id", "sensitivity_scan", "sensitivity_scan"),
        ("any_id", "optimization_loop", "optimization_loop"),
    ])
    def test_classify(self, run_id, source, expected):
        assert classify_run_origin(run_id, source=source) == expected


def test_push_run_basic_and_idempotent(tmp_path):
    store = ProjectStore(tmp_path)
    backend_id = "test-backend"

    items = [
        {"query": "q1", "predicted": "p1", "ground_truth": "p1", "hit": True},
        {"query": "q2", "predicted": "wrong", "ground_truth": "p2", "hit": False},
    ]
    _seed_runs(store, backend_id, [_make_run("baseline_001", 0.5, items=items)])

    mock = MockLangfuseLogger()
    result = push_run(mock, store, backend_id, "baseline_001")

    assert isinstance(result, list)
    assert len(result) == 2
    assert all(tid.startswith("mock_trace_") for tid in result)
    assert len(mock.traces) == 2

    # Idempotent — second call returns None
    result_2 = push_run(mock, store, backend_id, "baseline_001")
    assert result_2 is None
    assert len(mock.traces) == 2  # no new traces


def test_push_run_with_dataset_linking(tmp_path):
    store = ProjectStore(tmp_path)
    backend_id = "test-backend"

    items = [{"query": "q1", "predicted": "p1", "ground_truth": "p1", "hit": True}]
    _seed_runs(store, backend_id, [_make_run("baseline_001", 1.0, items=items)])

    mock = MockLangfuseLogger()
    push_run(mock, store, backend_id, "baseline_001",
             query_to_item_id={"q1": "item_abc"})

    assert len(mock.dataset_run_links) == 1
    link = mock.dataset_run_links[0]
    assert link["dataset_item_id"] == "item_abc"
    assert link["run_name"] == "baseline_001"
    assert link["trace_id"].startswith("mock_trace_")


def test_fallback_no_pipeline_data(tmp_path):
    """Without pipeline_data, creates rooted traces with no child spans."""
    store = ProjectStore(tmp_path)
    backend_id = "test-backend"

    items = [
        {"query": "q1", "predicted": "p1", "ground_truth": "p1", "hit": True},
        {"query": "q2", "predicted": "wrong", "ground_truth": "p2", "hit": False},
    ]
    _seed_runs(store, backend_id, [_make_run("baseline_002", 0.5, items=items)])

    mock = MockLangfuseLogger()
    result = push_run(mock, store, backend_id, "baseline_002")

    assert len(result) == 2
    assert len(mock.traces) == 2
    assert len(mock.spans) == 0  # no pipeline step spans


class TestPushAllRuns:
    """Tests for push_all_runs (batch push)."""

    BACKEND_ID = "test-backend"

    @pytest.fixture(autouse=True)
    def _setup(self, tmp_path, monkeypatch):
        self.store = ProjectStore(tmp_path)
        self.mock = MockLangfuseLogger()
        monkeypatch.setattr(
            LangfuseLogger, "get_instance", classmethod(lambda cls: self.mock),
        )

    def test_dataset_registration(self):
        items = [
            {"query": "aspirin", "predicted": "Aspirin",
             "ground_truth": "Aspirin", "hit": True},
            {"query": "ibuprofen", "predicted": "wrong",
             "ground_truth": "Ibuprofen", "hit": False},
        ]
        _seed_runs(self.store, self.BACKEND_ID,
                    [_make_run("baseline_001", 0.5, items=items)])

        push_all_runs(self.store, self.BACKEND_ID)

        # Dataset created with items
        assert len(self.mock.datasets_created) == 1
        assert self.mock.datasets_created[0]["name"] == DATASET_NAME
        assert len(self.mock.dataset_items_created) == 2
        queries = {it["input"]["query"] for it in self.mock.dataset_items_created}
        assert queries == {"aspirin", "ibuprofen"}

        # Per-query traces created and linked
        assert len(self.mock.traces) == 2
        assert len(self.mock.dataset_run_links) == 2
        for link in self.mock.dataset_run_links:
            assert link["trace_id"].startswith("mock_trace_")

    def test_multi_query_run(self):
        items = [
            {"query": "q1", "predicted": "p1", "ground_truth": "p1", "hit": True},
            {"query": "q2", "predicted": "wrong", "ground_truth": "p2", "hit": False},
            {"query": "q3", "predicted": "p3", "ground_truth": "p3", "hit": True},
        ]
        _seed_runs(self.store, self.BACKEND_ID,
                    [_make_run("baseline_001", 0.67, items=items)])

        push_all_runs(self.store, self.BACKEND_ID)

        assert len(self.mock.traces) == 3
        assert len(self.mock.scores) == 3

    def test_idempotent_and_incremental(self):
        _seed_runs(self.store, self.BACKEND_ID, [_make_run("baseline_001", 0.5)])

        stats1 = push_all_runs(self.store, self.BACKEND_ID)
        assert stats1["new_runs"] == 1

        # Add more runs
        _seed_runs(self.store, self.BACKEND_ID, [_make_run("run_001", 0.9)])

        self.mock.traces.clear()
        self.mock.spans.clear()
        self.mock.scores.clear()
        self.mock.trace_updates.clear()
        self.mock.end_trace_calls.clear()

        stats2 = push_all_runs(self.store, self.BACKEND_ID)
        assert stats2["new_runs"] == 1
        assert stats2["already_done"] == 1
        assert len(self.mock.traces) == 1  # only the new run

    def test_state_persisted(self):
        _seed_runs(self.store, self.BACKEND_ID, [
            _make_run("baseline_001", 0.5),
            _make_run("scan_001", 0.7),
        ])

        push_all_runs(self.store, self.BACKEND_ID)

        state_path = (self.store.base_dir / self.BACKEND_ID
                      / "obs" / "langfuse" / "backfill_state.json")
        assert state_path.exists()

        state = json.loads(state_path.read_text(encoding="utf-8"))
        assert set(state["backfilled_run_ids"]) == {"baseline_001", "scan_001"}
        assert isinstance(state["langfuse_trace_ids"]["baseline_001"], list)
        assert len(state["dataset_items"]) > 0


def test_finalize_obs_with_explicit_obs(tmp_path):
    from api.services.obs.observability_logger import ObsLogger
    from api.services.prompt_eval import _finalize_observability

    store = ProjectStore(tmp_path)
    backend_id = "test-backend"

    obs = ObsLogger.__new__(ObsLogger)
    obs.obs_root = store.base_dir / backend_id / "obs"
    obs._enabled = True
    obs._campaign_traces = {}
    obs._cloud = None

    _finalize_observability(
        store, backend_id, "baseline_aabb", "aabb1122",
        {"accuracy": 1.0, "total": 1, "hits": 1},
        "model", 0.0, "ps-1",
        obs=obs,
    )


@pytest.mark.asyncio
async def test_full_langfuse_integration(monkeypatch, eval_data, tmp_path):
    from api.config import settings as _settings_mod
    from api.models.opt_search_point import OptSearchPoint
    from api.services.campaign.models import CycleConfig
    from api.services.campaign.optimization_loop import run_optimization

    apply_llm_mock(monkeypatch)
    apply_grow_mock(monkeypatch)
    apply_eval_mock(monkeypatch, round_hits=[1, 1, 1])

    store_base = tmp_path / ".promptpotter" / "projects"
    store_base.mkdir(parents=True)
    config = CycleConfig(
        max_rounds=3, l1_patience=2, n_variants=2,
        backend_url="http://mock:8000",
        enable_critique=False, enable_l2=False,
        project_root=str(store_base), backend_id="test-backend",
    )

    monkeypatch.setattr(_settings_mod.settings, "OBS_ENABLED", True)
    mock = MockLangfuseLogger()
    monkeypatch.setattr(
        LangfuseLogger, "get_instance", classmethod(lambda cls: mock),
    )

    result = await run_optimization(
        instruction="Test.", eval_data=eval_data, config=config,
        langfuse_session_id="test_session_123",
        baseline_prompt_fields=OptSearchPoint(instruction="Test.").model_dump(),
        baseline_accuracy=0.0, baseline_results=[],
    )

    assert result.langfuse_trace_id is not None
    assert result.langfuse_trace_id.startswith("mock_trace_")
    campaign_traces = [t for t in mock.traces if t["name"] == "optimization_loop"]
    assert len(campaign_traces) == 1
    assert campaign_traces[0]["session_id"] == "test_session_123"
    assert result.n_rounds == 3
    round_spans = [s for s in mock.spans if s["name"].startswith("round_")]
    assert len(round_spans) == 3
    best_scores = [s for s in mock.scores if s["name"] == "best_accuracy"]
    assert len(best_scores) == 1
    assert best_scores[0]["value"] == result.best_accuracy
    assert len(mock.trace_updates) == 1
    assert mock.flush_count >= 1
    assert len(mock.end_trace_calls) == 1
