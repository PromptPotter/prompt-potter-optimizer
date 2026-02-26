"""Tests for M5 observability layer.

Covers:
- ObsLogger file output for all four public methods
- events.jsonl flat navigation log (every method appends)
- events.jsonl → file navigation (trace_id/campaign_id link to real files)
- OBS_ENABLED=False disables all output
- LLM retry logic (503, 429, 400, exhaustion)
"""

import json
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from api.services.observability_logger import ObsLogger


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_obs(tmp_path: Path, enabled: bool = True) -> ObsLogger:
    """Construct an ObsLogger without triggering settings import."""
    obs = ObsLogger.__new__(ObsLogger)
    obs.obs_root = tmp_path / "obs"
    obs._enabled = enabled
    obs._campaign_traces = {}
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
# LLM Retry Tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_llm_retry_503():
    """Retry on 503 twice, then succeed on third attempt."""
    from api.services.llm_client import _OpenAICompatibleClient

    client = _OpenAICompatibleClient(
        api_key="test-key",
        base_url="http://localhost:1234",
        default_model="test-model",
        provider_name="test",
    )

    call_count = [0]

    class MockCompletion:
        class Choice:
            class Message:
                content = '{"result": "ok"}'
            message = Message()
            finish_reason = "stop"

        choices = [Choice()]

        class Usage:
            prompt_tokens = 10
            completion_tokens = 5
            total_tokens = 15
        usage = Usage()
        model = "test-model"

    class Mock503Error(Exception):
        status_code = 503

    async def mock_create(**kwargs):
        call_count[0] += 1
        if call_count[0] <= 2:
            raise Mock503Error("Service unavailable")
        return MockCompletion()

    # Patch the client
    mock_async_client = AsyncMock()
    mock_async_client.chat.completions.create = mock_create
    client._client = mock_async_client

    # Should succeed after 2 retries
    response = await client.chat(
        messages=[{"role": "user", "content": "test"}],
        model="test-model",
    )

    assert response.content == '{"result": "ok"}'
    assert call_count[0] == 3  # 2 failures + 1 success


@pytest.mark.asyncio
async def test_llm_retry_429():
    """Retry on 429 once, then succeed."""
    from api.services.llm_client import _OpenAICompatibleClient

    client = _OpenAICompatibleClient(
        api_key="test-key",
        base_url="http://localhost:1234",
        default_model="test-model",
        provider_name="test",
    )

    call_count = [0]

    class MockCompletion:
        class Choice:
            class Message:
                content = "success"
            message = Message()
            finish_reason = "stop"

        choices = [Choice()]

        class Usage:
            prompt_tokens = 10
            completion_tokens = 5
            total_tokens = 15
        usage = Usage()
        model = "test-model"

    class Mock429Error(Exception):
        status_code = 429

    async def mock_create(**kwargs):
        call_count[0] += 1
        if call_count[0] <= 1:
            raise Mock429Error("Rate limited")
        return MockCompletion()

    mock_async_client = AsyncMock()
    mock_async_client.chat.completions.create = mock_create
    client._client = mock_async_client

    response = await client.chat(
        messages=[{"role": "user", "content": "test"}],
        model="test-model",
    )

    assert response.content == "success"
    assert call_count[0] == 2


@pytest.mark.asyncio
async def test_llm_no_retry_400():
    """400 errors raise immediately with no retry."""
    from api.services.llm_client import _OpenAICompatibleClient

    client = _OpenAICompatibleClient(
        api_key="test-key",
        base_url="http://localhost:1234",
        default_model="test-model",
        provider_name="test",
    )

    call_count = [0]

    class Mock400Error(Exception):
        status_code = 400

    async def mock_create(**kwargs):
        call_count[0] += 1
        raise Mock400Error("Bad request")

    mock_async_client = AsyncMock()
    mock_async_client.chat.completions.create = mock_create
    client._client = mock_async_client

    with pytest.raises(Mock400Error):
        await client.chat(
            messages=[{"role": "user", "content": "test"}],
            model="test-model",
        )

    assert call_count[0] == 1  # No retry


@pytest.mark.asyncio
async def test_llm_retry_exhausted():
    """503 on every attempt raises after max retries."""
    from api.services.llm_client import _MAX_APP_RETRIES, _OpenAICompatibleClient

    client = _OpenAICompatibleClient(
        api_key="test-key",
        base_url="http://localhost:1234",
        default_model="test-model",
        provider_name="test",
    )

    call_count = [0]

    class Mock503Error(Exception):
        status_code = 503

    async def mock_create(**kwargs):
        call_count[0] += 1
        raise Mock503Error("Service unavailable")

    mock_async_client = AsyncMock()
    mock_async_client.chat.completions.create = mock_create
    client._client = mock_async_client

    with pytest.raises(Mock503Error):
        await client.chat(
            messages=[{"role": "user", "content": "test"}],
            model="test-model",
        )

    assert call_count[0] == _MAX_APP_RETRIES + 1
