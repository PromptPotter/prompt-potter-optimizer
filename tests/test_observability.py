"""Tests for M5 observability layer.

Covers:
- ObsLogger file output for all four public methods
- events.jsonl flat navigation log (every method appends)
- events.jsonl → file navigation (trace_id/campaign_id link to real files)
- OBS_ENABLED=False disables all output
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
    """Read all lines from events.jsonl."""
    events_path = obs_root / "langfuse" / "events.jsonl"
    if not events_path.exists():
        return []
    lines = events_path.read_text(encoding="utf-8").strip().splitlines()
    return [json.loads(line) for line in lines]


def _find_traces(obs_root: Path) -> list[Path]:
    """Find all trace JSON files."""
    traces_dir = obs_root / "langfuse" / "traces"
    if not traces_dir.exists():
        return []
    return list(traces_dir.glob("*.json"))


def _find_observations(obs_root: Path, trace_id: str) -> list[Path]:
    """Find all observation JSON files for a trace."""
    obs_dir = obs_root / "langfuse" / "observations" / trace_id
    if not obs_dir.exists():
        return []
    return list(obs_dir.glob("*.json"))


def _find_scores(obs_root: Path, trace_id: str) -> list[dict]:
    """Read all score lines for a trace."""
    scores_path = obs_root / "langfuse" / "scores" / f"{trace_id}.jsonl"
    if not scores_path.exists():
        return []
    lines = scores_path.read_text(encoding="utf-8").strip().splitlines()
    return [json.loads(line) for line in lines]


# ---------------------------------------------------------------------------
# ObsLogger path construction
# ---------------------------------------------------------------------------


def test_obs_path_construction(tmp_path, monkeypatch):
    """Verify obs_root doesn't double the .promptpotter/projects/ prefix.

    Callers pass store.base_dir (already .promptpotter/projects).
    ObsLogger must NOT re-add that prefix.
    """
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
    """log_dataset_run creates trace JSON + events.jsonl line."""
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

    # Trace file created
    assert result is not None
    assert result.exists()
    trace_data = json.loads(result.read_text())
    assert trace_data["name"] == "dataset_run"
    assert trace_data["input"]["run_id"] == "baseline_816203b2"
    assert trace_data["input"]["content_hash"] == "816203b2deadbeef"
    assert trace_data["output"]["accuracy"] == 0.75
    assert trace_data["output"]["hits"] == 30
    assert trace_data["output"]["total"] == 40
    assert "dataset_run" in trace_data["tags"]

    # Score file created
    trace_id = trace_data["id"]
    scores = _find_scores(tmp_path / "obs", trace_id)
    assert len(scores) == 1
    assert scores[0]["name"] == "accuracy"
    assert scores[0]["value"] == 0.75

    # events.jsonl line
    events = _read_events(tmp_path / "obs")
    assert len(events) == 1
    assert events[0]["event"] == "dataset_run"
    assert events[0]["run_id"] == "baseline_816203b2"
    assert events[0]["accuracy"] == 0.75
    assert "timestamp" in events[0]
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

    # Trace file
    assert result is not None
    assert result.exists()
    trace_data = json.loads(result.read_text())
    assert trace_data["name"] == "feedback_cycle"
    assert trace_data["input"]["campaign_id"] == "campaign_test_001"
    assert trace_data["input"]["baseline_accuracy"] == 0.50

    # MLflow experiment meta.yaml
    exp_meta = tmp_path / "obs" / "experiments" / "campaign_test_001" / "meta.yaml"
    assert exp_meta.exists()
    meta_text = exp_meta.read_text()
    assert "campaign_test_001" in meta_text
    assert "active" in meta_text

    # events.jsonl
    events = _read_events(tmp_path / "obs")
    assert len(events) == 1
    assert events[0]["event"] == "campaign_start"
    assert events[0]["campaign_id"] == "campaign_test_001"
    assert events[0]["baseline_accuracy"] == 0.50

    # Campaign trace registered for linking
    assert "campaign_test_001" in obs._campaign_traces


# ---------------------------------------------------------------------------
# ObsLogger.log_round
# ---------------------------------------------------------------------------


def test_obs_logger_round(tmp_path):
    """log_round creates observation + score + MLflow run + events.jsonl."""
    obs = _make_obs(tmp_path)

    # Start campaign first (to register trace_id)
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

    # Observation directory created
    assert result is not None
    assert result.exists()

    # Langfuse observation files
    trace_id = obs._campaign_traces["campaign_round_test"]
    obs_files = _find_observations(tmp_path / "obs", trace_id)
    assert len(obs_files) == 1
    obs_data = json.loads(obs_files[0].read_text())
    assert obs_data["type"] == "span"
    assert obs_data["name"] == "round_0"
    assert obs_data["output"]["accuracy"] == 0.65
    assert obs_data["output"]["improved"] is True

    # Langfuse score (campaign trace already has baseline_accuracy from start)
    scores = _find_scores(tmp_path / "obs", trace_id)
    # baseline_accuracy from campaign_start + accuracy from round
    assert len(scores) == 2
    round_score = [s for s in scores if s["name"] == "accuracy"]
    assert len(round_score) == 1
    assert round_score[0]["value"] == 0.65

    # MLflow run directory
    exp_dir = tmp_path / "obs" / "experiments" / "campaign_round_test"
    run_dirs = [d for d in exp_dir.iterdir() if d.is_dir() and (d / "meta.yaml").exists()]
    assert len(run_dirs) == 1

    run_dir = run_dirs[0]
    # params
    assert (run_dir / "params" / "model").read_text() == "llama-4-maverick"
    assert (run_dir / "params" / "temperature").read_text() == "0.0"
    assert (run_dir / "params" / "n_variants").read_text() == "3"
    assert (run_dir / "params" / "round").read_text() == "0"

    # metrics
    accuracy_content = (run_dir / "metrics" / "accuracy").read_text().strip()
    parts = accuracy_content.split()
    assert len(parts) == 3
    assert float(parts[1]) == 0.65

    # tags
    assert (run_dir / "tags" / "improved").read_text() == "true"
    assert (run_dir / "tags" / "next_action").read_text() == "generate"

    # events.jsonl (campaign_start + round_complete)
    events = _read_events(tmp_path / "obs")
    assert len(events) == 2
    round_event = events[1]
    assert round_event["event"] == "round_complete"
    assert round_event["round"] == 0
    assert round_event["accuracy"] == 0.65
    assert round_event["improved"] is True


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
            "thinking_style": "step by step",
        },
        parent_id="parent_00112233",
    )

    assert result is not None
    assert result.exists()

    # prompt.txt
    prompt_text = result.read_text()
    assert "You are a ranking expert." in prompt_text
    assert "Rank candidates by relevance" in prompt_text

    # metadata.json
    meta_path = result.parent / "metadata.json"
    assert meta_path.exists()
    meta = json.loads(meta_path.read_text())
    assert meta["family"] == "ranking_prompt"
    assert meta["version"] == "abc12345"  # first 8 chars
    assert meta["prompt_state_id"] == "abc12345def67890"
    assert meta["parent_id"] == "parent_00112233"
    assert meta["layer1_fields"]["persona"] == "ranking expert"
    assert "created_at" in meta

    # Directory structure
    prompt_dir = tmp_path / "obs" / "prompts" / "ranking_prompt" / "abc12345"
    assert prompt_dir.exists()

    # events.jsonl
    events = _read_events(tmp_path / "obs")
    assert len(events) == 1
    assert events[0]["event"] == "prompt_version"
    assert events[0]["prompt_state_id"] == "abc12345def67890"
    assert events[0]["family"] == "ranking_prompt"
    assert events[0]["parent_id"] == "parent_00112233"


# ---------------------------------------------------------------------------
# events.jsonl navigation test
# ---------------------------------------------------------------------------


def test_events_jsonl_navigation(tmp_path):
    """trace_id and campaign_id in events.jsonl point to real files on disk."""
    obs = _make_obs(tmp_path)

    # Create a dataset run and a campaign with one round
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
            # trace_id should point to a real trace file
            trace_path = obs_root / "langfuse" / "traces" / f"{trace_id}.json"
            assert trace_path.exists(), f"Trace file missing for {trace_id}"

        campaign_id = event.get("campaign_id")
        if campaign_id:
            # campaign_id should point to a real experiment dir
            exp_path = obs_root / "experiments" / campaign_id / "meta.yaml"
            assert exp_path.exists(), f"Experiment missing for {campaign_id}"


# ---------------------------------------------------------------------------
# ObsLogger.log_campaign_end
# ---------------------------------------------------------------------------


def test_log_campaign_end(tmp_path):
    """log_campaign_end updates trace output, writes best_accuracy score, and events."""
    obs = _make_obs(tmp_path)

    # Start campaign first
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

    # Trace output updated
    trace_id = obs._campaign_traces["end_test_campaign"]
    trace_path = tmp_path / "obs" / "langfuse" / "traces" / f"{trace_id}.json"
    trace_data = json.loads(trace_path.read_text())
    assert trace_data["output"]["best_accuracy"] == 0.85
    assert trace_data["output"]["n_rounds"] == 3
    assert trace_data["output"]["stop_reason"] == "patience_exhausted"

    # best_accuracy score written
    scores = _find_scores(tmp_path / "obs", trace_id)
    best_scores = [s for s in scores if s["name"] == "best_accuracy"]
    assert len(best_scores) == 1
    assert best_scores[0]["value"] == 0.85

    # campaign_end event in events.jsonl
    events = _read_events(tmp_path / "obs")
    end_events = [e for e in events if e["event"] == "campaign_end"]
    assert len(end_events) == 1
    assert end_events[0]["best_accuracy"] == 0.85
    assert end_events[0]["stop_reason"] == "patience_exhausted"
    assert end_events[0]["best_round"] == 1


# ---------------------------------------------------------------------------
# ObsLogger.flush
# ---------------------------------------------------------------------------


def test_flush_without_langfuse(tmp_path):
    """flush() is a no-op when _cloud is None."""
    obs = _make_obs(tmp_path)
    # Should not raise
    obs.flush()


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

        # Dataset API tracking
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

    def create_span(self, trace_id, name, input, output, metadata=None):
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

    # Dataset API

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
            "trace_id": trace_id,
            "run_name": run_name,
        })
        return True


def test_dual_write_with_mock_langfuse(tmp_path):
    """Injecting a mock Langfuse causes cloud calls alongside file writes."""
    mock_lf = _MockLangfuse()
    obs = _make_obs(tmp_path)
    obs._cloud = CloudObsBackend(mock_lf)

    # dataset_run outside campaign → standalone cloud trace + score
    obs.log_dataset_run(
        run_id="dr1", content_hash="hash1", accuracy=0.80,
        total=10, hits=8, model="m", temperature=0.0,
    )
    assert len(mock_lf.traces) == 1
    assert mock_lf.traces[0]["name"] == "dataset_run"
    assert len(mock_lf.scores) == 1
    assert mock_lf.scores[0]["name"] == "accuracy"

    # campaign_start → cloud trace (campaign)
    obs.log_campaign_start(
        campaign_id="dual_test",
        config={"max_rounds": 3},
        baseline_accuracy=0.50,
        session_id="sess_123",
    )
    assert len(mock_lf.traces) == 2
    assert mock_lf.traces[1]["name"] == "feedback_cycle"
    assert mock_lf.traces[1]["session_id"] == "sess_123"
    assert "dual_test" in obs._cloud._trace_ids

    # dataset_run during campaign → nested span (not new trace)
    obs.log_dataset_run(
        run_id="dr_during", content_hash="hash2", accuracy=0.60,
        total=10, hits=6, model="m", temperature=0.0,
    )
    # No new trace created (still 2)
    assert len(mock_lf.traces) == 2
    # But a span was created under the campaign trace
    eval_spans = [s for s in mock_lf.spans if s["name"].startswith("eval_")]
    assert len(eval_spans) == 1
    assert eval_spans[0]["trace_id"] == obs._cloud._trace_ids["dual_test"]

    # round → cloud span + score
    obs.log_round(
        campaign_id="dual_test", round_num=0, accuracy=0.65,
        hits=6, total=10, improved=True, next_action="generate",
        winner_prompt_state_id="w1",
        candidate_scores=[{"label": "c0", "accuracy": 0.65}],
    )
    round_spans = [s for s in mock_lf.spans if s["name"] == "round_0"]
    assert len(round_spans) == 1
    round_scores = [s for s in mock_lf.scores if s["name"] == "accuracy_round_0"]
    assert len(round_scores) == 1

    # campaign_end → cloud score + update_trace + end_trace
    obs.log_campaign_end(
        campaign_id="dual_test",
        best_accuracy=0.65,
        n_rounds=1,
        stop_reason="max_rounds",
        best_round=0,
    )
    best_scores = [s for s in mock_lf.scores if s["name"] == "best_accuracy"]
    assert len(best_scores) == 1
    assert len(mock_lf.trace_updates) == 1
    assert len(mock_lf.end_trace_calls) == 1

    # flush
    obs.flush()
    assert mock_lf.flush_count == 1

    # get_cloud_trace_id
    assert obs.get_cloud_trace_id("dual_test") is not None
    assert obs.get_cloud_trace_id("nonexistent") is None


# ---------------------------------------------------------------------------
# OBS_ENABLED = False
# ---------------------------------------------------------------------------


def test_obs_disabled(tmp_path):
    """All methods are no-ops when OBS_ENABLED is False."""
    obs = _make_obs(tmp_path, enabled=False)

    # Call all methods
    r1 = obs.log_dataset_run(
        run_id="test", content_hash="hash", accuracy=0.5,
        total=10, hits=5, model="m", temperature=0.0,
    )
    r2 = obs.log_campaign_start(
        campaign_id="c1", config={}, baseline_accuracy=0.5,
    )
    r3 = obs.log_round(
        campaign_id="c1", round_num=0, accuracy=0.6,
        hits=6, total=10, improved=True, next_action="generate",
        winner_prompt_state_id="w1", candidate_scores=[],
    )
    r4 = obs.log_prompt_version(
        prompt_state_id="ps1", rendered_prompt="text",
        layer1_fields={},
    )

    # All return None
    assert r1 is None
    assert r2 is None
    assert r3 is None
    assert r4 is None

    # No files created
    obs_dir = tmp_path / "obs"
    if obs_dir.exists():
        all_files = list(obs_dir.rglob("*"))
        assert len(all_files) == 0, f"Files created when disabled: {all_files}"


# ---------------------------------------------------------------------------
# Dataset run standalone vs campaign nesting (behavioral)
# ---------------------------------------------------------------------------


def test_dataset_run_standalone_creates_trace(tmp_path):
    """Standalone dataset_run (no campaign) creates a cloud trace, not a span."""
    mock_lf = _MockLangfuse()
    obs = _make_obs(tmp_path)
    obs._cloud = CloudObsBackend(mock_lf)

    obs.log_dataset_run(
        run_id="standalone", content_hash="abc", accuracy=0.70,
        total=10, hits=7, model="m", temperature=0.0,
    )

    assert len(mock_lf.traces) == 1
    assert mock_lf.traces[0]["name"] == "dataset_run"
    assert len(mock_lf.spans) == 0
    assert len(mock_lf.scores) == 1
    assert mock_lf.scores[0]["name"] == "accuracy"


def test_active_trace_cleared_after_campaign_end(tmp_path):
    """After campaign_end, dataset_run creates a standalone trace (not a span)."""
    mock_lf = _MockLangfuse()
    obs = _make_obs(tmp_path)
    obs._cloud = CloudObsBackend(mock_lf)

    # Start campaign → cloud backend has active trace
    obs.log_campaign_start(
        campaign_id="cls_test",
        config={},
        baseline_accuracy=0.50,
        session_id="sess",
    )
    assert obs._cloud._active_trace_id is not None
    assert obs._cloud._active_session_id == "sess"

    # End campaign → active trace cleared
    obs.log_campaign_end(
        campaign_id="cls_test",
        best_accuracy=0.60,
        n_rounds=1,
        stop_reason="max_rounds",
        best_round=0,
    )
    assert obs._cloud._active_trace_id is None
    assert obs._cloud._active_session_id is None

    # Dataset run after campaign end → standalone trace (not span)
    obs.log_dataset_run(
        run_id="after_end", content_hash="def", accuracy=0.80,
        total=10, hits=8, model="m", temperature=0.0,
    )
    assert len(mock_lf.traces) == 2  # campaign trace + standalone trace
    assert mock_lf.traces[1]["name"] == "dataset_run"


# ---------------------------------------------------------------------------
# ObsLogger.register_dataset
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
    assert "aspirin" in item_map
    assert "ibuprofen" in item_map

    # Files exist on disk
    ds_dir = tmp_path / "obs" / "langfuse" / "datasets" / "termnorm_gt"
    assert ds_dir.exists()
    items = list(ds_dir.glob("*.json"))
    assert len(items) == 2

    # Item content correct
    item_data = json.loads(items[0].read_text())
    assert "input" in item_data
    assert "expected_output" in item_data
    assert item_data["expected_output"] in ("Aspirin", "Ibuprofen")

    # events.jsonl line
    events = _read_events(tmp_path / "obs")
    assert len(events) == 1
    assert events[0]["event"] == "dataset_registered"
    assert events[0]["n_items"] == 2


def test_register_dataset_disabled(tmp_path):
    """register_dataset returns empty dict when disabled."""
    obs = _make_obs(tmp_path, enabled=False)
    result = obs.register_dataset("ds", [{"query": "q", "ground_truth": "g"}])
    assert result == {}


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

    # Cloud dataset created
    assert len(mock_lf.datasets_created) == 1
    assert mock_lf.datasets_created[0]["name"] == "termnorm_gt"

    # Cloud items created with ground truth
    assert len(mock_lf.dataset_items_created) == 2
    for it in mock_lf.dataset_items_created:
        assert it["expected_output"] is not None

    # item_map uses cloud IDs (overwritten from file IDs)
    assert len(item_map) == 2
    for query, item_id in item_map.items():
        assert item_id.startswith("cloud_item_")


# ---------------------------------------------------------------------------
# ObsLogger.log_dataset_run with dataset linking
# ---------------------------------------------------------------------------


def test_dataset_run_with_linking(tmp_path):
    """log_dataset_run links to dataset items when dataset_name + item_map provided."""
    mock_lf = _MockLangfuse()
    obs = _make_obs(tmp_path)
    obs._cloud = CloudObsBackend(mock_lf)

    item_map = {"aspirin": "item_001", "ibuprofen": "item_002"}

    obs.log_dataset_run(
        run_id="baseline_abc",
        content_hash="hash123",
        accuracy=0.75,
        total=2,
        hits=1,
        model="test-model",
        temperature=0.0,
        prompt_state_id="ps_abc",
        dataset_name="termnorm_gt",
        dataset_item_map=item_map,
    )

    # Standalone trace created (no active campaign)
    assert len(mock_lf.traces) == 1

    # Dataset links created
    assert len(mock_lf.dataset_run_links) == 2
    linked_items = {lnk["dataset_item_id"] for lnk in mock_lf.dataset_run_links}
    assert linked_items == {"item_001", "item_002"}
    for link in mock_lf.dataset_run_links:
        assert link["run_name"] == "baseline_abc"


def test_dataset_run_no_linking_without_map(tmp_path):
    """log_dataset_run creates no links when dataset_item_map not provided."""
    mock_lf = _MockLangfuse()
    obs = _make_obs(tmp_path)
    obs._cloud = CloudObsBackend(mock_lf)

    obs.log_dataset_run(
        run_id="baseline_abc",
        content_hash="hash123",
        accuracy=0.75,
        total=2,
        hits=1,
        model="test-model",
        temperature=0.0,
    )

    # Trace created but no links
    assert len(mock_lf.traces) == 1
    assert len(mock_lf.dataset_run_links) == 0
