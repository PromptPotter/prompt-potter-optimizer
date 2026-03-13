"""Tests for cloud Langfuse integration via ObsLogger (WP 3.6).

Verifies that cloud Langfuse calls flow through ObsLogger's dual-write:
campaign trace, per-round spans/scores, final score, trace update + flush.
"""

import pytest

from api.services.campaign.models import CycleConfig
from api.services.campaign.feedback_cycle import run_feedback_cycle
from api.services.obs.langfuse_client import LangfuseLogger

from _helpers import (
    MockLangfuseLogger,
    apply_init_mock,
    apply_eval_mock,
    apply_grow_mock,
    apply_llm_mock,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def cycle_config(tmp_path):
    """Config with project_root + backend_id so ObsLogger is created."""
    store_base = tmp_path / ".promptpotter" / "projects"
    store_base.mkdir(parents=True)
    return CycleConfig(
        max_rounds=3,
        patience=2,
        n_variants=2,
        backend_url="http://mock:8000",
        generate_suggestions=False,
        project_root=str(store_base),
        backend_id="test-backend",
    )


@pytest.fixture
def mock_langfuse(monkeypatch):
    """Install mock Langfuse so ObsLogger auto-detects it."""
    from api.config import settings as _settings_mod

    monkeypatch.setattr(_settings_mod.settings, "OBS_ENABLED", True)

    mock = MockLangfuseLogger()
    monkeypatch.setattr(
        LangfuseLogger, "get_instance", classmethod(lambda cls: mock),
    )
    return mock


def _apply_service_mocks(monkeypatch):
    """Apply all service mocks for Langfuse integration testing."""
    apply_init_mock(monkeypatch)
    apply_llm_mock(monkeypatch)
    apply_grow_mock(monkeypatch)
    apply_eval_mock(monkeypatch, round_hits=[1, 1, 1])


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_full_langfuse_integration(
    monkeypatch, eval_data, cycle_config, mock_langfuse,
):
    """Full feedback cycle: campaign trace, per-round spans/scores, final output."""
    _apply_service_mocks(monkeypatch)

    result = await run_feedback_cycle(
        instruction="Test.",
        eval_data=eval_data,
        config=cycle_config,
        langfuse_session_id="test_session_123",
    )

    # Cloud trace ID returned
    assert result.langfuse_trace_id is not None
    assert result.langfuse_trace_id.startswith("mock_trace_")

    # Exactly one campaign trace
    campaign_traces = [t for t in mock_langfuse.traces if t["name"] == "feedback_cycle"]
    assert len(campaign_traces) == 1
    assert campaign_traces[0]["session_id"] == "test_session_123"
    assert "campaign" in campaign_traces[0]["tags"]

    # Per-round spans (3 rounds)
    assert result.n_rounds == 3
    round_spans = [s for s in mock_langfuse.spans if s["name"].startswith("round_")]
    assert len(round_spans) == 3
    for i, span in enumerate(round_spans):
        assert span["trace_id"] == result.langfuse_trace_id
        assert span["name"] == f"round_{i}"
        assert "winner_accuracy" in span["output"]

    # Per-round accuracy scores
    round_scores = [
        s for s in mock_langfuse.scores if s["name"].startswith("accuracy_round_")
    ]
    assert len(round_scores) == result.n_rounds

    # Final best_accuracy score
    best_scores = [s for s in mock_langfuse.scores if s["name"] == "best_accuracy"]
    assert len(best_scores) == 1
    assert best_scores[0]["value"] == result.best_accuracy

    # Trace updated with final output and flushed
    assert len(mock_langfuse.trace_updates) == 1
    update = mock_langfuse.trace_updates[0]
    assert update["trace_id"] == result.langfuse_trace_id
    assert update["output"]["stop_reason"] == result.stop_reason
    assert update["output"]["best_accuracy"] == result.best_accuracy
    assert mock_langfuse.flush_count >= 1

    # end_trace called
    assert len(mock_langfuse.end_trace_calls) == 1
    assert mock_langfuse.end_trace_calls[0] == result.langfuse_trace_id
