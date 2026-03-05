"""Tests for optimizer nodes (InitNode, GrowFilterNode, AnalysisEvalNode)."""

import pytest

from api.models.prompt_state import PromptState
from api.nodes.optimizer_nodes import (
    AnalysisEvalNode,
    AnalysisEvalOutput,
    GrowFilterNode,
    GrowFilterOutput,
    InitNode,
    InitNodeOutput,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def baseline_ps():
    return PromptState(
        instruction="Rank candidates by relevance to {{core_concept}}.",
        persona="You are a domain expert.",
        thinking_style="Think step by step.",
        changes_description="test_baseline",
    )


@pytest.fixture
def baseline_results():
    return [
        {"query": "aspirin", "predicted": "Aspirin", "ground_truth": "Aspirin",
         "hit": True, "score": 1.0, "error": None},
        {"query": "ibuprofen", "predicted": "Ibuprofen", "ground_truth": "Ibuprofen",
         "hit": True, "score": 1.0, "error": None},
        {"query": "acetaminophen", "predicted": "Tylenol",
         "ground_truth": "Acetaminophen",
         "hit": False, "score": 0.0, "error": None},
    ]


# ---------------------------------------------------------------------------
# Registration tests
# ---------------------------------------------------------------------------


def test_nodes_registered():
    """All three optimizer nodes appear in the registry."""
    from api.nodes import get_node_class

    assert get_node_class("InitNode") is InitNode
    assert get_node_class("GrowFilterNode") is GrowFilterNode
    assert get_node_class("AnalysisEvalNode") is AnalysisEvalNode
    assert get_node_class("nodes/InitNode") is InitNode


# ---------------------------------------------------------------------------
# InitNode
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_init_node(monkeypatch):
    """InitNode decomposes instruction into Layer 1 fields."""
    fake_fields = {
        "persona": "You are a ranking expert.",
        "task_intent": "Rank candidates.",
        "problem_description": "Term normalization.",
        "instruction": "Rank by relevance.",
        "thinking_style": "Step by step.",
        "answer_format": "JSON array.",
    }

    async def mock_restructure(context_input, llm_client, **kwargs):
        return dict(fake_fields)

    monkeypatch.setattr(
        "api.services.search.context.restructure_context",
        mock_restructure,
    )

    from api.services.llm_client import MockLLMClient
    monkeypatch.setattr(
        "api.services.llm_client.get_llm_client",
        lambda provider=None: MockLLMClient(),
    )

    node = InitNode(node_id="test_init", config={"model": "test-model"})
    output = await node.process({
        "instruction": "Rank candidates by relevance.",
        "improvement_areas": "Focus on entity profiling.",
    })

    assert isinstance(output, InitNodeOutput)
    assert output.prompt_state_id
    assert output.layer1_fields["persona"] == "You are a ranking expert."
    assert len(output.rendered_prompt) > 0
    ps = PromptState(**output.prompt_state)
    assert ps.id == output.prompt_state_id


# ---------------------------------------------------------------------------
# GrowFilterNode
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_grow_filter_node(monkeypatch, baseline_ps, baseline_results):
    """GrowFilterNode generates candidate PromptStates."""
    async def mock_generate(current_ps, accuracy, results, n, creativity,
                            llm_client, **kwargs):
        return [
            current_ps.derive(
                instruction=f"variant_{i}",
                changes_description=f"test_variant_{i}",
            )
            for i in range(n)
        ]

    monkeypatch.setattr(
        "api.services.prompt_optimizer.generate_candidates",
        mock_generate,
    )

    from api.services.llm_client import MockLLMClient
    monkeypatch.setattr(
        "api.services.llm_client.get_llm_client",
        lambda provider=None: MockLLMClient(),
    )

    node = GrowFilterNode(
        node_id="test_grow",
        config={"model": "test-model", "n_variants": 3, "creativity": 0.5},
    )
    output = await node.process({
        "prompt_state": baseline_ps.model_dump(),
        "accuracy": 0.67,
        "results": baseline_results,
    })

    assert isinstance(output, GrowFilterOutput)
    assert output.n_generated == 3
    assert len(output.candidates) == 3
    for c_dict in output.candidates:
        ps = PromptState(**c_dict)
        assert ps.parent_id == baseline_ps.id


# ---------------------------------------------------------------------------
# AnalysisEvalNode
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_analysis_eval_node(
    monkeypatch, baseline_ps, eval_data, baseline_results,
):
    """AnalysisEvalNode evaluates candidates and selects a winner."""
    candidate_a = baseline_ps.derive(
        instruction="variant_a", changes_description="better_a",
    )
    candidate_b = baseline_ps.derive(
        instruction="variant_b", changes_description="better_b",
    )

    call_count = [0]

    async def mock_eval(search_point, data, **kwargs):
        call_count[0] += 1
        if search_point.prompt_state.id == candidate_a.id:
            results = [
                {"query": d["query"], "predicted": d["ground_truth"],
                 "ground_truth": d["ground_truth"], "hit": True,
                 "score": 1.0, "error": None}
                for d in data
            ]
            scores = {"hits": len(data), "total": len(data),
                       "accuracy": 1.0, "errors": 0}
        else:
            results = [
                {"query": d["query"], "predicted": "WRONG",
                 "ground_truth": d["ground_truth"], "hit": False,
                 "score": 0.0, "error": None}
                for d in data
            ]
            results[0]["predicted"] = data[0]["ground_truth"]
            results[0]["hit"] = True
            results[0]["score"] = 1.0
            scores = {"hits": 1, "total": len(data),
                       "accuracy": 1 / len(data), "errors": 0}
        return results, scores, False

    monkeypatch.setattr(
        "api.services.prompt_eval.evaluate_prompt_cached",
        mock_eval,
    )

    node = AnalysisEvalNode(
        node_id="test_eval",
        config={
            "backend_url": "http://localhost:8000",
            "improvement_threshold": 0.01,
            "generate_suggestions": False,
        },
    )

    output = await node.process({
        "candidates": [candidate_a.model_dump(), candidate_b.model_dump()],
        "eval_data": eval_data,
        "current_best": {
            "accuracy": 0.67,
            "prompt_state": baseline_ps.model_dump(),
            "results": baseline_results,
            "label": "baseline",
        },
    })

    assert isinstance(output, AnalysisEvalOutput)
    assert output.improved is True
    assert output.winner_accuracy == 1.0
    winner_ps = PromptState(**output.winner_prompt_state)
    assert winner_ps.id == candidate_a.id
    assert call_count[0] == 2
