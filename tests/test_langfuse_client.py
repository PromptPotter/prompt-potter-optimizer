"""Unit tests for LangfuseLogger SDK wrapper.

Verifies that LangfuseLogger methods delegate to the Langfuse SDK correctly:
- create_trace() → client.start_observation(as_type="chain") + root.update_trace()
- create_span() → root.start_observation() (nested under root, supports parent + type)
- start_span() / end_observation() → long-running observation lifecycle
- create_generation() → root.start_generation() (nested under root)
- update_trace() → root.update_trace() (not dict.update)
- end_trace() → root.end()
- create_score() → client.score()
"""

import unittest.mock
from unittest.mock import MagicMock

from api.services.obs.langfuse_client import LangfuseLogger


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_logger_with_mock_client() -> tuple[LangfuseLogger, MagicMock]:
    """Create a LangfuseLogger with a mocked Langfuse SDK client."""
    lf = LangfuseLogger.__new__(LangfuseLogger)
    lf.enabled = True
    lf._trace_metadata = {}
    lf._open_observations = {}
    lf._rate_limit_until = 0.0

    mock_client = MagicMock()
    mock_client.create_trace_id.return_value = "trace_abc123"
    lf.client = mock_client

    # Mock root observation returned by client.start_observation()
    mock_root = MagicMock()
    mock_root.id = "root_span_001"
    mock_client.start_observation.return_value = mock_root

    return lf, mock_client


# ---------------------------------------------------------------------------
# create_trace
# ---------------------------------------------------------------------------


def test_create_trace_sends_to_server():
    """create_trace calls client.start_observation(as_type='chain') + root.update_trace."""
    lf, mock_client = _make_logger_with_mock_client()

    trace_id = lf.create_trace(
        name="feedback_cycle",
        input={"campaign_id": "c1"},
        metadata={"key": "val"},
        user_id="user1",
        session_id="sess1",
        tags=["campaign"],
    )

    assert trace_id == "trace_abc123"

    # client.start_observation called with as_type="chain"
    mock_client.start_observation.assert_called_once()
    call_kwargs = mock_client.start_observation.call_args[1]
    assert call_kwargs["trace_context"] == {"trace_id": "trace_abc123"}
    assert call_kwargs["as_type"] == "chain"
    assert call_kwargs["name"] == "feedback_cycle"
    assert call_kwargs["input"] == {"campaign_id": "c1"}

    # root.update_trace called to push trace metadata to server
    mock_root = mock_client.start_observation.return_value
    mock_root.update_trace.assert_called_once_with(
        name="feedback_cycle",
        user_id="user1",
        session_id="sess1",
        input={"campaign_id": "c1"},
        metadata={"key": "val"},
        tags=["campaign"],
    )

    # Root observation stored (not a plain dict)
    assert lf._trace_metadata[trace_id] is mock_root


def test_create_trace_disabled():
    """create_trace returns None when disabled."""
    lf, _ = _make_logger_with_mock_client()
    lf.enabled = False
    assert lf.create_trace("x", {}) is None


# ---------------------------------------------------------------------------
# create_span
# ---------------------------------------------------------------------------


def test_create_span_nests_under_root():
    """create_span calls root.start_observation (child nesting)."""
    lf, mock_client = _make_logger_with_mock_client()

    # Create trace first to populate root
    lf.create_trace(name="cycle", input={})
    mock_root = mock_client.start_observation.return_value

    mock_child = MagicMock()
    mock_child.id = "child_001"
    mock_root.start_observation.return_value = mock_child

    span_id = lf.create_span(
        trace_id="trace_abc123",
        name="round_0",
        input={"n": 5},
        output={"accuracy": 0.7},
        metadata={"round": 0},
    )

    assert span_id == "child_001"
    mock_root.start_observation.assert_called_once_with(
        as_type="span",
        name="round_0",
        input={"n": 5},
        output={"accuracy": 0.7},
        metadata={"round": 0},
    )
    mock_child.end.assert_called_once()


def test_create_span_returns_none_without_trace():
    """create_span returns None when trace_id doesn't exist."""
    lf, _ = _make_logger_with_mock_client()
    assert lf.create_span("nonexistent", "span", {}, {}) is None


# ---------------------------------------------------------------------------
# create_generation
# ---------------------------------------------------------------------------


def test_create_generation_nests_under_root():
    """create_generation calls root.start_generation (nested)."""
    lf, mock_client = _make_logger_with_mock_client()

    lf.create_trace(name="cycle", input={})
    mock_root = mock_client.start_observation.return_value

    mock_gen = MagicMock()
    mock_gen.id = "gen_001"
    mock_root.start_generation.return_value = mock_gen

    gen_id = lf.create_generation(
        trace_id="trace_abc123",
        name="llm_call",
        model="gpt-4",
        input="prompt",
        output="response",
        usage={"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
        metadata={"step": "generate"},
    )

    assert gen_id == "gen_001"
    mock_root.start_generation.assert_called_once_with(
        name="llm_call",
        model="gpt-4",
        input="prompt",
        output="response",
        usage_details={"input": 10, "output": 5, "total": 15},
        metadata={"step": "generate"},
    )
    mock_gen.end.assert_called_once()


def test_create_generation_returns_none_without_trace():
    """create_generation returns None when trace_id doesn't exist."""
    lf, _ = _make_logger_with_mock_client()
    assert lf.create_generation("bad", "gen", "m", "in", "out") is None


# ---------------------------------------------------------------------------
# update_trace
# ---------------------------------------------------------------------------


def test_update_trace_uses_sdk_object():
    """update_trace calls root.update_trace (SDK method, not dict.update)."""
    lf, mock_client = _make_logger_with_mock_client()

    lf.create_trace(name="cycle", input={})
    mock_root = mock_client.start_observation.return_value
    mock_root.update_trace.reset_mock()  # clear the call from create_trace

    result = lf.update_trace(
        trace_id="trace_abc123",
        output={"best_accuracy": 0.85},
        metadata={"stop_reason": "patience"},
    )

    assert result is True
    mock_root.update_trace.assert_called_once_with(
        output={"best_accuracy": 0.85},
        metadata={"stop_reason": "patience"},
    )


def test_update_trace_partial_kwargs():
    """update_trace only sends non-None kwargs."""
    lf, mock_client = _make_logger_with_mock_client()

    lf.create_trace(name="cycle", input={})
    mock_root = mock_client.start_observation.return_value
    mock_root.update_trace.reset_mock()

    lf.update_trace(trace_id="trace_abc123", output={"done": True})

    mock_root.update_trace.assert_called_once_with(output={"done": True})


# ---------------------------------------------------------------------------
# end_trace
# ---------------------------------------------------------------------------


def test_end_trace_ends_root_span():
    """end_trace calls root.end()."""
    lf, mock_client = _make_logger_with_mock_client()

    lf.create_trace(name="cycle", input={})
    mock_root = mock_client.start_observation.return_value

    lf.end_trace("trace_abc123")

    mock_root.end.assert_called_once()


def test_end_trace_noop_for_unknown():
    """end_trace is a no-op for unknown trace_id."""
    lf, mock_client = _make_logger_with_mock_client()
    # Should not raise
    lf.end_trace("nonexistent")


# ---------------------------------------------------------------------------
# create_score
# ---------------------------------------------------------------------------


def test_create_score_calls_client():
    """create_score calls client.create_score() (SDK v3 method)."""
    lf, mock_client = _make_logger_with_mock_client()

    result = lf.create_score(
        trace_id="trace_abc123",
        name="accuracy",
        value=0.85,
        comment="round 2",
    )

    assert result is True
    mock_client.create_score.assert_called_once_with(
        trace_id="trace_abc123",
        name="accuracy",
        value=0.85,
        data_type="NUMERIC",
        comment="round 2",
    )


# ---------------------------------------------------------------------------
# flush / shutdown
# ---------------------------------------------------------------------------


def test_flush_calls_client():
    """flush delegates to client.flush()."""
    lf, mock_client = _make_logger_with_mock_client()
    lf.flush()
    mock_client.flush.assert_called_once()


def test_shutdown_calls_client():
    """shutdown delegates to client.shutdown()."""
    lf, mock_client = _make_logger_with_mock_client()
    lf.shutdown()
    mock_client.shutdown.assert_called_once()


# ---------------------------------------------------------------------------
# Dataset API
# ---------------------------------------------------------------------------


def test_create_dataset():
    """create_dataset calls client.create_dataset()."""
    lf, mock_client = _make_logger_with_mock_client()

    result = lf.create_dataset(
        name="termnorm_ground_truth",
        description="Ground truth queries",
        metadata={"source": "production"},
    )

    assert result is True
    mock_client.create_dataset.assert_called_once_with(
        name="termnorm_ground_truth",
        description="Ground truth queries",
        metadata={"source": "production"},
    )


def test_create_dataset_disabled():
    """create_dataset returns False when disabled."""
    lf, _ = _make_logger_with_mock_client()
    lf.enabled = False
    assert lf.create_dataset("x") is False


def test_create_dataset_item():
    """create_dataset_item calls client.create_dataset_item()."""
    lf, mock_client = _make_logger_with_mock_client()

    mock_item = MagicMock()
    mock_item.id = "item_001"
    mock_client.create_dataset_item.return_value = mock_item

    item_id = lf.create_dataset_item(
        dataset_name="termnorm_ground_truth",
        input={"query": "aspirin"},
        expected_output="Aspirin",
        metadata={"source": "eval"},
    )

    assert item_id == "item_001"
    mock_client.create_dataset_item.assert_called_once_with(
        dataset_name="termnorm_ground_truth",
        input={"query": "aspirin"},
        expected_output="Aspirin",
        metadata={"source": "eval"},
    )


def test_create_dataset_item_disabled():
    """create_dataset_item returns None when disabled."""
    lf, _ = _make_logger_with_mock_client()
    lf.enabled = False
    assert lf.create_dataset_item("ds", {}) is None


def test_get_dataset():
    """get_dataset calls client.get_dataset()."""
    lf, mock_client = _make_logger_with_mock_client()

    mock_ds = MagicMock()
    mock_ds.name = "termnorm_ground_truth"
    mock_client.get_dataset.return_value = mock_ds

    ds = lf.get_dataset("termnorm_ground_truth")

    assert ds is mock_ds
    mock_client.get_dataset.assert_called_once_with(name="termnorm_ground_truth")


def test_get_dataset_disabled():
    """get_dataset returns None when disabled."""
    lf, _ = _make_logger_with_mock_client()
    lf.enabled = False
    assert lf.get_dataset("x") is None


def test_update_dataset_item():
    """update_dataset_item calls client.create_dataset_item with id + expected_output."""
    lf, mock_client = _make_logger_with_mock_client()

    result = lf.update_dataset_item(
        item_id="item_001",
        expected_output="Aspirin",
        metadata={"updated": True},
    )

    assert result is True
    mock_client.create_dataset_item.assert_called_once_with(
        id="item_001",
        expected_output="Aspirin",
        metadata={"updated": True},
    )


def test_update_dataset_item_disabled():
    """update_dataset_item returns False when disabled."""
    lf, _ = _make_logger_with_mock_client()
    lf.enabled = False
    assert lf.update_dataset_item("item_001") is False


def test_link_item_to_run():
    """link_item_to_run POSTs to the REST API."""
    lf, mock_client = _make_logger_with_mock_client()

    mock_resp = MagicMock()
    mock_resp.status_code = 200

    with unittest.mock.patch("api.services.obs.langfuse_client.requests") as mock_requests:
        mock_requests.post.return_value = mock_resp

        result = lf.link_item_to_run(
            dataset_item_id="item_001",
            trace_id="trace_abc",
            observation_id="span_001",
            run_name="baseline_abc",
            run_metadata={"origin": "baseline"},
        )

    assert result is True
    mock_requests.post.assert_called_once()
    call_kwargs = mock_requests.post.call_args
    body = call_kwargs.kwargs.get("json") or call_kwargs[1].get("json")
    assert body["datasetItemId"] == "item_001"
    assert body["traceId"] == "trace_abc"
    assert body["observationId"] == "span_001"
    assert body["runName"] == "baseline_abc"


def test_link_item_to_run_disabled():
    """link_item_to_run returns False when disabled."""
    lf, _ = _make_logger_with_mock_client()
    lf.enabled = False
    assert lf.link_item_to_run("item_001", "trace_abc") is False


# ---------------------------------------------------------------------------
# start_span / end_observation (long-running observation lifecycle)
# ---------------------------------------------------------------------------


def test_start_span_stores_open_observation():
    """start_span opens a long-running observation and stores it."""
    lf, mock_client = _make_logger_with_mock_client()

    lf.create_trace(name="cycle", input={})
    mock_root = mock_client.start_observation.return_value

    mock_child = MagicMock()
    mock_child.id = "round_obs_001"
    mock_root.start_observation.return_value = mock_child

    obs_id = lf.start_span(
        trace_id="trace_abc123",
        name="round_0",
        input={"round": 0},
        metadata={"round": 0},
    )

    assert obs_id == "round_obs_001"
    # Observation is stored in _open_observations (not immediately ended)
    assert lf._open_observations["round_obs_001"] is mock_child
    mock_child.end.assert_not_called()

    mock_root.start_observation.assert_called_once_with(
        as_type="span",
        name="round_0",
        input={"round": 0},
        metadata={"round": 0},
    )


def test_end_observation_closes_span():
    """end_observation closes a previously started span with output."""
    lf, mock_client = _make_logger_with_mock_client()

    lf.create_trace(name="cycle", input={})
    mock_root = mock_client.start_observation.return_value

    mock_child = MagicMock()
    mock_child.id = "round_obs_001"
    mock_root.start_observation.return_value = mock_child

    obs_id = lf.start_span(trace_id="trace_abc123", name="round_0")
    assert obs_id == "round_obs_001"

    lf.end_observation(
        obs_id="round_obs_001",
        output={"accuracy": 0.85, "improved": True},
        metadata={"candidates": 5},
    )

    mock_child.update.assert_called_once_with(
        output={"accuracy": 0.85, "improved": True},
        metadata={"candidates": 5},
    )
    mock_child.end.assert_called_once()
    # Removed from open observations
    assert "round_obs_001" not in lf._open_observations


def test_end_observation_noop_for_unknown():
    """end_observation is a no-op for unknown obs_id."""
    lf, _ = _make_logger_with_mock_client()
    # Should not raise
    lf.end_observation("nonexistent", output={"x": 1})


# ---------------------------------------------------------------------------
# create_span — parent + type overrides
# ---------------------------------------------------------------------------


def test_create_span_with_parent():
    """create_span nests under parent observation when parent_observation_id is set."""
    lf, mock_client = _make_logger_with_mock_client()

    lf.create_trace(name="cycle", input={})
    mock_root = mock_client.start_observation.return_value

    # Open a round span
    mock_round = MagicMock()
    mock_round.id = "round_obs_001"
    mock_root.start_observation.return_value = mock_round

    round_id = lf.start_span(trace_id="trace_abc123", name="round_0")

    # Now create a child span under the round
    mock_eval = MagicMock()
    mock_eval.id = "eval_001"
    mock_round.start_observation.return_value = mock_eval

    span_id = lf.create_span(
        trace_id="trace_abc123",
        name="eval_candidate_1",
        input={"run_id": "abc"},
        output={"accuracy": 0.9},
        parent_observation_id=round_id,
        as_type="tool",
    )

    assert span_id == "eval_001"
    mock_round.start_observation.assert_called_with(
        as_type="tool",
        name="eval_candidate_1",
        input={"run_id": "abc"},
        output={"accuracy": 0.9},
        metadata={},
    )
    mock_eval.end.assert_called_once()


def test_create_span_with_type():
    """create_span respects as_type parameter."""
    lf, mock_client = _make_logger_with_mock_client()

    lf.create_trace(name="cycle", input={})
    mock_root = mock_client.start_observation.return_value

    mock_child = MagicMock()
    mock_child.id = "tool_001"
    mock_root.start_observation.return_value = mock_child

    span_id = lf.create_span(
        trace_id="trace_abc123",
        name="prompt_version",
        input={},
        output={},
        as_type="tool",
    )

    assert span_id == "tool_001"
    call_kwargs = mock_root.start_observation.call_args[1]
    assert call_kwargs["as_type"] == "tool"
