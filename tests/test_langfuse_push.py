"""Tests for Langfuse cloud push of dataset runs.

Verifies run origin classification, dataset-first structure, per-query pipeline
traces, idempotency, incremental push, state persistence, and cloud push gating.
"""

import json
import time

import pytest
from _helpers import (
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


class MockLangfuseLogger:
    """Records all Langfuse calls for test verification.

    Covers both feedback-cycle integration tests (traces, spans, scores,
    generations) and backfill tests (dataset API, enabled flag, rate limiting).
    """

    def __init__(self, *, enabled=True):
        self.enabled = enabled
        self.traces: list[dict] = []
        self.trace_ids: list[str] = []
        self.spans: list[dict] = []
        self.scores: list[dict] = []
        self.generations: list[dict] = []
        self.trace_updates: list[dict] = []
        self.end_trace_calls: list[str] = []
        self.flush_count = 0
        self._counter = 0
        self._rate_limit_until = 0.0
        self.top_level_observations: list[dict] = []
        self.datasets_created: list[dict] = []
        self.dataset_items_created: list[dict] = []
        self.dataset_items_updated: list[dict] = []
        self.dataset_gets: list[str] = []
        self.dataset_run_links: list[dict] = []
        self._item_counter = 0

    @property
    def rate_limited(self) -> bool:
        return time.time() < self._rate_limit_until

    def create_trace_id(self):
        if not self.enabled:
            return None
        self._counter += 1
        tid = f"mock_trace_{self._counter:03d}"
        self.trace_ids.append(tid)
        return tid

    def create_trace(self, name, input, metadata=None, user_id=None,
                     session_id=None, tags=None):
        if not self.enabled:
            return None
        self._counter += 1
        tid = f"mock_trace_{self._counter:03d}"
        self.traces.append({
            "id": tid, "name": name, "input": input, "metadata": metadata,
            "session_id": session_id, "tags": tags,
        })
        return tid

    def start_span(self, trace_id, name, input=None, metadata=None,
                   *, parent_observation_id=None, as_type="span"):
        self._counter += 1
        obs_id = f"open_obs_{self._counter:03d}"
        self.spans.append({
            "trace_id": trace_id, "name": name,
            "input": input, "output": None, "metadata": metadata,
            "obs_id": obs_id, "open": True,
            "as_type": as_type, "parent_observation_id": parent_observation_id,
        })
        return obs_id

    def end_observation(self, obs_id, output=None, metadata=None):
        for span in self.spans:
            if span.get("obs_id") == obs_id and span.get("open"):
                span["output"] = output
                if metadata:
                    span["metadata"] = metadata
                span["open"] = False
                break

    def create_span(self, trace_id, name, input, output, metadata=None,
                    *, parent_observation_id=None, as_type="span",
                    model=None, usage_details=None):
        self.spans.append({
            "trace_id": trace_id, "name": name,
            "input": input, "output": output, "metadata": metadata,
            "as_type": as_type, "parent_observation_id": parent_observation_id,
            "model": model, "usage_details": usage_details,
        })
        return f"span_{name}"

    def create_top_level_observation(
        self, trace_id, name, as_type, input, output,
        metadata=None, model=None, usage_details=None,
        *, trace_params=None,
    ):
        record = {
            "trace_id": trace_id, "name": name, "as_type": as_type,
            "input": input, "output": output, "metadata": metadata,
            "model": model, "usage_details": usage_details,
            "top_level": True, "trace_params": trace_params,
        }
        self.top_level_observations.append(record)
        return f"tl_{name}"

    def create_generation(self, trace_id, name, model, input, output,
                          usage=None, metadata=None):
        self.generations.append({
            "trace_id": trace_id, "name": name, "model": model,
        })
        return f"gen_{name}"

    def create_score(self, trace_id, name, value, data_type="NUMERIC",
                     comment=None):
        self.scores.append({
            "trace_id": trace_id, "name": name,
            "value": value, "comment": comment,
        })
        return True

    def update_trace(self, trace_id, output=None, metadata=None):
        self.trace_updates.append({
            "trace_id": trace_id, "output": output, "metadata": metadata,
        })
        return True

    def end_trace(self, trace_id):
        self.end_trace_calls.append(trace_id)

    def flush(self):
        self.flush_count += 1

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
    from api.services.prompt_eval import _log_eval_to_obs

    store = ProjectStore(tmp_path)
    backend_id = "test-backend"

    obs = ObsLogger.__new__(ObsLogger)
    obs.obs_root = store.base_dir / backend_id / "obs"
    obs._enabled = True
    obs._campaign_traces = {}
    obs._cloud = None

    _log_eval_to_obs(
        store, backend_id, "baseline_aabb", "aabb1122",
        {"accuracy": 1.0, "total": 1, "hits": 1},
        "ps-1",
        obs=obs,
    )


@pytest.mark.asyncio
async def test_full_langfuse_integration(monkeypatch, eval_data, tmp_path):
    from api.config import settings as _settings_mod
    from api.models.opt_search_point import OptSearchPoint
    from api.services.campaign.config import CycleConfig
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
