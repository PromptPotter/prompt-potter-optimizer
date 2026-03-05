"""E2E optimization test (WP 3.5).

Integration test that:
1. Runs the single-pass optimizer workflow via WorkflowRunner
2. Records results to the campaign store
3. Verifies workflow output structure, PromptState lineage, and
   campaign persistence
"""

import pytest
from pathlib import Path

from api.core.workflow_runner import WorkflowRunner
from api.models.prompt_state import PromptState
from api.services.project_store import ProjectStore

from _helpers import build_eval_results


WORKFLOW_PATH = Path(__file__).parent.parent / "workflows" / "optimizer_single_pass.yaml"
BACKEND_ID = "e2e-test-backend"


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def eval_data():
    return [
        {"query": "aspirin", "ground_truth": "Aspirin"},
        {"query": "ibuprofen", "ground_truth": "Ibuprofen"},
        {"query": "acetaminophen", "ground_truth": "Acetaminophen"},
        {"query": "naproxen", "ground_truth": "Naproxen"},
    ]


def _apply_all_mocks(monkeypatch):
    """Apply all service mocks for E2E testing."""
    # Mock restructure_context
    async def mock_restructure(context_input, llm_client, **kwargs):
        return {
            "persona": "You are a pharmacology expert.",
            "task_intent": "Match drug names to canonical terms.",
            "problem_description": "Drug name normalization.",
            "instruction": "Rank candidates by relevance to the query.",
            "thinking_style": "Compare each candidate systematically.",
            "answer_format": "Return the best match.",
        }

    monkeypatch.setattr(
        "api.services.search.context.restructure_context",
        mock_restructure,
    )

    # Mock generate_candidates
    async def mock_generate(current_ps, accuracy, results, n, creativity,
                            llm_client, **kwargs):
        return [
            current_ps.derive(
                instruction=f"Match query to canonical drug name (variant {i})",
                changes_description=f"e2e_candidate_{i}",
            )
            for i in range(n)
        ]

    monkeypatch.setattr(
        "api.services.prompt_optimizer.generate_candidates",
        mock_generate,
    )

    # Mock evaluate_prompt_cached — first candidate: 75%, others: 0%
    async def mock_eval(search_point, data, **kwargs):
        label = kwargs.get("label", "")
        if label == "candidate_0":
            results, scores = build_eval_results(data, hits=3)
        else:
            results, scores = build_eval_results(data, hits=0)
        return results, scores, False

    monkeypatch.setattr(
        "api.services.prompt_eval.evaluate_prompt_cached",
        mock_eval,
    )

    # Mock LLM client
    from api.services.llm_client import MockLLMClient
    monkeypatch.setattr(
        "api.services.llm_client.get_llm_client",
        lambda provider=None: MockLLMClient(),
    )


# ---------------------------------------------------------------------------
# E2E test: workflow + campaign registry
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_e2e_workflow_and_campaign_registry(
    monkeypatch, store, eval_data,
):
    """Full E2E: run workflow → record to campaign registry → verify."""
    _apply_all_mocks(monkeypatch)

    # -- 1. Run the single-pass optimizer workflow --
    runner = WorkflowRunner.from_yaml(WORKFLOW_PATH)

    # Override backend_url in evaluate step config
    eval_step = runner.workflow.get_step("evaluate")
    eval_step.config["backend_url"] = "http://mock-backend:8000"

    context = await runner.execute(
        inputs={
            "instruction": "Normalize drug names to their canonical form.",
            "improvement_areas": "Focus on brand-name vs generic mapping.",
            "eval_data": eval_data,
            "initial_accuracy": 0.0,
        },
    )

    # Verify workflow completed
    assert context.status == "completed"
    assert "init" in context.step_outputs
    assert "grow" in context.step_outputs
    assert "evaluate" in context.step_outputs

    # Extract workflow outputs
    final = runner.get_final_outputs(context)
    assert final["improved"] is True
    assert final["winner_accuracy"] == 0.75
    assert final["n_candidates"] == 5

    # -- 2. Record results to campaign registry --
    campaign = store.campaigns.create_campaign(
        BACKEND_ID,
        name="E2E Drug Name Optimization",
        config={"instruction": "Normalize drug names"},
    )
    campaign_id = campaign["campaign_id"]

    # Record baseline trial from init step
    init_out = context.step_outputs["init"]
    store.campaigns.record_trial(
        BACKEND_ID, campaign_id,
        round_num=0,
        prompt_state=init_out["prompt_state"],
        accuracy=0.0,
        hits=0,
        total=len(eval_data),
        label="baseline",
    )

    # Record optimization trial from evaluate step
    eval_out = context.step_outputs["evaluate"]
    store.campaigns.record_trial(
        BACKEND_ID, campaign_id,
        round_num=1,
        prompt_state=eval_out["winner_prompt_state"],
        accuracy=eval_out["winner_accuracy"],
        hits=eval_out["winner"]["hits"],
        total=eval_out["winner"]["total"],
        label="single_pass_winner",
        improved=eval_out["improved"],
        candidates_evaluated=eval_out["winner"]["candidates_evaluated"],
    )

    store.campaigns.complete(BACKEND_ID, campaign_id)

    # -- 3. Verify campaign persistence --
    loaded = store.campaigns.load(BACKEND_ID, campaign_id)
    assert loaded is not None
    assert loaded["status"] == "completed"
    assert loaded["n_trials"] == 2
    assert loaded["best_accuracy"] == 0.75
    assert loaded["baseline_accuracy"] == 0.0

    # Verify trial details on disk
    baseline_trial = store.campaigns.load_trial(BACKEND_ID, campaign_id, 0)
    assert baseline_trial is not None
    assert baseline_trial["accuracy"] == 0.0

    winner_trial = store.campaigns.load_trial(BACKEND_ID, campaign_id, 1)
    assert winner_trial is not None
    assert winner_trial["accuracy"] == 0.75
    assert winner_trial["improved"] is True

    # -- 4. Verify PromptState lineage --
    lineage = store.campaigns.get_lineage(BACKEND_ID, campaign_id)
    assert len(lineage) == 2

    # Baseline has no parent
    assert lineage[0]["parent_prompt_state_id"] is None

    # Winner's parent is the baseline
    baseline_ps_id = lineage[0]["prompt_state_id"]
    assert lineage[1]["parent_prompt_state_id"] == baseline_ps_id

    # Reconstruct and verify PromptState objects
    winner_ps = PromptState(**winner_trial["prompt_state"])
    baseline_ps = PromptState(**baseline_trial["prompt_state"])
    assert winner_ps.parent_id == baseline_ps.id
    assert "variant 0" in winner_ps.instruction  # From mock generate

    # -- 5. Verify full export --
    export = store.campaigns.export(BACKEND_ID, campaign_id)
    assert export is not None
    assert len(export["trials_detail"]) == 2
    assert export["status"] == "completed"

    # -- 6. Verify campaign appears in list --
    all_campaigns = store.campaigns.list_all(BACKEND_ID)
    assert len(all_campaigns) == 1
    assert all_campaigns[0]["name"] == "E2E Drug Name Optimization"


# ---------------------------------------------------------------------------
# E2E test: feedback cycle + campaign store
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_e2e_feedback_cycle_with_registry(
    monkeypatch, store, eval_data,
):
    """Feedback cycle results persist to campaign store."""
    _apply_all_mocks(monkeypatch)

    from api.services.campaign.feedback_cycle import CycleConfig, run_feedback_cycle

    config = CycleConfig(
        max_rounds=3,
        patience=2,
        n_variants=2,
        backend_url="http://mock:8000",
        generate_suggestions=False,
    )

    result = await run_feedback_cycle(
        instruction="Normalize drug names.",
        eval_data=eval_data,
        config=config,
    )

    assert result.n_rounds > 0

    # Record to campaign store
    campaign = store.campaigns.create_campaign(
        BACKEND_ID,
        name="E2E Feedback Cycle",
        config={"max_rounds": 3},
    )
    campaign_id = campaign["campaign_id"]

    # Convert cycle rounds to campaign round format
    rounds_for_registry = []
    for rd in result.rounds:
        ps = PromptState(**rd.prompt_state)
        rounds_for_registry.append({
            "round": rd.round,
            "label": rd.label,
            "prompt_state": ps,
            "accuracy": rd.accuracy,
            "hits": rd.hits,
            "total": rd.total,
            "improved": rd.improved,
            "candidates_evaluated": rd.candidates_evaluated,
        })

    trial_ids = store.campaigns.record_campaign_rounds(
        BACKEND_ID, campaign_id, rounds_for_registry,
    )
    store.campaigns.complete(BACKEND_ID, campaign_id)

    assert len(trial_ids) == result.n_rounds

    loaded = store.campaigns.load(BACKEND_ID, campaign_id)
    assert loaded["status"] == "completed"
    assert loaded["n_trials"] == result.n_rounds


# ---------------------------------------------------------------------------
# E2E test: feedback cycle produces obs files
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_e2e_feedback_cycle_obs_output(
    monkeypatch, tmp_path, eval_data,
):
    """Feedback cycle with project_root writes obs files."""
    _apply_all_mocks(monkeypatch)

    from api.services.campaign.feedback_cycle import CycleConfig, run_feedback_cycle

    project_root = tmp_path
    backend_id = "obs-test-backend"

    # Create minimal backend registration so ProjectStore works
    store = ProjectStore(base_dir=project_root)
    from api.models.backend import BackendConnection
    store.backends.register(BackendConnection(
        id=backend_id, name="Obs Test",
        backend_type="termnorm", base_url="http://mock:8000",
    ))

    config = CycleConfig(
        max_rounds=2,
        patience=2,
        n_variants=2,
        backend_url="http://mock:8000",
        backend_id=backend_id,
        project_root=str(project_root),
        generate_suggestions=False,
    )

    result = await run_feedback_cycle(
        instruction="Normalize drug names.",
        eval_data=eval_data,
        config=config,
    )
    assert result.n_rounds > 0

    obs_root = project_root / backend_id / "obs"

    # events.jsonl exists and has entries
    events_file = obs_root / "langfuse" / "events.jsonl"
    assert events_file.exists(), "events.jsonl not created"
    lines = events_file.read_text().strip().splitlines()
    assert len(lines) > 0, "events.jsonl is empty"

    # traces/ has at least one trace file
    traces_dir = obs_root / "langfuse" / "traces"
    assert traces_dir.exists(), "traces/ directory not created"
    trace_files = list(traces_dir.glob("*.json"))
    assert len(trace_files) >= 1, "No trace files written"

    # experiments/ has at least one experiment directory
    experiments_dir = obs_root / "experiments"
    assert experiments_dir.exists(), "experiments/ directory not created"
    exp_dirs = [d for d in experiments_dir.iterdir() if d.is_dir()]
    assert len(exp_dirs) >= 1, "No experiment directories written"

    # prompts/ has at least one prompt version
    prompts_dir = obs_root / "prompts"
    assert prompts_dir.exists(), "prompts/ directory not created"
    prompt_files = list(prompts_dir.rglob("prompt.txt"))
    assert len(prompt_files) >= 1, "No prompt versions written"
