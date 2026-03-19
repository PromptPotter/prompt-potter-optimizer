"""Tests for optimizer pipeline nodes (M7)."""

import pytest

from api.models.prompt_state import PromptState
from api.nodes.optimizer_nodes import (
    L1EvaluateNode,
    L1EvaluateOutput,
    L1GenerateNode,
    L1GenerateOutput,
    L2RefineNode,
    L2RefineOutput,
    L3ModifyPlanNode,
    L3ModifyPlanOutput,
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
    """All optimizer nodes appear in the registry."""
    from api.nodes import get_node_class

    assert get_node_class("InitNode") is InitNode
    assert get_node_class("L1GenerateNode") is L1GenerateNode
    assert get_node_class("L1EvaluateNode") is L1EvaluateNode
    assert get_node_class("L2RefineNode") is L2RefineNode
    assert get_node_class("L3ModifyPlanNode") is L3ModifyPlanNode
    assert get_node_class("nodes/InitNode") is InitNode
    assert get_node_class("nodes/L2RefineNode") is L2RefineNode


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
# L1GenerateNode
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_grow_filter_node(monkeypatch, baseline_ps, baseline_results):
    """L1GenerateNode generates candidate PromptStates."""
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

    node = L1GenerateNode(
        node_id="test_grow",
        config={"model": "test-model", "n_variants": 3, "creativity": 0.5},
    )
    output = await node.process({
        "prompt_state": baseline_ps.model_dump(),
        "accuracy": 0.67,
        "results": baseline_results,
    })

    assert isinstance(output, L1GenerateOutput)
    assert output.n_generated == 3
    assert len(output.candidates) == 3
    for c_dict in output.candidates:
        ps = PromptState(**c_dict)
        assert ps.parent_id == baseline_ps.id


@pytest.mark.asyncio
async def test_l1_generate_forwards_critique(monkeypatch, baseline_ps, baseline_results):
    """L1GenerateNode forwards critique_text, thinking_styles, and scan_context."""
    received_kwargs = {}

    async def mock_generate(current_ps, accuracy, results, n, creativity,
                            llm_client, **kwargs):
        received_kwargs.update(kwargs)
        return [current_ps.derive(instruction="v0", changes_description="gen_0")]

    monkeypatch.setattr(
        "api.services.prompt_optimizer.generate_candidates",
        mock_generate,
    )

    from api.services.llm_client import MockLLMClient
    monkeypatch.setattr(
        "api.services.llm_client.get_llm_client",
        lambda provider=None: MockLLMClient(),
    )

    node = L1GenerateNode(
        node_id="test_grow",
        config={"model": "test-model", "n_variants": 1, "creativity": 0.5},
    )
    await node.process({
        "prompt_state": baseline_ps.model_dump(),
        "accuracy": 0.67,
        "results": baseline_results,
        "critique_text": "Focus on entity matching.",
        "thinking_styles": ["analytical", "comparative"],
        "scan_context": {"top_axis": "thinking_style"},
    })

    assert received_kwargs["critique_text"] == "Focus on entity matching."
    assert received_kwargs["thinking_styles"] == ["analytical", "comparative"]
    assert received_kwargs["scan_context"] == {"top_axis": "thinking_style"}


# ---------------------------------------------------------------------------
# L1EvaluateNode
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_l1_evaluate_node(
    monkeypatch, baseline_ps, eval_data, baseline_results,
):
    """L1EvaluateNode evaluates candidates and selects a winner."""
    candidate_a = baseline_ps.derive(
        instruction="variant_a", changes_description="better_a",
    )
    candidate_b = baseline_ps.derive(
        instruction="variant_b", changes_description="better_b",
    )

    call_count = [0]

    def mock_eval(search_point, data, ctx=None, **kwargs):
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

    from api.services.prompt_eval import EvalContext
    from api.services.backend_client import BackendClient

    ctx = EvalContext(
        backend_client=BackendClient("http://localhost:8000"),
        store=None,
        backend_id="",
        source="test",
    )

    node = L1EvaluateNode(
        node_id="test_eval",
        config={
            "eval_ctx": ctx,
            "improvement_threshold": 0.01,
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

    assert isinstance(output, L1EvaluateOutput)
    assert output.improved is True
    assert output.winner_accuracy == 1.0
    winner_ps = PromptState(**output.winner_prompt_state)
    assert winner_ps.id == candidate_a.id
    assert call_count[0] == 2
    # New output fields
    assert output.critique_text == ""  # critique not enabled
    assert len(output.thinking_styles) == 3
    assert output.winner_composite > 0


# ---------------------------------------------------------------------------
# L2RefineNode
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_l2_refine_node(monkeypatch, baseline_ps):
    """L2RefineNode wraps refine_context and returns transition fields."""
    from api.services.llm_client import MockLLMClient
    monkeypatch.setattr(
        "api.services.llm_client.get_llm_client",
        lambda provider=None: MockLLMClient(),
    )

    async def mock_refine(current_ps, stalled_rounds, eval_data, llm_client, **kwargs):
        from api.services.campaign.layer_transitions import TransitionResult
        new_ps = current_ps.derive(
            optimizer_params={"n_variants": 7},
            changes_description="L2: test refinement",
        )
        return TransitionResult(
            prompt_state=new_ps,
            task_context={"domain": "test"},
        )

    monkeypatch.setattr(
        "api.services.campaign.layer_transitions.refine_context",
        mock_refine,
    )

    node = L2RefineNode(
        node_id="test_l2",
        config={"model": "test-model", "temperature": 0.3},
    )
    output = await node.process({
        "prompt_state": baseline_ps.model_dump(),
        "stalled_rounds": [{"round": 0, "accuracy": 0.5, "results": []}],
        "eval_data": [{"query": "test", "ground_truth": "Test"}],
    })

    assert isinstance(output, L2RefineOutput)
    ps = PromptState(**output.prompt_state)
    assert ps.optimizer_params.get("n_variants") == 7
    assert output.task_context == {"domain": "test"}
    assert "L2" in output.changes_description


# ---------------------------------------------------------------------------
# L3ModifyPlanNode
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_l3_modify_plan_node(monkeypatch, baseline_ps):
    """L3ModifyPlanNode wraps modify_plan and returns transition fields."""
    from api.services.llm_client import MockLLMClient
    monkeypatch.setattr(
        "api.services.llm_client.get_llm_client",
        lambda provider=None: MockLLMClient(),
    )

    async def mock_modify(current_ps, l2_history, eval_data, llm_client, **kwargs):
        from api.services.campaign.layer_transitions import TransitionResult
        new_ps = current_ps.derive(
            plan="new strategy: chain-of-thought",
            changes_description="L3: strategic shift to CoT",
        )
        return TransitionResult(prompt_state=new_ps)

    monkeypatch.setattr(
        "api.services.campaign.layer_transitions.modify_plan",
        mock_modify,
    )

    node = L3ModifyPlanNode(
        node_id="test_l3",
        config={"model": "test-model", "temperature": 0.5},
    )
    output = await node.process({
        "prompt_state": baseline_ps.model_dump(),
        "l2_history": [{"l2_round": 1, "parameters": {}, "accuracy_change": 0.0}],
        "eval_data": [{"query": "test", "ground_truth": "Test"}],
    })

    assert isinstance(output, L3ModifyPlanOutput)
    ps = PromptState(**output.prompt_state)
    assert ps.plan == "new strategy: chain-of-thought"
    assert output.pipeline_params is None
    assert "L3" in output.changes_description


# ---------------------------------------------------------------------------
# Observability type tests
# ---------------------------------------------------------------------------


def test_node_obs_types():
    """Verify _node_obs_type() returns correct types per node class."""
    assert L1GenerateNode(node_id="t", config={})._node_obs_type() == "generation"
    assert L1EvaluateNode(node_id="t", config={})._node_obs_type() == "span"
    assert L2RefineNode(node_id="t", config={})._node_obs_type() == "generation"
    assert L3ModifyPlanNode(node_id="t", config={})._node_obs_type() == "generation"
    assert InitNode(node_id="t", config={})._node_obs_type() == "generation"


# ---------------------------------------------------------------------------
# Step tracing tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_node_tracing_opt_in(monkeypatch, baseline_ps, baseline_results):
    """When obs + trace_id are set, log_node_step_start/end are called."""
    from unittest.mock import MagicMock

    mock_obs = MagicMock()
    mock_obs.log_node_step_start.return_value = "obs-test-123"

    async def mock_generate(current_ps, accuracy, results, n, creativity,
                            llm_client, **kwargs):
        return [current_ps.derive(instruction="v0", changes_description="gen_0")]

    monkeypatch.setattr(
        "api.services.prompt_optimizer.generate_candidates", mock_generate,
    )
    from api.services.llm_client import MockLLMClient
    monkeypatch.setattr(
        "api.services.llm_client.get_llm_client",
        lambda provider=None: MockLLMClient(),
    )

    node = L1GenerateNode(
        node_id="l1_gen_traced",
        config={"model": "test", "n_variants": 1, "creativity": 0.5},
        obs=mock_obs, trace_id="trace_abc",
    )
    await node.process({
        "prompt_state": baseline_ps.model_dump(),
        "accuracy": 0.5,
        "results": baseline_results,
    })

    mock_obs.log_node_step_start.assert_called_once()
    call_kwargs = mock_obs.log_node_step_start.call_args
    assert call_kwargs[1]["trace_id"] == "trace_abc"
    assert call_kwargs[1]["node_id"] == "l1_gen_traced"
    assert call_kwargs[1]["obs_type"] == "generation"

    mock_obs.log_node_step_end.assert_called_once()
    end_kwargs = mock_obs.log_node_step_end.call_args
    assert end_kwargs[1]["obs_id"] == "obs-test-123"
    assert end_kwargs[1]["error"] is None
    assert end_kwargs[1]["output_data"] is not None


@pytest.mark.asyncio
async def test_node_tracing_on_error(monkeypatch, baseline_ps):
    """When _execute raises, log_node_step_end is called with error set."""
    from unittest.mock import MagicMock

    mock_obs = MagicMock()
    mock_obs.log_node_step_start.return_value = "obs-err-456"

    async def mock_generate_fail(*args, **kwargs):
        raise ValueError("test explosion")

    monkeypatch.setattr(
        "api.services.prompt_optimizer.generate_candidates", mock_generate_fail,
    )
    from api.services.llm_client import MockLLMClient
    monkeypatch.setattr(
        "api.services.llm_client.get_llm_client",
        lambda provider=None: MockLLMClient(),
    )

    node = L1GenerateNode(
        node_id="l1_gen_err",
        config={"model": "test", "n_variants": 1, "creativity": 0.5},
        obs=mock_obs, trace_id="trace_err",
    )

    with pytest.raises(ValueError, match="test explosion"):
        await node.process({
            "prompt_state": baseline_ps.model_dump(),
            "accuracy": 0.5,
            "results": [],
        })

    mock_obs.log_node_step_end.assert_called_once()
    end_kwargs = mock_obs.log_node_step_end.call_args
    assert end_kwargs[1]["obs_id"] == "obs-err-456"
    assert "test explosion" in end_kwargs[1]["error"]
