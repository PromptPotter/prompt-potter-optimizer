"""Tests for Langfuse cloud push of dataset runs.

Verifies run origin classification, dataset-first structure, per-query pipeline
traces, idempotency, incremental push, state persistence, and cloud push gating.
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

from _helpers import (
    MockLangfuseLogger, make_dataset_run,
    apply_eval_mock, apply_grow_mock, apply_llm_mock,
)


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
    return make_dataset_run(run_id, accuracy=accuracy, items=items)


def _seed_runs(store: ProjectStore, backend_id: str, runs: list[dict]):
    """Save runs to the store so they appear in list_all / load_by_id."""
    for run in runs:
        store.dataset_runs.save(backend_id, run["run_id"], run)


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


# ---------------------------------------------------------------------------
# classify_run_origin
# ---------------------------------------------------------------------------


class TestClassifyRunOrigin:
    @pytest.mark.parametrize("run_id,source,expected", [
        ("unknown_run_xyz", "", "other"),
        ("", "", "other"),
        ("grid_00230b37", "baseline", "baseline"),
        ("grid_00230b37", "grid_search", "grid_search"),
        ("any_id", "sensitivity_scan", "sensitivity_scan"),
        ("any_id", "feedback_cycle", "feedback_cycle"),
    ])
    def test_classify(self, run_id, source, expected):
        assert classify_run_origin(run_id, source=source) == expected


# ---------------------------------------------------------------------------
# push_run (single-run)
# ---------------------------------------------------------------------------


def test_push_run_basic_and_idempotent(tmp_path):
    """push_run returns per-query trace IDs, second call returns None."""
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
    """push_run links traces to dataset items when query_to_item_id given."""
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


# ---------------------------------------------------------------------------
# push_all_runs (batch)
# ---------------------------------------------------------------------------


def test_push_all_dataset_registration(tmp_path, monkeypatch):
    """push_all_runs registers dataset items and links per-query traces."""
    store = ProjectStore(tmp_path)
    backend_id = "test-backend"

    items = [
        {"query": "aspirin", "predicted": "Aspirin",
         "ground_truth": "Aspirin", "hit": True},
        {"query": "ibuprofen", "predicted": "wrong",
         "ground_truth": "Ibuprofen", "hit": False},
    ]
    _seed_runs(store, backend_id, [_make_run("baseline_001", 0.5, items=items)])

    mock = MockLangfuseLogger()
    monkeypatch.setattr(LangfuseLogger, "get_instance", classmethod(lambda cls: mock))

    push_all_runs(store, backend_id)

    # Dataset created with items
    assert len(mock.datasets_created) == 1
    assert mock.datasets_created[0]["name"] == DATASET_NAME
    assert len(mock.dataset_items_created) == 2
    queries = {it["input"]["query"] for it in mock.dataset_items_created}
    assert queries == {"aspirin", "ibuprofen"}

    # Per-query traces created and linked
    assert len(mock.traces) == 2
    assert len(mock.dataset_run_links) == 2
    for link in mock.dataset_run_links:
        assert link["trace_id"].startswith("mock_trace_")


def test_push_all_multi_query_run(tmp_path, monkeypatch):
    """A run with N queries creates N traces."""
    store = ProjectStore(tmp_path)
    backend_id = "test-backend"

    items = [
        {"query": "q1", "predicted": "p1", "ground_truth": "p1", "hit": True},
        {"query": "q2", "predicted": "wrong", "ground_truth": "p2", "hit": False},
        {"query": "q3", "predicted": "p3", "ground_truth": "p3", "hit": True},
    ]
    _seed_runs(store, backend_id, [_make_run("baseline_001", 0.67, items=items)])

    mock = MockLangfuseLogger()
    monkeypatch.setattr(LangfuseLogger, "get_instance", classmethod(lambda cls: mock))
    push_all_runs(store, backend_id)

    assert len(mock.traces) == 3
    assert len(mock.scores) == 3


def test_push_all_idempotent_and_incremental(tmp_path, monkeypatch):
    """Second call skips done runs; new runs get pushed incrementally."""
    store = ProjectStore(tmp_path)
    backend_id = "test-backend"

    _seed_runs(store, backend_id, [_make_run("baseline_001", 0.5)])

    mock = MockLangfuseLogger()
    monkeypatch.setattr(LangfuseLogger, "get_instance", classmethod(lambda cls: mock))

    stats1 = push_all_runs(store, backend_id)
    assert stats1["new_runs"] == 1

    # Add more runs
    _seed_runs(store, backend_id, [_make_run("grid_001", 0.9)])

    mock.traces.clear()
    mock.spans.clear()
    mock.scores.clear()
    mock.trace_updates.clear()
    mock.end_trace_calls.clear()

    stats2 = push_all_runs(store, backend_id)
    assert stats2["new_runs"] == 1
    assert stats2["already_done"] == 1
    assert len(mock.traces) == 1  # only the new run


# ---------------------------------------------------------------------------
# State file
# ---------------------------------------------------------------------------


def test_state_persisted(tmp_path, monkeypatch):
    """backfill_state.json with per-run trace ID lists."""
    store = ProjectStore(tmp_path)
    backend_id = "test-backend"

    _seed_runs(store, backend_id, [
        _make_run("baseline_001", 0.5),
        _make_run("scan_001", 0.7),
    ])

    mock = MockLangfuseLogger()
    monkeypatch.setattr(LangfuseLogger, "get_instance", classmethod(lambda cls: mock))
    push_all_runs(store, backend_id)

    state_path = tmp_path / backend_id / "obs" / "langfuse" / "backfill_state.json"
    assert state_path.exists()

    state = json.loads(state_path.read_text(encoding="utf-8"))
    assert set(state["backfilled_run_ids"]) == {"baseline_001", "scan_001"}
    assert isinstance(state["langfuse_trace_ids"]["baseline_001"], list)
    assert len(state["dataset_items"]) > 0


# ---------------------------------------------------------------------------
# Pipeline step observations
# ---------------------------------------------------------------------------


def test_pipeline_step_structure_and_types(tmp_path):
    """Pipeline steps: child spans with correct names, types, and parent traces."""
    store = ProjectStore(tmp_path)
    backend_id = "test-backend"

    _seed_runs(store, backend_id, [_make_run("baseline_001", 1.0, _make_pipeline_items())])

    mock = MockLangfuseLogger()
    result = push_run(mock, store, backend_id, "baseline_001")

    # 2 rooted traces (one per query)
    assert len(result) == 2
    assert len(mock.traces) == 2

    # 4 steps per query × 2 queries = 8 child spans
    assert len(mock.spans) == 8
    span_names = {s["name"] for s in mock.spans}
    assert span_names == {"web_search", "entity_profiling", "token_matching", "llm_ranking"}

    # All spans belong to a rooted trace
    trace_ids = {t["id"] for t in mock.traces}
    for s in mock.spans:
        assert s["trace_id"] in trace_ids

    # Correct as_type per step
    for s in mock.spans:
        if s["name"] == "web_search":
            assert s["as_type"] == "tool"
        elif s["name"] == "token_matching":
            assert s["as_type"] == "retriever"
        else:
            assert s["as_type"] == "generation"


def test_pipeline_step_content_and_scores(tmp_path):
    """Pipeline step content, trace output, hit scores, and metadata."""
    store = ProjectStore(tmp_path)
    backend_id = "test-backend"

    _seed_runs(store, backend_id, [_make_run("baseline_001", 1.0, _make_pipeline_items())])

    mock = MockLangfuseLogger()
    push_run(mock, store, backend_id, "baseline_001")

    # Step content
    entity_obs = [s for s in mock.spans if s["name"] == "entity_profiling"]
    for s in entity_obs:
        assert "core_concept" in s["output"]

    ranking_obs = [s for s in mock.spans if s["name"] == "llm_ranking"]
    for s in ranking_obs:
        assert "candidates" in s["output"]
        assert len(s["output"]["candidates"]) > 0

    # Hit scores
    assert len(mock.scores) == 2
    assert all(s["name"] == "hit" and s["value"] == 1.0 for s in mock.scores)

    # Trace output
    assert len(mock.trace_updates) == 2
    for tu in mock.trace_updates:
        assert "predicted" in tu["output"]
        assert "ground_truth" in tu["output"]

    # Pipeline params in metadata
    aspirin_trace = [t for t in mock.traces if t["input"]["query"] == "aspirin"][0]
    assert aspirin_trace["metadata"]["pipeline_params"]["max_sites"] == 3


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


# ---------------------------------------------------------------------------
# Local obs finalization
# ---------------------------------------------------------------------------


def test_finalize_obs_with_explicit_obs(tmp_path):
    """_finalize_observability logs to the provided ObsLogger without error."""
    from api.services.prompt_eval import _finalize_observability
    from api.services.obs.observability_logger import ObsLogger

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


# ---------------------------------------------------------------------------
# Feedback-cycle Langfuse integration (merged from test_langfuse_integration)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_full_langfuse_integration(monkeypatch, eval_data, tmp_path):
    """Full feedback cycle: campaign trace, per-round spans/scores, final output."""
    from api.services.campaign.models import CycleConfig
    from api.services.campaign.feedback_cycle import run_feedback_cycle
    from api.models.prompt_state import PromptState
    from api.config import settings as _settings_mod

    apply_llm_mock(monkeypatch)
    apply_grow_mock(monkeypatch)
    apply_eval_mock(monkeypatch, round_hits=[1, 1, 1])

    store_base = tmp_path / ".promptpotter" / "projects"
    store_base.mkdir(parents=True)
    config = CycleConfig(
        max_rounds=3, patience=2, n_variants=2,
        backend_url="http://mock:8000",
        enable_critique=False, enable_l2=False,
        project_root=str(store_base), backend_id="test-backend",
    )

    monkeypatch.setattr(_settings_mod.settings, "OBS_ENABLED", True)
    mock = MockLangfuseLogger()
    monkeypatch.setattr(
        LangfuseLogger, "get_instance", classmethod(lambda cls: mock),
    )

    result = await run_feedback_cycle(
        instruction="Test.", eval_data=eval_data, config=config,
        langfuse_session_id="test_session_123",
        baseline_prompt_state=PromptState(instruction="Test.").model_dump(),
        baseline_accuracy=0.0, baseline_results=[],
    )

    assert result.langfuse_trace_id is not None
    assert result.langfuse_trace_id.startswith("mock_trace_")
    campaign_traces = [t for t in mock.traces if t["name"] == "feedback_cycle"]
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
