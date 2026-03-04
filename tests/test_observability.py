"""Tests for M5 observability layer.

Covers ObsLogger file output, events.jsonl navigation,
dual-write with cloud Langfuse, and dataset registration.
"""

import json
from pathlib import Path

from api.services.obs.observability_logger import CloudObsBackend, ObsLogger


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_obs(tmp_path: Path, enabled: bool = True) -> ObsLogger:
    """Construct an ObsLogger without triggering settings import."""
    obs = ObsLogger.__new__(ObsLogger)
    obs.obs_root = tmp_path / "obs"
    obs._enabled = enabled
    obs._campaign_traces = {}
    obs._cloud = None
    return obs


def _read_events(obs_root: Path) -> list[dict]:
    events_path = obs_root / "langfuse" / "events.jsonl"
    if not events_path.exists():
        return []
    lines = events_path.read_text(encoding="utf-8").strip().splitlines()
    return [json.loads(line) for line in lines]


def _find_observations(obs_root: Path, trace_id: str) -> list[Path]:
    obs_dir = obs_root / "langfuse" / "observations" / trace_id
    if not obs_dir.exists():
        return []
    return list(obs_dir.glob("*.json"))


def _find_scores(obs_root: Path, trace_id: str) -> list[dict]:
    scores_path = obs_root / "langfuse" / "scores" / f"{trace_id}.jsonl"
    if not scores_path.exists():
        return []
    lines = scores_path.read_text(encoding="utf-8").strip().splitlines()
    return [json.loads(line) for line in lines]


# ---------------------------------------------------------------------------
# ObsLogger path construction
# ---------------------------------------------------------------------------


def test_obs_path_construction(tmp_path, monkeypatch):
    """obs_root doesn't double the .promptpotter/projects/ prefix."""
    from api.config import settings as _settings_mod

    monkeypatch.setattr(_settings_mod.settings, "OBS_ENABLED", True)

    store_base = tmp_path / ".promptpotter" / "projects"
    store_base.mkdir(parents=True)
    obs = ObsLogger(store_base, "my-backend")

    assert ".promptpotter/projects/.promptpotter" not in str(obs.obs_root)
    assert obs.obs_root == store_base / "my-backend" / "obs"


# ---------------------------------------------------------------------------
# ObsLogger.log_dataset_run
# ---------------------------------------------------------------------------


def test_obs_logger_dataset_run(tmp_path):
    """log_dataset_run creates trace JSON + score + events.jsonl line."""
    obs = _make_obs(tmp_path)

    result = obs.log_dataset_run(
        run_id="baseline_816203b2",
        content_hash="816203b2deadbeef",
        accuracy=0.75,
        total=40,
        hits=30,
        model="llama-4-maverick",
        temperature=0.0,
        prompt_state_id="ps_abc123",
    )

    assert result is not None
    assert result.exists()
    trace_data = json.loads(result.read_text())
    assert trace_data["name"] == "dataset_run"
    assert trace_data["input"]["run_id"] == "baseline_816203b2"
    assert trace_data["output"]["accuracy"] == 0.75
    assert "dataset_run" in trace_data["tags"]

    trace_id = trace_data["id"]
    scores = _find_scores(tmp_path / "obs", trace_id)
    assert len(scores) == 1
    assert scores[0]["name"] == "accuracy"
    assert scores[0]["value"] == 0.75

    events = _read_events(tmp_path / "obs")
    assert len(events) == 1
    assert events[0]["event"] == "dataset_run"
    assert events[0]["trace_id"] == trace_id


# ---------------------------------------------------------------------------
# ObsLogger.log_campaign_start
# ---------------------------------------------------------------------------


def test_obs_logger_campaign(tmp_path):
    """log_campaign_start creates experiment meta.yaml + trace + events.jsonl."""
    obs = _make_obs(tmp_path)

    result = obs.log_campaign_start(
        campaign_id="campaign_test_001",
        config={"max_rounds": 10, "patience": 3},
        baseline_accuracy=0.50,
    )

    assert result is not None
    trace_data = json.loads(result.read_text())
    assert trace_data["name"] == "feedback_cycle"
    assert trace_data["input"]["campaign_id"] == "campaign_test_001"

    exp_meta = tmp_path / "obs" / "experiments" / "campaign_test_001" / "meta.yaml"
    assert exp_meta.exists()

    events = _read_events(tmp_path / "obs")
    assert len(events) == 1
    assert events[0]["event"] == "campaign_start"
    assert "campaign_test_001" in obs._campaign_traces


# ---------------------------------------------------------------------------
# ObsLogger.log_round
# ---------------------------------------------------------------------------


def test_obs_logger_round(tmp_path):
    """log_round creates observation + score + MLflow run + events.jsonl."""
    obs = _make_obs(tmp_path)
    obs.log_campaign_start(
        campaign_id="campaign_round_test",
        config={"max_rounds": 5},
        baseline_accuracy=0.50,
    )

    result = obs.log_round(
        campaign_id="campaign_round_test",
        round_num=0,
        accuracy=0.65,
        hits=26,
        total=40,
        improved=True,
        next_action="generate",
        winner_prompt_state_id="ps_winner_001",
        candidate_scores=[
            {"label": "candidate_0", "accuracy": 0.65},
            {"label": "candidate_1", "accuracy": 0.55},
        ],
        model="llama-4-maverick",
        temperature=0.0,
        n_variants=3,
    )

    assert result is not None

    trace_id = obs._campaign_traces["campaign_round_test"]
    obs_files = _find_observations(tmp_path / "obs", trace_id)
    assert len(obs_files) == 2

    round_obs = [
        json.loads(f.read_text()) for f in obs_files
        if json.loads(f.read_text())["name"] == "round_0"
    ]
    assert len(round_obs) == 1
    assert round_obs[0]["output"]["accuracy"] == 0.65

    scores = _find_scores(tmp_path / "obs", trace_id)
    assert len(scores) == 2

    exp_dir = tmp_path / "obs" / "experiments" / "campaign_round_test"
    run_dirs = [d for d in exp_dir.iterdir() if d.is_dir() and (d / "meta.yaml").exists()]
    assert len(run_dirs) == 1
    assert (run_dirs[0] / "params" / "model").read_text() == "llama-4-maverick"

    events = _read_events(tmp_path / "obs")
    assert len(events) == 2
    assert events[1]["event"] == "round_complete"
    assert events[1]["accuracy"] == 0.65


# ---------------------------------------------------------------------------
# ObsLogger.log_prompt_version
# ---------------------------------------------------------------------------


def test_obs_logger_prompt_version(tmp_path):
    """log_prompt_version writes prompt.txt + metadata.json + events.jsonl."""
    obs = _make_obs(tmp_path)

    result = obs.log_prompt_version(
        prompt_state_id="abc12345def67890",
        rendered_prompt="You are a ranking expert.\n\nRank candidates by relevance.",
        layer1_fields={
            "persona": "ranking expert",
            "instruction": "Rank candidates by relevance",
        },
        parent_id="parent_00112233",
    )

    assert result is not None
    assert "You are a ranking expert." in result.read_text()

    meta = json.loads((result.parent / "metadata.json").read_text())
    assert meta["prompt_state_id"] == "abc12345def67890"
    assert meta["parent_id"] == "parent_00112233"

    events = _read_events(tmp_path / "obs")
    assert events[0]["event"] == "prompt_version"


# ---------------------------------------------------------------------------
# events.jsonl navigation test
# ---------------------------------------------------------------------------


def test_events_jsonl_navigation(tmp_path):
    """trace_id and campaign_id in events.jsonl point to real files on disk."""
    obs = _make_obs(tmp_path)

    obs.log_dataset_run(
        run_id="eval_abc", content_hash="abcdef01", accuracy=0.80,
        total=10, hits=8, model="test-model", temperature=0.0,
    )
    obs.log_campaign_start(
        campaign_id="nav_test_campaign",
        config={"max_rounds": 3},
        baseline_accuracy=0.60,
    )
    obs.log_round(
        campaign_id="nav_test_campaign", round_num=0,
        accuracy=0.70, hits=7, total=10, improved=True,
        next_action="generate", winner_prompt_state_id="winner_001",
        candidate_scores=[],
    )

    events = _read_events(tmp_path / "obs")
    assert len(events) == 3

    obs_root = tmp_path / "obs"
    for event in events:
        trace_id = event.get("trace_id")
        if trace_id:
            trace_path = obs_root / "langfuse" / "traces" / f"{trace_id}.json"
            assert trace_path.exists()

        campaign_id = event.get("campaign_id")
        if campaign_id:
            exp_path = obs_root / "experiments" / campaign_id / "meta.yaml"
            assert exp_path.exists()


# ---------------------------------------------------------------------------
# ObsLogger.log_campaign_end
# ---------------------------------------------------------------------------


def test_log_campaign_end(tmp_path):
    """log_campaign_end updates trace output, writes best_accuracy score."""
    obs = _make_obs(tmp_path)
    obs.log_campaign_start(
        campaign_id="end_test_campaign",
        config={"max_rounds": 5},
        baseline_accuracy=0.50,
    )

    obs.log_campaign_end(
        campaign_id="end_test_campaign",
        best_accuracy=0.85,
        n_rounds=3,
        stop_reason="patience_exhausted",
        best_round=1,
    )

    trace_id = obs._campaign_traces["end_test_campaign"]
    trace_data = json.loads(
        (tmp_path / "obs" / "langfuse" / "traces" / f"{trace_id}.json").read_text()
    )
    assert trace_data["output"]["best_accuracy"] == 0.85
    assert trace_data["output"]["stop_reason"] == "patience_exhausted"

    scores = _find_scores(tmp_path / "obs", trace_id)
    best_scores = [s for s in scores if s["name"] == "best_accuracy"]
    assert len(best_scores) == 1
    assert best_scores[0]["value"] == 0.85


# ---------------------------------------------------------------------------
# Dual-write with mock Langfuse
# ---------------------------------------------------------------------------


class _MockLangfuse:
    """Minimal mock to verify cloud calls from ObsLogger."""

    def __init__(self):
        self.enabled = True
        self.traces: list[dict] = []
        self.spans: list[dict] = []
        self.scores: list[dict] = []
        self.trace_updates: list[dict] = []
        self.end_trace_calls: list[str] = []
        self.flush_count = 0
        self._counter = 0
        self.datasets_created: list[dict] = []
        self.dataset_items_created: list[dict] = []
        self.dataset_run_links: list[dict] = []
        self._item_counter = 0

    def create_trace(self, name, input, metadata=None, user_id=None,
                     session_id=None, tags=None):
        self._counter += 1
        tid = f"cloud_trace_{self._counter}"
        self.traces.append({"name": name, "input": input, "session_id": session_id,
                            "tags": tags})
        return tid

    def start_span(self, trace_id, name, input=None, metadata=None,
                   *, parent_observation_id=None, as_type="span"):
        self._counter += 1
        obs_id = f"open_obs_{self._counter}"
        self.spans.append({"trace_id": trace_id, "name": name,
                           "input": input, "output": None,
                           "obs_id": obs_id, "open": True})
        return obs_id

    def end_observation(self, obs_id, output=None, metadata=None):
        for span in self.spans:
            if span.get("obs_id") == obs_id and span.get("open"):
                span["output"] = output
                span["open"] = False
                break

    def create_span(self, trace_id, name, input, output, metadata=None,
                    *, parent_observation_id=None, as_type="span"):
        self.spans.append({"trace_id": trace_id, "name": name,
                           "input": input, "output": output})
        return f"span_{name}"

    def create_score(self, trace_id, name, value, data_type="NUMERIC",
                     comment=None):
        self.scores.append({"trace_id": trace_id, "name": name,
                            "value": value, "comment": comment})
        return True

    def update_trace(self, trace_id, output=None, metadata=None):
        self.trace_updates.append({"trace_id": trace_id, "output": output,
                                   "metadata": metadata})
        return True

    def end_trace(self, trace_id):
        self.end_trace_calls.append(trace_id)

    def flush(self):
        self.flush_count += 1

    def create_dataset(self, name, description=None, metadata=None):
        self.datasets_created.append({"name": name, "description": description})
        return True

    def create_dataset_item(self, dataset_name, input, expected_output=None,
                            metadata=None):
        self._item_counter += 1
        item_id = f"cloud_item_{self._item_counter}"
        self.dataset_items_created.append({
            "id": item_id, "dataset_name": dataset_name,
            "input": input, "expected_output": expected_output,
        })
        return item_id

    def link_item_to_run(self, dataset_item_id, trace_id,
                         observation_id=None, run_name="", run_metadata=None):
        self.dataset_run_links.append({
            "dataset_item_id": dataset_item_id,
            "trace_id": trace_id, "run_name": run_name,
        })
        return True


def test_dual_write_full_lifecycle(tmp_path):
    """Cloud calls alongside file writes for campaign lifecycle."""
    mock_lf = _MockLangfuse()
    obs = _make_obs(tmp_path)
    obs._cloud = CloudObsBackend(mock_lf)

    # dataset_run is file-only (no cloud trace)
    obs.log_dataset_run(
        run_id="dr1", content_hash="hash1", accuracy=0.80,
        total=10, hits=8, model="m", temperature=0.0,
    )
    assert len(mock_lf.traces) == 0

    # campaign_start -> cloud trace
    obs.log_campaign_start(
        campaign_id="dual_test", config={"max_rounds": 3},
        baseline_accuracy=0.50, session_id="sess_123",
    )
    assert len(mock_lf.traces) == 1
    assert mock_lf.traces[0]["name"] == "feedback_cycle"

    # round -> cloud span + score
    obs.log_round(
        campaign_id="dual_test", round_num=0, accuracy=0.65,
        hits=6, total=10, improved=True, next_action="generate",
        winner_prompt_state_id="w1",
        candidate_scores=[{"label": "c0", "accuracy": 0.65}],
    )
    assert len([s for s in mock_lf.spans if s["name"] == "round_0"]) == 1

    # campaign_end -> score + update_trace + end_trace
    obs.log_campaign_end(
        campaign_id="dual_test", best_accuracy=0.65,
        n_rounds=1, stop_reason="max_rounds", best_round=0,
    )
    assert len([s for s in mock_lf.scores if s["name"] == "best_accuracy"]) == 1
    assert len(mock_lf.trace_updates) == 1
    assert len(mock_lf.end_trace_calls) == 1

    # Active trace cleared after campaign_end
    assert obs._cloud._active_trace_id is None

    # flush + get_cloud_trace_id
    obs.flush()
    assert mock_lf.flush_count == 1
    assert obs.get_cloud_trace_id("dual_test") is not None
    assert obs.get_cloud_trace_id("nonexistent") is None


# ---------------------------------------------------------------------------
# Dataset registration
# ---------------------------------------------------------------------------


def test_register_dataset_creates_files(tmp_path):
    """register_dataset writes dataset item JSON files to disk."""
    obs = _make_obs(tmp_path)

    eval_data = [
        {"query": "aspirin", "ground_truth": "Aspirin"},
        {"query": "ibuprofen", "ground_truth": "Ibuprofen"},
    ]

    item_map = obs.register_dataset("termnorm_gt", eval_data)

    assert len(item_map) == 2
    ds_dir = tmp_path / "obs" / "langfuse" / "datasets" / "termnorm_gt"
    assert len(list(ds_dir.glob("*.json"))) == 2

    events = _read_events(tmp_path / "obs")
    assert events[0]["event"] == "dataset_registered"
    assert events[0]["n_items"] == 2


def test_register_dataset_cloud_dual_write(tmp_path):
    """register_dataset pushes items to cloud Langfuse."""
    mock_lf = _MockLangfuse()
    obs = _make_obs(tmp_path)
    obs._cloud = CloudObsBackend(mock_lf)

    eval_data = [
        {"query": "aspirin", "ground_truth": "Aspirin"},
        {"query": "ibuprofen", "ground_truth": "Ibuprofen"},
    ]

    item_map = obs.register_dataset("termnorm_gt", eval_data)

    assert len(mock_lf.datasets_created) == 1
    assert len(mock_lf.dataset_items_created) == 2
    assert all(v.startswith("cloud_item_") for v in item_map.values())
