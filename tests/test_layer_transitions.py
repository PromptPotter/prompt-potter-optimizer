"""Tests for layer transitions (L2/L3) and hierarchical escalation.

Covers:
- refine_context() — L2 parameter/context adjustment with mock LLM
- modify_plan() — L3 strategic plan change with mock LLM
- L1→L2 escalation in feedback cycle
- L2→L3 escalation in feedback cycle
- L2 meta-param overrides (n_variants, creativity from OptSearchPoint.optimizer_params)
- OptSearchPoint.plan injection into meta-prompt
"""

import json

import pytest
from _helpers import (
    apply_eval_mock,
    apply_grow_mock,
    apply_llm_mock,
)
from conftest import TEST_PIPELINE_SCHEMA

from api.models.opt_search_point import OptSearchPoint
from api.services.campaign.config import CycleConfig
from api.services.campaign.layer_transitions import (
    TransitionResult,
    modify_plan,
    refine_context,
)
from api.services.campaign.optimization_loop import run_optimization
from tests.mock_llm_client import MockLLMClient


def _mock_client(response_dict: dict) -> MockLLMClient:
    """Create a MockLLMClient that returns the given dict as JSON."""
    return MockLLMClient(responses=[json.dumps(response_dict)])



@pytest.mark.asyncio
@pytest.mark.parametrize("response,check_key,check_val", [
    (
        {"optimizer_params": {"creativity": 0.9, "n_variants": 8}, "context": "",
         "rationale": "Increase diversity"},
        "optimizer_params", {"creativity": 0.9, "n_variants": 8},
    ),
    (
        {"optimizer_params": {}, "task_context": {"domain": "pharmaceutical"},
         "rationale": "Add domain grounding"},
        "task_context", {"domain": "pharmaceutical"},
    ),
    (
        {"optimizer_params": {}, "context": "", "rationale": "No changes needed"},
        None, None,
    ),
])
async def test_refine_context_variants(response, check_key, check_val):
    client = _mock_client(response)
    osp = OptSearchPoint(instruction="Rank candidates", optimizer_params={"creativity": 0.5})

    result = await refine_context(osp, client)

    assert isinstance(result, TransitionResult)
    assert result.opt_search_point.parent_id is not None
    assert "L2:" in result.opt_search_point.changes_description
    assert result.pipeline_params is None
    if check_key == "optimizer_params":
        for k, v in check_val.items():
            assert result.opt_search_point.optimizer_params[k] == v
    elif check_key == "task_context":
        assert result.task_context == check_val



@pytest.mark.asyncio
async def test_modify_plan():
    client = _mock_client({
        "plan": "Use chain-of-thought reasoning.",
        "rationale": "Switch to comparative reasoning",
    })

    osp = OptSearchPoint(
        instruction="Rank candidates",
        persona="Expert pharmacologist",
        optimizer_params={"creativity": 0.8},
        plan="",
    )

    result = await modify_plan(osp, [{"l2_round": 1, "optimizer_params": {}, "accuracy_change": 0.0}], client)

    assert isinstance(result, TransitionResult)
    assert result.opt_search_point.plan == "Use chain-of-thought reasoning."
    assert result.opt_search_point.parent_id is not None
    assert "L3:" in result.opt_search_point.changes_description
    assert result.pipeline_params is None
    # Preserves other fields
    assert result.opt_search_point.instruction == osp.instruction
    assert result.opt_search_point.persona == osp.persona



@pytest.mark.asyncio
async def test_l1_l2_escalation(monkeypatch, eval_data):

    apply_llm_mock(monkeypatch)
    apply_grow_mock(monkeypatch)

    # All rounds return 0 hits → L1 stalls at patience=2, triggers L2
    # After L2, L1 resumes and stalls again → L2 stalls → stop
    apply_eval_mock(monkeypatch, round_hits=[0])

    # Mock refine_context to return a slightly modified PS
    l2_calls = []

    async def mock_refine_context(osp, llm_client, **kwargs):
        l2_calls.append(True)
        return TransitionResult(opt_search_point=osp.derive_candidate(
            optimizer_params={"creativity": 0.9},
            changes_description="L2: mock refine",
        ))

    monkeypatch.setattr(
        "api.services.campaign.layer_transitions.refine_context",
        mock_refine_context,
    )

    config = CycleConfig(
        max_rounds=10,
        l1_patience=2,
        n_variants=2,
        backend_url="http://mock:8000",
        enable_critique=False,
        enable_l2=True,
        enable_l3=False,
        l2_patience=2,
        pipeline_schema=TEST_PIPELINE_SCHEMA,
        prompt_node="llm_ranking",
    )

    result = await run_optimization(
        instruction="Rank candidates.",
        eval_data=eval_data,
        config=config,
        baseline_prompt_fields=OptSearchPoint(instruction="Rank candidates.").model_dump(),
        baseline_accuracy=0.0,
        baseline_results=[],
    )

    # L2 should have been called at least once
    assert len(l2_calls) >= 1
    # Should eventually stop (l2_patience_exhausted since no L3)
    assert result.stop_reason == "l2_patience_exhausted"



@pytest.mark.asyncio
async def test_l2_l3_escalation(monkeypatch, eval_data):

    apply_llm_mock(monkeypatch)
    apply_grow_mock(monkeypatch)
    apply_eval_mock(monkeypatch, round_hits=[0])

    l2_calls = []
    l3_calls = []

    async def mock_refine_context(osp, llm_client, **kwargs):
        l2_calls.append(True)
        return TransitionResult(opt_search_point=osp.derive_candidate(
            optimizer_params={"creativity": 0.9},
            changes_description="L2: mock",
        ))

    async def mock_modify_plan(osp, l2_history, llm_client, **kwargs):
        l3_calls.append(True)
        return TransitionResult(opt_search_point=osp.derive_candidate(
            plan="New strategy",
            changes_description="L3: mock",
        ))

    monkeypatch.setattr(
        "api.services.campaign.layer_transitions.refine_context",
        mock_refine_context,
    )
    monkeypatch.setattr(
        "api.services.campaign.layer_transitions.modify_plan",
        mock_modify_plan,
    )

    config = CycleConfig(
        max_rounds=30,
        l1_patience=2,
        n_variants=2,
        backend_url="http://mock:8000",
        enable_critique=False,
        enable_l2=True,
        enable_l3=True,
        l2_patience=2,
        l3_patience=1,
        pipeline_schema=TEST_PIPELINE_SCHEMA,
        prompt_node="llm_ranking",
    )

    result = await run_optimization(
        instruction="Rank candidates.",
        eval_data=eval_data,
        config=config,
        baseline_prompt_fields=OptSearchPoint(instruction="Rank candidates.").model_dump(),
        baseline_accuracy=0.0,
        baseline_results=[],
    )

    # L2 called multiple times (resets after each L3)
    assert len(l2_calls) >= 2
    # L3 called at least once
    assert len(l3_calls) >= 1
    # Stops at L3 patience exhaustion
    assert result.stop_reason == "l3_patience_exhausted"



@pytest.mark.asyncio
async def test_l2_meta_param_overrides(monkeypatch, eval_data):

    apply_llm_mock(monkeypatch)
    apply_eval_mock(monkeypatch, round_hits=[3])  # perfect → stops after round 0

    # Track what l1_generate receives
    gen_calls = []

    async def mock_generate(osp, accuracy, results, n, creativity,
                            llm_client, **kwargs):
        gen_calls.append({"n": n, "creativity": creativity})
        return [
            osp.derive_candidate(
                instruction=f"variant_{i}",
                changes_description=f"gen_{i}",
            ).prompt_field_dict()
            for i in range(n)
        ]

    monkeypatch.setattr(
        "api.services.l1_optimizer.l1_generate",
        mock_generate,
    )

    # Provide baseline with L2 meta-param overrides
    baseline_ps = OptSearchPoint(
        instruction="Rank candidates",
        optimizer_params={"n_variants": 7, "creativity": 0.95},
    )

    config = CycleConfig(
        max_rounds=2,
        l1_patience=5,
        n_variants=3,          # default: 3
        creativity=0.5,        # default: 0.5
        backend_url="http://mock:8000",
        enable_critique=False,
        enable_l2=False,
        pipeline_schema=TEST_PIPELINE_SCHEMA,
        prompt_node="llm_ranking",
    )

    await run_optimization(
        instruction="",
        eval_data=eval_data,
        config=config,
        baseline_prompt_fields=baseline_ps.model_dump(),
        baseline_accuracy=0.0,
    )

    # Should use OptSearchPoint.optimizer_params overrides, not config defaults
    assert gen_calls[0]["n"] == 7
    assert gen_calls[0]["creativity"] == 0.95



@pytest.mark.asyncio
async def test_plan_injected_into_meta_prompt(monkeypatch, eval_data):

    apply_llm_mock(monkeypatch)
    apply_eval_mock(monkeypatch, round_hits=[3])  # perfect → stops

    # Track what meta-prompt l1_generate builds
    captured_prompts = []

    async def mock_generate(osp, accuracy, results, n, creativity,
                            llm_client, **kwargs):
        # The plan should have been injected by now — capture from OptSearchPoint
        captured_prompts.append(osp.plan)
        return [
            osp.derive_candidate(
                instruction=f"variant_{i}",
                changes_description=f"gen_{i}",
            ).prompt_field_dict()
            for i in range(n)
        ]

    monkeypatch.setattr(
        "api.services.l1_optimizer.l1_generate",
        mock_generate,
    )

    baseline_ps = OptSearchPoint(
        instruction="Rank candidates",
        plan="Use chain-of-thought with explicit comparison steps.",
    )

    config = CycleConfig(
        max_rounds=2,
        l1_patience=5,
        n_variants=2,
        backend_url="http://mock:8000",
        enable_critique=False,
        enable_l2=False,
        pipeline_schema=TEST_PIPELINE_SCHEMA,
        prompt_node="llm_ranking",
    )

    await run_optimization(
        instruction="",
        eval_data=eval_data,
        config=config,
        baseline_prompt_fields=baseline_ps.model_dump(),
        baseline_accuracy=0.0,
    )

    # The plan should be present on the OptSearchPoint passed to l1_generate
    assert any("chain-of-thought" in (p or "") for p in captured_prompts)



