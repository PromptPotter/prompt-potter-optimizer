"""Unit tests for LangfuseLogger SDK wrapper.

Verifies that LangfuseLogger methods delegate to the Langfuse SDK correctly:
- create_trace() → client.start_span() + root.update_trace()
- create_span() → root.start_span() (nested under root)
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
    lf._rate_limit_until = 0.0

    mock_client = MagicMock()
    mock_client.create_trace_id.return_value = "trace_abc123"
    lf.client = mock_client

    # Mock root span returned by client.start_span()
    mock_root = MagicMock()
    mock_root.id = "root_span_001"
    mock_client.start_span.return_value = mock_root

    return lf, mock_client


# ---------------------------------------------------------------------------
# create_trace
# ---------------------------------------------------------------------------


def test_create_trace_sends_to_server():
    """create_trace calls client.start_span + root.update_trace (not just local dict)."""
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

    # client.start_span called with trace context
    mock_client.start_span.assert_called_once()
    call_kwargs = mock_client.start_span.call_args[1]
    assert call_kwargs["trace_context"] == {"trace_id": "trace_abc123"}
    assert call_kwargs["name"] == "feedback_cycle"
    assert call_kwargs["input"] == {"campaign_id": "c1"}

    # root.update_trace called to push trace metadata to server
    mock_root = mock_client.start_span.return_value
    mock_root.update_trace.assert_called_once_with(
        name="feedback_cycle",
        user_id="user1",
        session_id="sess1",
        input={"campaign_id": "c1"},
        metadata={"key": "val"},
        tags=["campaign"],
    )

    # Root span stored (not a plain dict)
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
    """create_span calls root.start_span (child nesting, not client.start_span)."""
    lf, mock_client = _make_logger_with_mock_client()

    # Create trace first to populate root
    lf.create_trace(name="cycle", input={})
    mock_root = mock_client.start_span.return_value

    mock_child = MagicMock()
    mock_child.id = "child_001"
    mock_root.start_span.return_value = mock_child

    span_id = lf.create_span(
        trace_id="trace_abc123",
        name="round_0",
        input={"n": 5},
        output={"accuracy": 0.7},
        metadata={"round": 0},
    )

    assert span_id == "child_001"
    mock_root.start_span.assert_called_once_with(
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
    mock_root = mock_client.start_span.return_value

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
    mock_root = mock_client.start_span.return_value
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
    mock_root = mock_client.start_span.return_value
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
    mock_root = mock_client.start_span.return_value

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
