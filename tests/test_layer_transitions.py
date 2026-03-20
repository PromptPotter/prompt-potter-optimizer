"""Tests for layer transitions (L2/L3) and hierarchical escalation.

Covers:
- refine_context() — L2 parameter/context adjustment with mock LLM
- modify_plan() — L3 strategic plan change with mock LLM
- L1→L2 escalation in feedback cycle
- L2→L3 escalation in feedback cycle
- L2 meta-param overrides (n_variants, creativity from PromptState.optimizer_params)
- PromptState.plan injection into meta-prompt
"""

import json

import pytest

from api.models.prompt_state import PromptState
from api.services.campaign.models import CycleConfig
from api.services.campaign.feedback_cycle import run_feedback_cycle
from api.services.campaign.layer_transitions import (
    TransitionResult, modify_plan, refine_context,
)
from api.services.llm_client import MockLLMClient

from _helpers import (
    apply_eval_mock,
    apply_grow_mock,
    apply_llm_mock,
)


def _mock_client(response_dict: dict) -> MockLLMClient:
    """Create a MockLLMClient that returns the given dict as JSON."""
    return MockLLMClient(responses=[json.dumps(response_dict)])


# ---------------------------------------------------------------------------
# Unit tests: refine_context
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_refine_context_applies_parameters():
    """refine_context merges LLM-recommended parameters onto current state."""
    client = _mock_client({
        "optimizer_params": {"creativity": 0.9, "n_variants": 8},
        "context": "",
        "rationale": "Increase diversity to escape local minimum",
    })

    ps = PromptState(instruction="Rank candidates", optimizer_params={"creativity": 0.5})
    stalled_rounds = [
        {"round": 0, "accuracy": 0.4, "results": [
            {"query": "aspirin", "ground_truth": "Aspirin", "predicted": "WRONG", "hit": False},
        ]},
        {"round": 1, "accuracy": 0.4, "results": []},
    ]

    result = await refine_context(ps, stalled_rounds, [], client)

    assert isinstance(result, TransitionResult)
    assert result.prompt_state.parent_id == ps.id
    assert result.prompt_state.optimizer_params["creativity"] == 0.9
    assert result.prompt_state.optimizer_params["n_variants"] == 8
    assert "L2:" in result.prompt_state.changes_description
    assert result.pipeline_params is None


@pytest.mark.asyncio
async def test_refine_context_applies_task_context():
    """refine_context updates task_context when LLM recommends it."""
    client = _mock_client({
        "optimizer_params": {},
        "task_context": {"domain": "pharmaceutical"},
        "rationale": "Add domain grounding",
    })

    _ps = PromptState(instruction="Rank candidates")
    result = await refine_context(
        _ps, [{"round": 0, "accuracy": 0.3, "results": []}], [], client,
        task_context={"domain": "generic"},
    )

    assert result.task_context == {"domain": "pharmaceutical"}
    assert result.prompt_state.parent_id == _ps.id


@pytest.mark.asyncio
async def test_refine_context_no_changes_returns_same():
    """refine_context returns current PS unchanged when LLM suggests nothing."""
    client = _mock_client({
        "optimizer_params": {},
        "context": "",
        "rationale": "No changes needed",
    })

    ps = PromptState(instruction="Rank candidates")

    result = await refine_context(ps, [{"round": 0, "accuracy": 0.5, "results": []}], [], client)

    # Still derives because changes_description is always added
    assert result.prompt_state.parent_id == ps.id
    assert "L2:" in result.prompt_state.changes_description


# ---------------------------------------------------------------------------
# Unit tests: modify_plan
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_modify_plan_sets_new_plan():
    """modify_plan applies a new strategic plan from LLM."""
    client = _mock_client({
        "plan": "Use chain-of-thought reasoning with explicit comparison steps.",
        "rationale": "Switch from direct ranking to comparative reasoning",
    })

    ps = PromptState(instruction="Rank candidates", plan="")
    l2_history = [{"l2_round": 1, "optimizer_params": {}, "accuracy_change": 0.0}]

    result = await modify_plan(ps, l2_history, [], client)

    assert isinstance(result, TransitionResult)
    expected_plan = "Use chain-of-thought reasoning with explicit comparison steps."
    assert result.prompt_state.plan == expected_plan
    assert result.prompt_state.parent_id == ps.id
    assert "L3:" in result.prompt_state.changes_description
    assert result.pipeline_params is None


@pytest.mark.asyncio
async def test_modify_plan_preserves_other_fields():
    """modify_plan only changes plan and changes_description."""
    client = _mock_client({
        "plan": "New strategy",
        "rationale": "Strategic shift",
    })

    ps = PromptState(
        instruction="Rank candidates",
        persona="Expert pharmacologist",
        optimizer_params={"creativity": 0.8},
    )

    result = await modify_plan(ps, [], [], client)

    assert result.prompt_state.instruction == ps.instruction
    assert result.prompt_state.persona == ps.persona
    assert result.prompt_state.optimizer_params == ps.optimizer_params
    assert result.prompt_state.plan == "New strategy"


# ---------------------------------------------------------------------------
# Integration: L1→L2 escalation in feedback cycle
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_l1_l2_escalation(monkeypatch, eval_data):
    """When L1 stalls and enable_l2=True, cycle calls refine_context and continues."""
    apply_llm_mock(monkeypatch)
    apply_grow_mock(monkeypatch)

    # All rounds return 0 hits → L1 stalls at patience=2, triggers L2
    # After L2, L1 resumes and stalls again → L2 stalls → stop
    apply_eval_mock(monkeypatch, round_hits=[0])

    # Mock refine_context to return a slightly modified PS
    l2_calls = []

    async def mock_refine_context(current_ps, stalled_rounds, eval_d, llm_client, **kwargs):
        l2_calls.append({"ps_id": current_ps.id, "n_stalled": len(stalled_rounds)})
        return TransitionResult(prompt_state=current_ps.derive(
            optimizer_params={"creativity": 0.9},
            changes_description="L2: mock refine",
        ))

    monkeypatch.setattr(
        "api.services.campaign.layer_transitions.refine_context",
        mock_refine_context,
    )

    config = CycleConfig(
        max_rounds=10,
        patience=2,
        n_variants=2,
        backend_url="http://mock:8000",
        enable_critique=False,
        enable_l2=True,
        enable_l3=False,
        l2_patience=2,
    )

    result = await run_feedback_cycle(
        instruction="Rank candidates.",
        eval_data=eval_data,
        config=config,
        baseline_prompt_state=PromptState(instruction="Rank candidates.").model_dump(),
        baseline_accuracy=0.0,
        baseline_results=[],
    )

    # L2 should have been called at least once
    assert len(l2_calls) >= 1
    # Should eventually stop (l2_patience_exhausted since no L3)
    assert result.stop_reason == "l2_patience_exhausted"


# ---------------------------------------------------------------------------
# Integration: L2→L3 escalation in feedback cycle
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_l2_l3_escalation(monkeypatch, eval_data):
    """Full L1→L2→L3 escalation chain when all levels stall."""
    apply_llm_mock(monkeypatch)
    apply_grow_mock(monkeypatch)
    apply_eval_mock(monkeypatch, round_hits=[0])

    l2_calls = []
    l3_calls = []

    async def mock_refine_context(current_ps, stalled_rounds, eval_d, llm_client, **kwargs):
        l2_calls.append(current_ps.id)
        return TransitionResult(prompt_state=current_ps.derive(
            optimizer_params={"creativity": 0.9},
            changes_description="L2: mock",
        ))

    async def mock_modify_plan(current_ps, l2_history, eval_d, llm_client, **kwargs):
        l3_calls.append(current_ps.id)
        return TransitionResult(prompt_state=current_ps.derive(
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
        patience=2,
        n_variants=2,
        backend_url="http://mock:8000",
        enable_critique=False,
        enable_l2=True,
        enable_l3=True,
        l2_patience=2,
        l3_patience=1,
    )

    result = await run_feedback_cycle(
        instruction="Rank candidates.",
        eval_data=eval_data,
        config=config,
        baseline_prompt_state=PromptState(instruction="Rank candidates.").model_dump(),
        baseline_accuracy=0.0,
        baseline_results=[],
    )

    # L2 called multiple times (resets after each L3)
    assert len(l2_calls) >= 2
    # L3 called at least once
    assert len(l3_calls) >= 1
    # Stops at L3 patience exhaustion
    assert result.stop_reason == "l3_patience_exhausted"


# ---------------------------------------------------------------------------
# L2 meta-param overrides
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_l2_meta_param_overrides(monkeypatch, eval_data):
    """PromptState.optimizer_params override n_variants and creativity in generation."""
    apply_llm_mock(monkeypatch)
    apply_eval_mock(monkeypatch, round_hits=[3])  # perfect → stops after round 0

    # Track what generate_candidates receives
    gen_calls = []

    async def mock_generate(current_ps, accuracy, results, n, creativity,
                            llm_client, **kwargs):
        gen_calls.append({"n": n, "creativity": creativity})
        return [
            current_ps.derive(
                instruction=f"variant_{i}",
                changes_description=f"gen_{i}",
            ).model_dump()
            for i in range(n)
        ]

    monkeypatch.setattr(
        "api.services.prompt_optimizer.generate_candidates",
        mock_generate,
    )

    # Provide baseline with L2 meta-param overrides
    baseline_ps = PromptState(
        instruction="Rank candidates",
        optimizer_params={"n_variants": 7, "creativity": 0.95},
    )

    config = CycleConfig(
        max_rounds=2,
        patience=5,
        n_variants=3,          # default: 3
        creativity=0.5,        # default: 0.5
        backend_url="http://mock:8000",
        enable_critique=False,
        enable_l2=False,
    )

    await run_feedback_cycle(
        instruction="",
        eval_data=eval_data,
        config=config,
        baseline_prompt_state=baseline_ps.model_dump(),
        baseline_accuracy=0.0,
    )

    # Should use PromptState.optimizer_params overrides, not config defaults
    assert gen_calls[0]["n"] == 7
    assert gen_calls[0]["creativity"] == 0.95


# ---------------------------------------------------------------------------
# PromptState.plan injection into meta-prompt
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_plan_injected_into_meta_prompt(monkeypatch, eval_data):
    """PromptState.plan is appended to meta-prompt during candidate generation."""
    apply_llm_mock(monkeypatch)
    apply_eval_mock(monkeypatch, round_hits=[3])  # perfect → stops

    # Track what meta-prompt generate_candidates builds
    captured_prompts = []

    async def mock_generate(current_ps, accuracy, results, n, creativity,
                            llm_client, **kwargs):
        # The plan should have been injected by now — capture the PS
        captured_prompts.append(current_ps.plan)
        return [
            current_ps.derive(
                instruction=f"variant_{i}",
                changes_description=f"gen_{i}",
            ).model_dump()
            for i in range(n)
        ]

    monkeypatch.setattr(
        "api.services.prompt_optimizer.generate_candidates",
        mock_generate,
    )

    baseline_ps = PromptState(
        instruction="Rank candidates",
        plan="Use chain-of-thought with explicit comparison steps.",
    )

    config = CycleConfig(
        max_rounds=2,
        patience=5,
        n_variants=2,
        backend_url="http://mock:8000",
        enable_critique=False,
        enable_l2=False,
    )

    await run_feedback_cycle(
        instruction="",
        eval_data=eval_data,
        config=config,
        baseline_prompt_state=baseline_ps.model_dump(),
        baseline_accuracy=0.0,
    )

    # The plan should be present on the PromptState passed to generate_candidates
    assert any("chain-of-thought" in (p or "") for p in captured_prompts)


# ---------------------------------------------------------------------------
# Pipeline params in TransitionResult
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_refine_context_ignores_pipeline_params():
    """refine_context does NOT return pipeline_params (L1's job)."""
    client = _mock_client({
        "optimizer_params": {"creativity": 0.5},
        "context": "Updated context",
        "pipeline_params": {"llm_ranking": {"temperature": 0.5}},
        "rationale": "L2 focuses on context, not pipeline_params",
    })

    ps = PromptState(instruction="Rank candidates")
    result = await refine_context(
        ps, [{"round": 0, "accuracy": 0.4, "results": []}], [], client,
        pipeline_params={"llm_ranking": {"temperature": 0.3}},
    )

    # L2 no longer sets pipeline_params — only context + meta-settings
    assert result.pipeline_params is None
    assert result.prompt_state.optimizer_params.get("creativity") == 0.5


@pytest.mark.asyncio
async def test_modify_plan_with_pipeline_params():
    """modify_plan returns pipeline_params when LLM suggests them."""
    client = _mock_client({
        "plan": "Focus on fuzzy matching",
        "pipeline_params": {"fuzzy_matching": {"threshold": 0.6}},
        "rationale": "Lower fuzzy threshold",
    })

    ps = PromptState(instruction="Rank candidates")
    result = await modify_plan(
        ps, [], [], client,
        pipeline_params={"fuzzy_matching": {"threshold": 0.8}},
    )

    assert result.prompt_state.plan == "Focus on fuzzy matching"
    assert result.pipeline_params is not None
    assert result.pipeline_params["fuzzy_matching"]["threshold"] == 0.6
