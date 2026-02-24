"""Tests for feedback cycling (WP 3.4).

Covers:
- Multi-round optimization with improving candidates
- Patience-based stopping (consecutive non-improvements)
- Perfect score early exit
- next_action routing from analysis node
- AnalysisEvalOutput.next_action field
"""

import pytest

from api.services.feedback_cycle import CycleConfig, run_feedback_cycle


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
        max_rounds=5,
        patience=2,
        n_variants=3,
        creativity=0.5,
        improvement_threshold=0.01,
        backend_url="http://mock:8000",
        generate_suggestions=False,
    )


# ---------------------------------------------------------------------------
# Shared mock setup
# ---------------------------------------------------------------------------


def _apply_init_mock(monkeypatch):
    """Mock InitNode's restructure_context."""
    async def mock_restructure(context_input, llm_client, **kwargs):
        return {
            "persona": "Expert",
            "instruction": "Rank by relevance",
            "thinking_style": "Step by step",
        }

    monkeypatch.setattr(
        "api.services.search.context.restructure_context",
        mock_restructure,
    )


def _apply_llm_mock(monkeypatch):
    """Mock get_llm_client."""
    from api.services.llm_client import MockLLMClient
    monkeypatch.setattr(
        "api.services.llm_client.get_llm_client",
        lambda provider=None: MockLLMClient(),
    )


def _apply_grow_mock(monkeypatch):
    """Mock generate_candidates."""
    async def mock_generate(current_ps, accuracy, results, n, creativity,
                            llm_client, **kwargs):
        return [
            current_ps.derive(
                instruction=f"variant_{i}_acc{accuracy:.0%}",
                changes_description=f"gen_{i}",
            )
            for i in range(n)
        ]

    monkeypatch.setattr(
        "api.services.prompt_optimizer.generate_candidates",
        mock_generate,
    )


# ---------------------------------------------------------------------------
# Multi-round improving test
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_multi_round_improvement(monkeypatch, eval_data, cycle_config):
    """Feedback cycle improves accuracy over multiple rounds."""
    _apply_init_mock(monkeypatch)
    _apply_llm_mock(monkeypatch)
    _apply_grow_mock(monkeypatch)

    # Hits improve each round: 1/3 → 2/3 → 3/3 (perfect, stops early)
    round_hits = [1, 2, 3]
    call_count = [0]

    async def mock_eval(ps, data, backend_client, **kwargs):
        idx = min(call_count[0], len(round_hits) - 1)
        target_hits = round_hits[idx]

        label = kwargs.get("label", "")
        # First candidate each round gets the improving accuracy
        if label == "candidate_0":
            results = []
            for i, d in enumerate(data):
                hit = i < target_hits
                results.append({
                    "query": d["query"],
                    "predicted": d["ground_truth"] if hit else "WRONG",
                    "ground_truth": d["ground_truth"],
                    "hit": hit, "score": 1.0 if hit else 0.0, "error": None,
                })
            scores = {"hits": target_hits, "total": len(data),
                      "accuracy": target_hits / len(data), "errors": 0}
            call_count[0] += 1
        else:
            results = [
                {"query": d["query"], "predicted": "WRONG",
                 "ground_truth": d["ground_truth"], "hit": False,
                 "score": 0.0, "error": None}
                for d in data
            ]
            scores = {"hits": 0, "total": len(data),
                      "accuracy": 0.0, "errors": 0}
        return results, scores, False

    monkeypatch.setattr(
        "api.services.prompt_eval.evaluate_prompt_cached",
        mock_eval,
    )

    result = await run_feedback_cycle(
        instruction="Rank candidates.",
        eval_data=eval_data,
        config=cycle_config,
    )

    assert result.n_rounds == 3
    assert result.best_accuracy == 1.0
    assert result.stop_reason == "perfect_score"
    assert result.baseline_accuracy == pytest.approx(1 / 3, abs=0.01)
    # Each round should show improvement
    assert result.rounds[0].accuracy == pytest.approx(1 / 3, abs=0.01)
    assert result.rounds[1].accuracy == pytest.approx(2 / 3, abs=0.01)
    assert result.rounds[2].accuracy == 1.0


# ---------------------------------------------------------------------------
# Patience exhaustion test
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_patience_exhaustion(monkeypatch, eval_data, cycle_config):
    """Loop stops after patience consecutive non-improvements."""
    _apply_init_mock(monkeypatch)
    _apply_llm_mock(monkeypatch)
    _apply_grow_mock(monkeypatch)

    # All candidates return 0% — never beats baseline (which is also 0%)
    async def mock_eval(ps, data, backend_client, **kwargs):
        results = [
            {"query": d["query"], "predicted": "WRONG",
             "ground_truth": d["ground_truth"], "hit": False,
             "score": 0.0, "error": None}
            for d in data
        ]
        scores = {"hits": 0, "total": len(data), "accuracy": 0.0, "errors": 0}
        return results, scores, False

    monkeypatch.setattr(
        "api.services.prompt_eval.evaluate_prompt_cached",
        mock_eval,
    )

    result = await run_feedback_cycle(
        instruction="Rank candidates.",
        eval_data=eval_data,
        config=cycle_config,
    )

    # patience=2 → stops after 2 consecutive non-improvements
    assert result.stop_reason == "patience_exhausted"
    assert result.n_rounds == 2
    assert result.best_accuracy == 0.0


# ---------------------------------------------------------------------------
# Max rounds test
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_max_rounds(monkeypatch, eval_data):
    """Loop stops at max_rounds even with continuous improvement."""
    _apply_init_mock(monkeypatch)
    _apply_llm_mock(monkeypatch)
    _apply_grow_mock(monkeypatch)

    # Slow steady improvement — never reaches perfect or stalls
    call_idx = [0]

    async def mock_eval(ps, data, backend_client, **kwargs):
        label = kwargs.get("label", "")
        if label == "candidate_0":
            # Improve by small amount each round
            acc = min(0.1 + call_idx[0] * 0.1, 0.9)
            call_idx[0] += 1
            hits = max(1, int(acc * len(data)))
            results = []
            for i, d in enumerate(data):
                hit = i < hits
                results.append({
                    "query": d["query"],
                    "predicted": d["ground_truth"] if hit else "WRONG",
                    "ground_truth": d["ground_truth"],
                    "hit": hit, "score": 1.0 if hit else 0.0, "error": None,
                })
            scores = {"hits": hits, "total": len(data),
                      "accuracy": hits / len(data), "errors": 0}
        else:
            results = [
                {"query": d["query"], "predicted": "WRONG",
                 "ground_truth": d["ground_truth"], "hit": False,
                 "score": 0.0, "error": None}
                for d in data
            ]
            scores = {"hits": 0, "total": len(data),
                      "accuracy": 0.0, "errors": 0}
        return results, scores, False

    monkeypatch.setattr(
        "api.services.prompt_eval.evaluate_prompt_cached",
        mock_eval,
    )

    config = CycleConfig(
        max_rounds=3,
        patience=10,  # Won't trigger
        n_variants=2,
        backend_url="http://mock:8000",
        generate_suggestions=False,
    )

    result = await run_feedback_cycle(
        instruction="Rank candidates.",
        eval_data=eval_data,
        config=config,
    )

    assert result.stop_reason == "max_rounds"
    assert result.n_rounds == 3
    assert result.best_accuracy > 0


# ---------------------------------------------------------------------------
# next_action=stop test
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_next_action_stop(monkeypatch, eval_data, cycle_config):
    """Loop stops when AnalysisEvalNode outputs next_action='stop'."""
    _apply_init_mock(monkeypatch)
    _apply_llm_mock(monkeypatch)
    _apply_grow_mock(monkeypatch)

    # First candidate gets 33%, analysis suggestions say stop
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
                 "ground_truth": d["ground_truth"], "hit": False,
                 "score": 0.0, "error": None}
                for d in data[1:]
            ]
            scores = {"hits": 1, "total": len(data),
                      "accuracy": 1 / len(data), "errors": 0}
        else:
            results = [
                {"query": d["query"], "predicted": "WRONG",
                 "ground_truth": d["ground_truth"], "hit": False,
                 "score": 0.0, "error": None}
                for d in data
            ]
            scores = {"hits": 0, "total": len(data),
                      "accuracy": 0.0, "errors": 0}
        return results, scores, False

    monkeypatch.setattr(
        "api.services.prompt_eval.evaluate_prompt_cached",
        mock_eval,
    )

    # Mock generate_suggestions to return next_action=stop
    async def mock_suggestions(rounds, data, config, client, **kwargs):
        return {"next_action": "stop", "reason": "Manual stop signal"}

    monkeypatch.setattr(
        "api.services.prompt_optimizer.generate_suggestions",
        mock_suggestions,
    )

    # Enable suggestions so next_action is populated
    cycle_config.generate_suggestions = True
    # Need campaign_rounds for suggestions to trigger — we pass them
    # via the node's input. The cycle runner doesn't pass campaign_rounds
    # yet, but the suggestions mock will still be called because
    # generate_suggestions=True and campaign_rounds is empty → skipped.
    # So let's force it by setting a non-empty campaign_rounds.
    # Actually, the node checks `if config.generate_suggestions and
    # input_data.campaign_rounds`. Since campaign_rounds is empty in
    # the cycle runner, suggestions won't be called. Let me adjust
    # the cycle_config instead.
    cycle_config.generate_suggestions = False

    # Instead, test next_action via the output field directly.
    # The analysis node defaults to "generate". We need to verify
    # the stop mechanism works.
    from api.nodes.optimizer_nodes import AnalysisEvalNode

    original_execute = AnalysisEvalNode._execute

    async def patched_execute(self, input_data):
        result = await original_execute(self, input_data)
        # Force next_action to "stop"
        return result.model_copy(update={"next_action": "stop"})

    monkeypatch.setattr(AnalysisEvalNode, "_execute", patched_execute)

    result = await run_feedback_cycle(
        instruction="Rank candidates.",
        eval_data=eval_data,
        config=cycle_config,
    )

    assert result.stop_reason == "next_action_stop"
    assert result.n_rounds == 1


# ---------------------------------------------------------------------------
# AnalysisEvalOutput.next_action field test
# ---------------------------------------------------------------------------


def test_next_action_in_output():
    """AnalysisEvalOutput has next_action field with correct default."""
    from api.nodes.optimizer_nodes import AnalysisEvalOutput

    output = AnalysisEvalOutput(
        winner={"label": "test", "accuracy": 0.5, "hits": 1, "total": 2,
                "improved": False, "candidates_evaluated": 1},
        winner_prompt_state={},
        winner_accuracy=0.5,
        improved=False,
    )
    assert output.next_action == "generate"


# ---------------------------------------------------------------------------
# Result structure test
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_result_structure(monkeypatch, eval_data, cycle_config):
    """CycleResult has all expected fields with correct types."""
    _apply_init_mock(monkeypatch)
    _apply_llm_mock(monkeypatch)
    _apply_grow_mock(monkeypatch)

    async def mock_eval(ps, data, backend_client, **kwargs):
        results = [
            {"query": d["query"], "predicted": "WRONG",
             "ground_truth": d["ground_truth"], "hit": False,
             "score": 0.0, "error": None}
            for d in data
        ]
        scores = {"hits": 0, "total": len(data), "accuracy": 0.0, "errors": 0}
        return results, scores, False

    monkeypatch.setattr(
        "api.services.prompt_eval.evaluate_prompt_cached",
        mock_eval,
    )

    result = await run_feedback_cycle(
        instruction="Test.", eval_data=eval_data, config=cycle_config,
    )

    assert isinstance(result.rounds, list)
    assert isinstance(result.n_rounds, int)
    assert isinstance(result.best_accuracy, float)
    assert isinstance(result.stop_reason, str)
    assert result.started_at < result.finished_at
    assert result.winner_prompt_state  # non-empty dict

    for rd in result.rounds:
        assert isinstance(rd.round, int)
        assert isinstance(rd.accuracy, float)
        assert isinstance(rd.next_action, str)
        assert isinstance(rd.prompt_state, dict)
