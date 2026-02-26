"""Unit tests for LangfuseLogger SDK wrapper.

Verifies that LangfuseLogger methods delegate to the Langfuse SDK correctly:
- create_trace() → client.start_span() + root.update_trace()
- create_span() → root.start_span() (nested under root)
- create_generation() → root.start_generation() (nested under root)
- update_trace() → root.update_trace() (not dict.update)
- end_trace() → root.end()
- create_score() → client.score()
"""

from unittest.mock import MagicMock

from api.services.langfuse_client import LangfuseLogger


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_logger_with_mock_client() -> tuple[LangfuseLogger, MagicMock]:
    """Create a LangfuseLogger with a mocked Langfuse SDK client."""
    lf = LangfuseLogger.__new__(LangfuseLogger)
    lf.enabled = True
    lf._trace_metadata = {}

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
