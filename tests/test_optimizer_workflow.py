"""Tests for optimizer single-pass workflow (WP 3.2).

Verifies:
- Workflow YAML loads and validates
- End-to-end execution with mocked services
- Data flow between InitNode → GrowFilterNode → AnalysisEvalNode
- Flat baseline field assembly in AnalysisEvalNode
- Final outputs carry full lineage
"""

import pytest
from pathlib import Path

from api.core.workflow_runner import WorkflowRunner, WorkflowContext
from api.models.prompt_state import PromptState
from api.models.workflow import WorkflowDefinition


WORKFLOW_PATH = Path(__file__).parent.parent / "workflows" / "optimizer_single_pass.yaml"


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def workflow_def():
    """Load the optimizer single-pass workflow definition."""
    return WorkflowRunner.from_yaml(WORKFLOW_PATH)


# ---------------------------------------------------------------------------
# Schema / loading tests
# ---------------------------------------------------------------------------


def test_workflow_yaml_loads():
    """Workflow YAML parses into a valid WorkflowDefinition."""
    runner = WorkflowRunner.from_yaml(WORKFLOW_PATH)
    wf = runner.workflow
    assert isinstance(wf, WorkflowDefinition)
    assert wf.id == "optimizer_single_pass"
    assert len(wf.steps) == 3


def test_workflow_step_ids():
    """Steps have expected IDs and node types."""
    runner = WorkflowRunner.from_yaml(WORKFLOW_PATH)
    step_ids = [s.id for s in runner.workflow.steps]
    assert step_ids == ["init", "grow", "evaluate"]

    step_types = [s.node_type for s in runner.workflow.steps]
    assert step_types == ["InitNode", "GrowFilterNode", "AnalysisEvalNode"]


def test_workflow_dependencies():
    """Dependency graph is linear: init → grow → evaluate."""
    runner = WorkflowRunner.from_yaml(WORKFLOW_PATH)
    wf = runner.workflow

    assert wf.get_step_dependencies("init") == []
    assert wf.get_step_dependencies("grow") == ["init"]
    assert set(wf.get_step_dependencies("evaluate")) == {"grow", "init"}


def test_workflow_outputs_defined():
    """Workflow exposes expected output fields."""
    runner = WorkflowRunner.from_yaml(WORKFLOW_PATH)
    output_names = set(runner.workflow.outputs.keys())
    assert "winner_prompt_state" in output_names
    assert "winner_accuracy" in output_names
    assert "improved" in output_names
    assert "candidate_scores" in output_names
    assert "baseline_prompt" in output_names


# ---------------------------------------------------------------------------
# End-to-end execution test
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_workflow_end_to_end(monkeypatch, eval_data):
    """Full workflow executes with mocked services, producing valid outputs."""
    # -- Mock restructure_context (InitNode) --
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

    # -- Mock generate_candidates (GrowFilterNode) --
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

    # -- Mock evaluate_prompt_cached (AnalysisEvalNode) --
    # First candidate gets 100% accuracy, others get 0%
    async def mock_eval(ps, data, backend_client, **kwargs):
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

    # -- Mock LLM client --
    from api.services.llm_client import MockLLMClient
    monkeypatch.setattr(
        "api.services.llm_client.get_llm_client",
        lambda provider=None: MockLLMClient(),
    )

    # -- Load workflow and override backend_url in config --
    runner = WorkflowRunner.from_yaml(WORKFLOW_PATH)
    # Set backend_url on the evaluate step config
    eval_step = runner.workflow.get_step("evaluate")
    eval_step.config["backend_url"] = "http://mock-backend:8000"

    # -- Execute --
    context = await runner.execute(
        inputs={
            "instruction": "Rank candidates by relevance.",
            "improvement_areas": "Focus on entity profiling.",
            "eval_data": eval_data,
            "initial_accuracy": 0.0,
        },
    )

    # -- Verify context --
    assert isinstance(context, WorkflowContext)
    assert context.status == "completed"
    assert context.error is None

    # -- Verify step outputs exist --
    assert "init" in context.step_outputs
    assert "grow" in context.step_outputs
    assert "evaluate" in context.step_outputs

    # -- Verify init outputs --
    init_out = context.step_outputs["init"]
    assert init_out["prompt_state_id"]
    assert init_out["layer1_fields"]["persona"] == "You are a ranking expert."
    assert len(init_out["rendered_prompt"]) > 0

    # -- Verify grow outputs --
    grow_out = context.step_outputs["grow"]
    assert grow_out["n_generated"] == 5
    assert len(grow_out["candidates"]) == 5

    # -- Verify candidate lineage --
    baseline_ps = PromptState(**init_out["prompt_state"])
    for c_dict in grow_out["candidates"]:
        candidate = PromptState(**c_dict)
        assert candidate.parent_id == baseline_ps.id

    # -- Verify evaluate outputs --
    eval_out = context.step_outputs["evaluate"]
    assert eval_out["improved"] is True
    assert eval_out["winner_accuracy"] == 1.0
    assert len(eval_out["candidate_scores"]) == 5

    # Winner should be candidate_0 (100% accuracy)
    winner_ps = PromptState(**eval_out["winner_prompt_state"])
    assert winner_ps.parent_id == baseline_ps.id
    assert winner_ps.instruction == "variant_0"

    # -- Verify final workflow outputs --
    final = runner.get_final_outputs(context)
    assert final["winner_accuracy"] == 1.0
    assert final["improved"] is True
    assert final["n_candidates"] == 5
    assert final["baseline_prompt"]  # non-empty rendered prompt


# ---------------------------------------------------------------------------
# Flat baseline fields test
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

    eval_data = [
        {"query": "q1", "ground_truth": "A1"},
    ]

    # Mock: candidate beats baseline
    async def mock_eval(ps, data, backend_client, **kwargs):
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

    # Use flat fields instead of current_best dict
    output = await node.process({
        "candidates": [candidate.model_dump()],
        "eval_data": eval_data,
        "baseline_prompt_state": baseline_ps.model_dump(),
        "baseline_accuracy": 0.0,
        "baseline_results": [],
        "baseline_label": "flat_baseline",
    })

    assert isinstance(output, AnalysisEvalOutput)
    assert output.improved is True
    assert output.winner_accuracy == 1.0


@pytest.mark.asyncio
async def test_flat_baseline_missing_raises():
    """AnalysisEvalNode raises when neither current_best nor flat fields given."""
    from api.nodes.optimizer_nodes import AnalysisEvalNode

    node = AnalysisEvalNode(
        node_id="test_missing",
        config={"backend_url": "http://mock:8000"},
    )

    with pytest.raises(ValueError, match="current_best.*baseline_prompt_state"):
        await node.process({
            "candidates": [{}],
            "eval_data": [],
        })
