"""Tests for optimizer single-pass workflow (WP 3.2).

Verifies workflow YAML loads, dependencies, and end-to-end execution.
"""

import pytest
from pathlib import Path

from api.core.workflow_runner import WorkflowRunner, WorkflowContext
from api.models.prompt_state import PromptState
from api.models.workflow import WorkflowDefinition


WORKFLOW_PATH = Path(__file__).parent.parent / "workflows" / "optimizer_single_pass.yaml"


# ---------------------------------------------------------------------------
# Schema / loading tests
# ---------------------------------------------------------------------------


def test_workflow_schema():
    """Workflow YAML parses, has 3 steps with correct IDs and linear deps."""
    runner = WorkflowRunner.from_yaml(WORKFLOW_PATH)
    wf = runner.workflow
    assert isinstance(wf, WorkflowDefinition)
    assert wf.id == "optimizer_single_pass"
    assert len(wf.steps) == 3
    assert [s.id for s in wf.steps] == ["init", "grow", "evaluate"]

    assert wf.get_step_dependencies("init") == []
    assert wf.get_step_dependencies("grow") == ["init"]
    assert set(wf.get_step_dependencies("evaluate")) == {"grow", "init"}


# ---------------------------------------------------------------------------
# End-to-end execution
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_workflow_end_to_end(monkeypatch, eval_data):
    """Full workflow executes with mocked services, producing valid outputs."""
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

    async def mock_generate(current_ps, accuracy, results, n, creativity,
                            llm_client, **kwargs):
        return [
            current_ps.derive(
                instruction=f"variant_{i}",
                changes_description=f"candidate_{i}",
            )
            for i in range(n)
        ]

    monkeypatch.setattr(
        "api.services.prompt_optimizer.generate_candidates",
        mock_generate,
    )

    async def mock_eval(ps, data, **kwargs):
        label = kwargs.get("label", "")
        if label == "candidate_0":
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

    runner = WorkflowRunner.from_yaml(WORKFLOW_PATH)
    eval_step = runner.workflow.get_step("evaluate")
    eval_step.config["backend_url"] = "http://mock-backend:8000"

    context = await runner.execute(
        inputs={
            "instruction": "Rank candidates by relevance.",
            "improvement_areas": "Focus on entity profiling.",
            "eval_data": eval_data,
            "initial_accuracy": 0.0,
        },
    )

    assert isinstance(context, WorkflowContext)
    assert context.status == "completed"
    assert context.error is None

    init_out = context.step_outputs["init"]
    assert init_out["layer1_fields"]["persona"] == "You are a ranking expert."

    grow_out = context.step_outputs["grow"]
    assert grow_out["n_generated"] == 5

    eval_out = context.step_outputs["evaluate"]
    assert eval_out["improved"] is True
    assert eval_out["winner_accuracy"] == 1.0

    winner_ps = PromptState(**eval_out["winner_prompt_state"])
    baseline_ps = PromptState(**init_out["prompt_state"])
    assert winner_ps.parent_id == baseline_ps.id

    final = runner.get_final_outputs(context)
    assert final["winner_accuracy"] == 1.0
    assert final["improved"] is True


# ---------------------------------------------------------------------------
# Flat baseline fields
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_flat_baseline_fields(monkeypatch):
    """AnalysisEvalNode assembles current_best from flat baseline fields."""
    from api.nodes.optimizer_nodes import AnalysisEvalNode, AnalysisEvalOutput

    baseline_ps = PromptState(
        instruction="test instruction",
        changes_description="flat_baseline_test",
    )
    candidate = baseline_ps.derive(
        instruction="improved",
        changes_description="candidate_variant",
    )

    async def mock_eval(ps, data, **kwargs):
        results = [
            {"query": d["query"], "predicted": d["ground_truth"],
             "ground_truth": d["ground_truth"], "hit": True,
             "score": 1.0, "error": None}
            for d in data
        ]
        scores = {"hits": len(data), "total": len(data),
                  "accuracy": 1.0, "errors": 0}
        return results, scores, False

    monkeypatch.setattr(
        "api.services.prompt_eval.evaluate_prompt_cached",
        mock_eval,
    )

    node = AnalysisEvalNode(
        node_id="test_flat",
        config={
            "backend_url": "http://mock:8000",
            "generate_suggestions": False,
        },
    )

    output = await node.process({
        "candidates": [candidate.model_dump()],
        "eval_data": [{"query": "q1", "ground_truth": "A1"}],
        "baseline_prompt_state": baseline_ps.model_dump(),
        "baseline_accuracy": 0.0,
        "baseline_results": [],
        "baseline_label": "flat_baseline",
    })

    assert isinstance(output, AnalysisEvalOutput)
    assert output.improved is True
    assert output.winner_accuracy == 1.0
