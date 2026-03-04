"""Unit tests for LangfuseLogger SDK wrapper.

Verifies trace/span/generation/score delegation to Langfuse SDK,
dataset CRUD, and long-running observation lifecycle.
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

    mock_root = MagicMock()
    mock_root.id = "root_span_001"
    mock_client.start_observation.return_value = mock_root

    return lf, mock_client


# ---------------------------------------------------------------------------
# Trace lifecycle
# ---------------------------------------------------------------------------


def test_create_trace():
    """create_trace → start_observation(as_type='chain') + root.update_trace."""
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

    mock_client.start_observation.assert_called_once()
    call_kwargs = mock_client.start_observation.call_args[1]
    assert call_kwargs["trace_context"] == {"trace_id": "trace_abc123"}
    assert call_kwargs["as_type"] == "chain"

    mock_root = mock_client.start_observation.return_value
    mock_root.update_trace.assert_called_once_with(
        name="feedback_cycle",
        user_id="user1",
        session_id="sess1",
        input={"campaign_id": "c1"},
        metadata={"key": "val"},
        tags=["campaign"],
    )
    assert lf._trace_metadata[trace_id] is mock_root


def test_update_trace():
    """update_trace calls root.update_trace (SDK method)."""
    lf, mock_client = _make_logger_with_mock_client()
    lf.create_trace(name="cycle", input={})
    mock_root = mock_client.start_observation.return_value
    mock_root.update_trace.reset_mock()

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


def test_end_trace():
    """end_trace calls root.end()."""
    lf, mock_client = _make_logger_with_mock_client()
    lf.create_trace(name="cycle", input={})
    mock_root = mock_client.start_observation.return_value

    lf.end_trace("trace_abc123")
    mock_root.end.assert_called_once()


# ---------------------------------------------------------------------------
# Spans and generations
# ---------------------------------------------------------------------------


def test_create_span_nests_under_root():
    """create_span calls root.start_observation (child nesting)."""
    lf, mock_client = _make_logger_with_mock_client()
    lf.create_trace(name="cycle", input={})
    mock_root = mock_client.start_observation.return_value

    mock_child = MagicMock()
    mock_child.id = "child_001"
    mock_root.start_observation.return_value = mock_child

    span_id = lf.create_span(
        trace_id="trace_abc123", name="round_0",
        input={"n": 5}, output={"accuracy": 0.7}, metadata={"round": 0},
    )

    assert span_id == "child_001"
    mock_root.start_observation.assert_called_once_with(
        as_type="span", name="round_0",
        input={"n": 5}, output={"accuracy": 0.7}, metadata={"round": 0},
    )
    mock_child.end.assert_called_once()


def test_create_span_with_parent_and_type():
    """create_span nests under parent observation and respects as_type."""
    lf, mock_client = _make_logger_with_mock_client()
    lf.create_trace(name="cycle", input={})
    mock_root = mock_client.start_observation.return_value

    mock_round = MagicMock()
    mock_round.id = "round_obs_001"
    mock_root.start_observation.return_value = mock_round

    round_id = lf.start_span(trace_id="trace_abc123", name="round_0")

    mock_eval = MagicMock()
    mock_eval.id = "eval_001"
    mock_round.start_observation.return_value = mock_eval

    span_id = lf.create_span(
        trace_id="trace_abc123", name="eval_candidate",
        input={}, output={},
        parent_observation_id=round_id, as_type="tool",
    )

    assert span_id == "eval_001"
    call_kwargs = mock_round.start_observation.call_args[1]
    assert call_kwargs["as_type"] == "tool"
    mock_eval.end.assert_called_once()


def test_create_generation():
    """create_generation calls root.start_generation (nested)."""
    lf, mock_client = _make_logger_with_mock_client()
    lf.create_trace(name="cycle", input={})
    mock_root = mock_client.start_observation.return_value

    mock_gen = MagicMock()
    mock_gen.id = "gen_001"
    mock_root.start_generation.return_value = mock_gen

    gen_id = lf.create_generation(
        trace_id="trace_abc123", name="llm_call", model="gpt-4",
        input="prompt", output="response",
        usage={"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
    )

    assert gen_id == "gen_001"
    mock_root.start_generation.assert_called_once()
    mock_gen.end.assert_called_once()


# ---------------------------------------------------------------------------
# Long-running observation lifecycle
# ---------------------------------------------------------------------------


def test_observation_lifecycle():
    """start_span stores open observation; end_observation closes it with output."""
    lf, mock_client = _make_logger_with_mock_client()
    lf.create_trace(name="cycle", input={})
    mock_root = mock_client.start_observation.return_value

    mock_child = MagicMock()
    mock_child.id = "round_obs_001"
    mock_root.start_observation.return_value = mock_child

    obs_id = lf.start_span(trace_id="trace_abc123", name="round_0", input={"round": 0})
    assert obs_id == "round_obs_001"
    assert lf._open_observations["round_obs_001"] is mock_child
    mock_child.end.assert_not_called()

    lf.end_observation(
        obs_id="round_obs_001",
        output={"accuracy": 0.85},
        metadata={"candidates": 5},
    )

    mock_child.update.assert_called_once_with(
        output={"accuracy": 0.85}, metadata={"candidates": 5},
    )
    mock_child.end.assert_called_once()
    assert "round_obs_001" not in lf._open_observations


# ---------------------------------------------------------------------------
# Score and dataset
# ---------------------------------------------------------------------------


def test_create_score():
    """create_score calls client.create_score()."""
    lf, mock_client = _make_logger_with_mock_client()
    lf.create_score(trace_id="trace_abc123", name="accuracy", value=0.85, comment="round 2")
    mock_client.create_score.assert_called_once_with(
        trace_id="trace_abc123", name="accuracy", value=0.85,
        data_type="NUMERIC", comment="round 2",
    )


def test_dataset_crud():
    """create_dataset, create_dataset_item, update_dataset_item, link_item_to_run."""
    lf, mock_client = _make_logger_with_mock_client()

    # create_dataset
    lf.create_dataset(name="gt", description="Ground truth", metadata={"src": "prod"})
    mock_client.create_dataset.assert_called_once()

    # create_dataset_item
    mock_item = MagicMock()
    mock_item.id = "item_001"
    mock_client.create_dataset_item.return_value = mock_item
    item_id = lf.create_dataset_item(
        dataset_name="gt", input={"query": "aspirin"}, expected_output="Aspirin",
    )
    assert item_id == "item_001"

    # update_dataset_item
    mock_client.create_dataset_item.reset_mock()
    lf.update_dataset_item(item_id="item_001", expected_output="Updated")
    mock_client.create_dataset_item.assert_called_once_with(
        id="item_001", expected_output="Updated",
    )

    # link_item_to_run
    with unittest.mock.patch("api.services.obs.langfuse_client.requests") as mock_requests:
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_requests.post.return_value = mock_resp

        lf.link_item_to_run(
            dataset_item_id="item_001", trace_id="trace_abc",
            observation_id="span_001", run_name="baseline_abc",
        )

    mock_requests.post.assert_called_once()
    body = mock_requests.post.call_args.kwargs.get("json") or \
        mock_requests.post.call_args[1].get("json")
    assert body["datasetItemId"] == "item_001"
