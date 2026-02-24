"""Tests for Langfuse integration with feedback cycling (WP 3.6).

Verifies that:
- Campaign-level trace is created for each feedback cycle
- Per-round spans are logged with accuracy metadata
- Per-round accuracy scores are emitted
- Final best_accuracy score is emitted
- Trace is updated with final output and flushed
"""

import pytest

from api.services.feedback_cycle import CycleConfig, run_feedback_cycle
from api.services.langfuse_client import LangfuseLogger


# ---------------------------------------------------------------------------
# Mock Langfuse logger
# ---------------------------------------------------------------------------


class MockLangfuseLogger:
    """Records all Langfuse calls for test verification."""

    def __init__(self):
        self.enabled = True
        self.traces: list[dict] = []
        self.spans: list[dict] = []
        self.scores: list[dict] = []
        self.generations: list[dict] = []
        self.trace_updates: list[dict] = []
        self.flush_count = 0
        self._next_trace_id = "mock_trace_001"

    def create_trace(self, name, input, metadata=None, user_id=None,
                     session_id=None, tags=None):
        self.traces.append({
            "name": name, "input": input, "metadata": metadata,
            "session_id": session_id, "tags": tags,
        })
        return self._next_trace_id

    def create_span(self, trace_id, name, input, output, metadata=None):
        self.spans.append({
            "trace_id": trace_id, "name": name,
            "input": input, "output": output, "metadata": metadata,
        })
        return f"span_{name}"

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

    def flush(self):
        self.flush_count += 1


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def eval_data():
    return [
        {"query": "aspirin", "ground_truth": "Aspirin"},
        {"query": "ibuprofen", "ground_truth": "Ibuprofen"},
        {"query": "acetaminophen", "ground_truth": "Acetaminophen"},
    ]


@pytest.fixture
def cycle_config():
    return CycleConfig(
        max_rounds=3,
        patience=2,
        n_variants=2,
        backend_url="http://mock:8000",
        generate_suggestions=False,
    )


@pytest.fixture
def mock_langfuse(monkeypatch):
    """Install mock Langfuse and return it for assertions."""
    mock = MockLangfuseLogger()
    monkeypatch.setattr(
        LangfuseLogger, "get_instance", classmethod(lambda cls: mock),
    )
    return mock


def _apply_service_mocks(monkeypatch):
    """Apply all service mocks for Langfuse integration testing."""
    async def mock_restructure(context_input, llm_client, **kwargs):
        return {"instruction": "test", "persona": "expert"}

    monkeypatch.setattr(
        "api.services.search.context.restructure_context",
        mock_restructure,
    )

    async def mock_generate(current_ps, accuracy, results, n, creativity,
                            llm_client, **kwargs):
        return [
            current_ps.derive(
                instruction=f"v{i}", changes_description=f"gen_{i}",
            )
            for i in range(n)
        ]

    monkeypatch.setattr(
        "api.services.prompt_optimizer.generate_candidates",
        mock_generate,
    )

    # All candidates get 33% accuracy → no improvement → patience stop
    async def mock_eval(ps, data, backend_client, **kwargs):
        label = kwargs.get("label", "")
        if label == "candidate_0":
            results = [
                {"query": data[0]["query"],
                 "predicted": data[0]["ground_truth"],
                 "ground_truth": data[0]["ground_truth"],
                 "hit": True, "score": 1.0, "error": None},
            ] + [
                {"query": d["query"], "predicted": "WRONG",
                 "ground_truth": d["ground_truth"],
                 "hit": False, "score": 0.0, "error": None}
                for d in data[1:]
            ]
            scores = {"hits": 1, "total": len(data),
                      "accuracy": 1 / len(data), "errors": 0}
        else:
            results = [
                {"query": d["query"], "predicted": "WRONG",
                 "ground_truth": d["ground_truth"],
                 "hit": False, "score": 0.0, "error": None}
                for d in data
            ]
            scores = {"hits": 0, "total": len(data),
                      "accuracy": 0.0, "errors": 0}
        return results, scores, False

    monkeypatch.setattr(
        "api.services.prompt_eval.evaluate_prompt_cached",
        mock_eval,
    )

    from api.services.llm_client import MockLLMClient
    monkeypatch.setattr(
        "api.services.llm_client.get_llm_client",
        lambda provider=None: MockLLMClient(),
    )


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_campaign_trace_created(
    monkeypatch, eval_data, cycle_config, mock_langfuse,
):
    """Feedback cycle creates a campaign-level Langfuse trace."""
    _apply_service_mocks(monkeypatch)

    result = await run_feedback_cycle(
        instruction="Test.",
        eval_data=eval_data,
        config=cycle_config,
        langfuse_session_id="test_session_123",
    )

    assert result.langfuse_trace_id == "mock_trace_001"
    assert len(mock_langfuse.traces) == 1
    assert mock_langfuse.traces[0]["name"] == "feedback_cycle"
    assert mock_langfuse.traces[0]["session_id"] == "test_session_123"
    assert "campaign" in mock_langfuse.traces[0]["tags"]


@pytest.mark.asyncio
async def test_per_round_spans(
    monkeypatch, eval_data, cycle_config, mock_langfuse,
):
    """Each round gets a Langfuse span with accuracy metadata."""
    _apply_service_mocks(monkeypatch)

    result = await run_feedback_cycle(
        instruction="Test.",
        eval_data=eval_data,
        config=cycle_config,
    )

    # Round 0 improves (0→0.33), then patience=2 stalls → 3 rounds total
    assert result.n_rounds == 3
    assert len(mock_langfuse.spans) == 3

    for i, span in enumerate(mock_langfuse.spans):
        assert span["trace_id"] == "mock_trace_001"
        assert span["name"] == f"round_{i}"
        assert "winner_accuracy" in span["output"]
        assert "improved" in span["output"]


@pytest.mark.asyncio
async def test_per_round_accuracy_scores(
    monkeypatch, eval_data, cycle_config, mock_langfuse,
):
    """Each round emits an accuracy score to Langfuse."""
    _apply_service_mocks(monkeypatch)

    result = await run_feedback_cycle(
        instruction="Test.",
        eval_data=eval_data,
        config=cycle_config,
    )

    # 2 rounds → 2 per-round scores + 1 final best_accuracy = 3 scores
    round_scores = [
        s for s in mock_langfuse.scores if s["name"].startswith("accuracy_round_")
    ]
    assert len(round_scores) == result.n_rounds

    for score in round_scores:
        assert score["trace_id"] == "mock_trace_001"
        assert isinstance(score["value"], float)


@pytest.mark.asyncio
async def test_final_best_accuracy_score(
    monkeypatch, eval_data, cycle_config, mock_langfuse,
):
    """Final best_accuracy score is emitted at cycle end."""
    _apply_service_mocks(monkeypatch)

    result = await run_feedback_cycle(
        instruction="Test.",
        eval_data=eval_data,
        config=cycle_config,
    )

    best_scores = [s for s in mock_langfuse.scores if s["name"] == "best_accuracy"]
    assert len(best_scores) == 1
    assert best_scores[0]["value"] == result.best_accuracy


@pytest.mark.asyncio
async def test_trace_updated_and_flushed(
    monkeypatch, eval_data, cycle_config, mock_langfuse,
):
    """Campaign trace is updated with final output and flushed."""
    _apply_service_mocks(monkeypatch)

    result = await run_feedback_cycle(
        instruction="Test.",
        eval_data=eval_data,
        config=cycle_config,
    )

    assert len(mock_langfuse.trace_updates) == 1
    update = mock_langfuse.trace_updates[0]
    assert update["trace_id"] == "mock_trace_001"
    assert update["output"]["stop_reason"] == result.stop_reason
    assert update["output"]["best_accuracy"] == result.best_accuracy

    assert mock_langfuse.flush_count >= 1
