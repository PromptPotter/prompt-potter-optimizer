"""Tests for feedback cycling (WP 3.4) and scan winner selection.

Covers:
- Multi-round optimization with improving candidates
- Patience-based stopping (consecutive non-improvements)
- Perfect score early exit
- next_action routing from analysis node
- AnalysisEvalOutput.next_action field
- select_scan_winner greedy composition from OAT scan results
"""

import pytest

import pandas as pd

from api.models.prompt_state import PromptState
from api.services.campaign.feedback_cycle import (
    CycleConfig,
    cycle_config_identity,
    run_feedback_cycle,
)
from api.services.search import select_scan_winner

from _helpers import (
    apply_eval_mock,
    apply_grow_mock,
    apply_init_mock,
    apply_llm_mock,
)


# ---------------------------------------------------------------------------
# Multi-round improving test
# ---------------------------------------------------------------------------


@pytest.mark.slow
@pytest.mark.asyncio
async def test_multi_round_improvement(monkeypatch, eval_data, cycle_config):
    """Feedback cycle improves accuracy over multiple rounds."""
    apply_init_mock(monkeypatch)
    apply_llm_mock(monkeypatch)
    apply_grow_mock(monkeypatch)

    # Hits improve each round: 1/3 → 2/3 → 3/3 (perfect, stops early)
    apply_eval_mock(monkeypatch, round_hits=[1, 2, 3])

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


@pytest.mark.slow
@pytest.mark.asyncio
async def test_patience_exhaustion(monkeypatch, eval_data, cycle_config):
    """Loop stops after patience consecutive non-improvements."""
    apply_init_mock(monkeypatch)
    apply_llm_mock(monkeypatch)
    apply_grow_mock(monkeypatch)

    # All candidates return 0% — never beats baseline (which is also 0%)
    apply_eval_mock(monkeypatch, round_hits=[0])

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


@pytest.mark.slow
@pytest.mark.asyncio
async def test_max_rounds(monkeypatch, eval_data):
    """Loop stops at max_rounds even with continuous improvement."""
    apply_init_mock(monkeypatch)
    apply_llm_mock(monkeypatch)
    apply_grow_mock(monkeypatch)

    # Slow steady improvement: 1/3, 1/3, 2/3 hits — never reaches perfect or stalls
    apply_eval_mock(monkeypatch, round_hits=[1, 1, 2])

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


@pytest.mark.slow
@pytest.mark.asyncio
async def test_next_action_stop(monkeypatch, eval_data, cycle_config):
    """Loop stops when AnalysisEvalNode outputs next_action='stop'."""
    apply_init_mock(monkeypatch)
    apply_llm_mock(monkeypatch)
    apply_grow_mock(monkeypatch)

    # First candidate gets 33%, analysis suggestions say stop
    apply_eval_mock(monkeypatch, round_hits=[1])

    # Wrap evaluate_and_select_winner to force next_action="stop"
    from api.services.prompt_optimizer import evaluate_and_select_winner

    original_eval = evaluate_and_select_winner

    async def patched_eval(*args, **kwargs):
        result = await original_eval(*args, **kwargs)
        result["next_action"] = "stop"
        return result

    monkeypatch.setattr(
        "api.services.prompt_optimizer.evaluate_and_select_winner",
        patched_eval,
    )

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


@pytest.mark.slow
@pytest.mark.asyncio
async def test_result_structure(monkeypatch, eval_data, cycle_config):
    """CycleResult has all expected fields with correct types."""
    apply_init_mock(monkeypatch)
    apply_llm_mock(monkeypatch)
    apply_grow_mock(monkeypatch)
    apply_eval_mock(monkeypatch, round_hits=[0])

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


# ---------------------------------------------------------------------------
# Baseline acceptance test (skip InitNode)
# ---------------------------------------------------------------------------


@pytest.mark.slow
@pytest.mark.asyncio
async def test_baseline_acceptance(monkeypatch, eval_data, cycle_config):
    """Feedback cycle skips InitNode when baseline_prompt_state is provided."""
    apply_llm_mock(monkeypatch)
    apply_grow_mock(monkeypatch)

    from api.models.prompt_state import PromptState

    # Create a pre-existing baseline
    baseline_ps = PromptState(
        instruction="Pre-existing baseline",
        persona="Expert pharmacologist",
        changes_description="manual_baseline",
    )

    # All candidates score 0% → patience stops after 2 rounds
    apply_eval_mock(monkeypatch, round_hits=[0])

    # Do NOT mock restructure_context — it should NOT be called
    result = await run_feedback_cycle(
        instruction="",
        eval_data=eval_data,
        config=cycle_config,
        baseline_prompt_state=baseline_ps.model_dump(),
        baseline_accuracy=0.5,
        baseline_results=[{"query": "test", "hit": True}],
    )

    # Should have run without calling InitNode
    assert result.n_rounds == 2  # patience=2, all 0% < 0.5 baseline
    assert result.stop_reason == "patience_exhausted"


# ---------------------------------------------------------------------------
# Results tracking test (failure analysis data flows between rounds)
# ---------------------------------------------------------------------------


@pytest.mark.slow
@pytest.mark.asyncio
async def test_results_tracked_across_rounds(monkeypatch, eval_data, cycle_config):
    """Winner results are passed forward to next round's GrowFilterNode."""
    apply_init_mock(monkeypatch)
    apply_llm_mock(monkeypatch)

    # Track what results GrowFilterNode receives each round
    grow_results_received = []

    async def mock_generate(current_ps, accuracy, results, n, creativity,
                            llm_client, **kwargs):
        grow_results_received.append(list(results))
        return [
            current_ps.derive(
                instruction=f"variant_{i}",
                changes_description=f"gen_{i}",
            )
            for i in range(n)
        ]

    monkeypatch.setattr(
        "api.services.prompt_optimizer.generate_candidates",
        mock_generate,
    )

    # First candidate always gets 1/3 accuracy with real results
    apply_eval_mock(monkeypatch, round_hits=[1])

    result = await run_feedback_cycle(
        instruction="Test.", eval_data=eval_data, config=cycle_config,
    )

    # Round 0: starts with empty results (InitNode has no eval)
    assert grow_results_received[0] == []

    # Round 1+: should have winner results from previous round
    for i in range(1, len(grow_results_received)):
        assert len(grow_results_received[i]) > 0, (
            f"Round {i} should have received results from previous round"
        )

    # CycleRoundResult.results should also be populated
    for rd in result.rounds:
        assert isinstance(rd.results, list)
        assert len(rd.results) > 0


# ---------------------------------------------------------------------------
# on_round_complete callback test
# ---------------------------------------------------------------------------


@pytest.mark.slow
@pytest.mark.asyncio
async def test_on_round_complete_callback(monkeypatch, eval_data, cycle_config):
    """on_round_complete is called after each round with correct args."""
    apply_init_mock(monkeypatch)
    apply_llm_mock(monkeypatch)
    apply_grow_mock(monkeypatch)
    apply_eval_mock(monkeypatch, round_hits=[0])

    callbacks = []

    def on_round(round_result, stall_count):
        callbacks.append((round_result.round, stall_count))

    result = await run_feedback_cycle(
        instruction="Test.", eval_data=eval_data, config=cycle_config,
        on_round_complete=on_round,
    )

    assert len(callbacks) == result.n_rounds
    # Each callback should have incrementing stall_count
    for i, (round_num, stall) in enumerate(callbacks):
        assert round_num == i
        assert stall == i + 1  # All rounds are non-improving


# ---------------------------------------------------------------------------
# select_scan_winner tests
# ---------------------------------------------------------------------------


class TestSelectScanWinner:
    """Tests for select_scan_winner greedy composition."""

    @pytest.fixture
    def baseline_sp(self):
        from api.models.search_point import SearchPoint
        ps = PromptState(
            instruction="Rank candidates",
            thinking_style="baseline_style",
            task_intent="baseline_intent",
        )
        return SearchPoint(prompt_state=ps)

    @pytest.fixture
    def scan_variants(self):
        return {
            "thinking_style": [
                "baseline_style",
                "step by step",
                "analytical reasoning",
                "comparative analysis",
            ],
            "task_intent": [
                "baseline_intent",
                "match materials",
                "rank by relevance",
            ],
            "ranking_temperature": [0.0, 0.3, 0.7],
        }

    @pytest.fixture
    def scan_df(self):
        return pd.DataFrame([
            # thinking_style: variant idx 2 is best (+10%)
            {"axis": "thinking_style", "axis_type": "prompt_field",
             "value_idx": 0, "accuracy": 0.50, "delta": 0.0},
            {"axis": "thinking_style", "axis_type": "prompt_field",
             "value_idx": 1, "accuracy": 0.55, "delta": 0.05},
            {"axis": "thinking_style", "axis_type": "prompt_field",
             "value_idx": 2, "accuracy": 0.60, "delta": 0.10},
            {"axis": "thinking_style", "axis_type": "prompt_field",
             "value_idx": 3, "accuracy": 0.45, "delta": -0.05},
            # task_intent: no improvement
            {"axis": "task_intent", "axis_type": "prompt_field",
             "value_idx": 0, "accuracy": 0.50, "delta": 0.0},
            {"axis": "task_intent", "axis_type": "prompt_field",
             "value_idx": 1, "accuracy": 0.48, "delta": -0.02},
            {"axis": "task_intent", "axis_type": "prompt_field",
             "value_idx": 2, "accuracy": 0.50, "delta": 0.0},
            # ranking_temperature: variant idx 1 is best (+5%)
            {"axis": "ranking_temperature", "axis_type": "pipeline_param",
             "value_idx": 0, "accuracy": 0.50, "delta": 0.0},
            {"axis": "ranking_temperature", "axis_type": "pipeline_param",
             "value_idx": 1, "accuracy": 0.55, "delta": 0.05},
            {"axis": "ranking_temperature", "axis_type": "pipeline_param",
             "value_idx": 2, "accuracy": 0.45, "delta": -0.05},
        ])

    @pytest.fixture
    def axis_profiles(self):
        return [
            {"axis": "thinking_style", "axis_type": "prompt_field",
             "cardinality": 4, "sensitivity_range": 0.15,
             "best_delta": 0.10, "exploration_budget": "high"},
            {"axis": "task_intent", "axis_type": "prompt_field",
             "cardinality": 3, "sensitivity_range": 0.02,
             "best_delta": 0.0, "exploration_budget": "medium"},
            {"axis": "ranking_temperature", "axis_type": "pipeline_param",
             "cardinality": 3, "sensitivity_range": 0.10,
             "best_delta": 0.05, "exploration_budget": "medium"},
        ]

    def test_picks_best_per_improving_axis(
        self, scan_df, axis_profiles, baseline_sp, scan_variants,
    ):
        best_sp = select_scan_winner(
            scan_df, axis_profiles, baseline_sp, scan_variants,
        )
        # thinking_style should be "analytical reasoning" (idx 2)
        assert best_sp.prompt_state.thinking_style == "analytical reasoning"
        # task_intent unchanged (best_delta=0)
        assert best_sp.prompt_state.task_intent == "baseline_intent"
        # ranking_temperature should be 0.3 (idx 1)
        assert best_sp.pipeline_params["ranking_temperature"] == 0.3

    def test_no_improving_axes(self, baseline_sp, scan_variants):
        """When no axes improve, returns baseline unchanged."""
        scan_df = pd.DataFrame([
            {"axis": "thinking_style", "axis_type": "prompt_field",
             "value_idx": 0, "accuracy": 0.50, "delta": 0.0},
        ])
        profiles = [
            {"axis": "thinking_style", "axis_type": "prompt_field",
             "cardinality": 1, "sensitivity_range": 0.0,
             "best_delta": 0.0, "exploration_budget": "skip"},
        ]
        best_sp = select_scan_winner(
            scan_df, profiles, baseline_sp, scan_variants,
        )
        assert best_sp.prompt_state.id == baseline_sp.prompt_state.id
        assert best_sp.pipeline_params is None

    def test_skipped_axes_excluded(
        self, scan_df, baseline_sp, scan_variants,
    ):
        """Axes with exploration_budget='skip' are excluded even if best_delta > 0."""
        profiles = [
            {"axis": "thinking_style", "axis_type": "prompt_field",
             "cardinality": 4, "sensitivity_range": 0.15,
             "best_delta": 0.10, "exploration_budget": "skip"},
        ]
        best_sp = select_scan_winner(
            scan_df, profiles, baseline_sp, scan_variants,
        )
        assert best_sp.prompt_state.id == baseline_sp.prompt_state.id

    def test_pipeline_params_merged(
        self, scan_df, axis_profiles, scan_variants,
    ):
        """Existing pipeline params are preserved and new ones merged in."""
        from api.models.search_point import SearchPoint
        ps = PromptState(
            instruction="Rank candidates",
            thinking_style="baseline_style",
            task_intent="baseline_intent",
        )
        sp = SearchPoint(
            prompt_state=ps,
            pipeline_params={"existing_key": "keep_me"},
        )
        best_sp = select_scan_winner(
            scan_df, axis_profiles, sp, scan_variants,
        )
        assert best_sp.pipeline_params["existing_key"] == "keep_me"
        assert best_sp.pipeline_params["ranking_temperature"] == 0.3


# ---------------------------------------------------------------------------
# Resume & Persistence (moved from test_cycle_resume.py)
# ---------------------------------------------------------------------------


class TestCycleConfigIdentity:
    def test_differs_on_config_change(self, eval_data):
        """Different config fields produce different cycle_ids."""
        rendered = "You are an expert."
        c1 = CycleConfig(
            max_rounds=5, patience=2, n_variants=3, creativity=0.5,
            backend_url="http://mock:8000",
        )
        c2 = CycleConfig(
            max_rounds=10, patience=2, n_variants=3, creativity=0.5,
            backend_url="http://mock:8000",
        )
        assert cycle_config_identity(c1, rendered, eval_data) != \
            cycle_config_identity(c2, rendered, eval_data)

    def test_differs_on_baseline_change(self, cycle_config, eval_data):
        """Different baseline rendered prompt produces different cycle_id."""
        id1 = cycle_config_identity(cycle_config, "prompt A", eval_data)
        id2 = cycle_config_identity(cycle_config, "prompt B", eval_data)
        assert id1 != id2

    def test_eval_order_invariant(self, cycle_config):
        """Eval data order doesn't affect the hash."""
        rendered = "test"
        data_a = [
            {"query": "z_last", "ground_truth": "Z"},
            {"query": "a_first", "ground_truth": "A"},
        ]
        data_b = [
            {"query": "a_first", "ground_truth": "A"},
            {"query": "z_last", "ground_truth": "Z"},
        ]
        assert cycle_config_identity(cycle_config, rendered, data_a) == \
            cycle_config_identity(cycle_config, rendered, data_b)


@pytest.mark.slow
@pytest.mark.asyncio
async def test_completed_cycle_returns_cached(
    monkeypatch, eval_data, cycle_config, tmp_path,
):
    """Re-running a completed cycle returns the cached result with 0 new evals."""
    apply_init_mock(monkeypatch)
    apply_llm_mock(monkeypatch)
    apply_grow_mock(monkeypatch)
    call_count = apply_eval_mock(monkeypatch, round_hits=[1, 2, 3])

    config = cycle_config.model_copy(update={
        "project_root": str(tmp_path),
        "backend_id": "test_backend",
    })

    result1 = await run_feedback_cycle(
        instruction="Rank candidates.",
        eval_data=eval_data,
        config=config,
    )
    assert result1.stop_reason == "perfect_score"
    evals_after_first = call_count[0]

    result2 = await run_feedback_cycle(
        instruction="Rank candidates.",
        eval_data=eval_data,
        config=config,
    )

    assert result2.cycle_id == result1.cycle_id
    assert result2.best_accuracy == result1.best_accuracy
    assert call_count[0] == evals_after_first


@pytest.mark.slow
@pytest.mark.asyncio
async def test_resume_from_interrupted_cycle(
    monkeypatch, eval_data, tmp_path,
):
    """Aborted cycle resumes from last completed round."""
    apply_init_mock(monkeypatch)
    apply_llm_mock(monkeypatch)
    apply_grow_mock(monkeypatch)
    call_count = apply_eval_mock(monkeypatch, round_hits=[1, 2, 3])

    config = CycleConfig(
        max_rounds=1,
        patience=10,
        n_variants=3,
        creativity=0.5,
        improvement_threshold=0.01,
        backend_url="http://mock:8000",
        generate_suggestions=False,
        project_root=str(tmp_path),
        backend_id="test_backend",
    )

    result1 = await run_feedback_cycle(
        instruction="Rank candidates.",
        eval_data=eval_data,
        config=config,
    )
    assert result1.n_rounds == 1
    evals_after_r1 = call_count[0]

    from api.services.stores.campaign_store import CampaignStore
    store = CampaignStore(tmp_path)
    cycle_id = result1.cycle_id
    store.update("test_backend", cycle_id, {"status": "interrupted"})

    result2 = await run_feedback_cycle(
        instruction="Rank candidates.",
        eval_data=eval_data,
        config=config,
    )

    assert result2.resumed_from_round == 1
    assert result2.cycle_id == cycle_id
    # Replayed rounds re-evaluate from cache (for full display), so evals double
    assert call_count[0] == 2 * evals_after_r1


@pytest.mark.slow
@pytest.mark.asyncio
async def test_interrupt_writes_interrupted_status(
    monkeypatch, eval_data, tmp_path,
):
    """KeyboardInterrupt during eval writes status='interrupted', not 'completed'."""
    apply_init_mock(monkeypatch)
    apply_llm_mock(monkeypatch)
    apply_grow_mock(monkeypatch)

    call_count = [0]

    async def mock_eval_interrupt(search_point, data, ctx=None, **kwargs):
        call_count[0] += 1
        # Round 0: all candidates return 1/3 hits → round completes
        # Round 1: interrupt on first candidate
        if call_count[0] <= 1:
            results = [
                {"query": d["query"],
                 "predicted": d["ground_truth"] if i == 0 else "WRONG",
                 "ground_truth": d["ground_truth"],
                 "hit": i == 0, "score": 1.0 if i == 0 else 0.0, "error": None}
                for i, d in enumerate(data)
            ]
            scores = {"hits": 1, "total": len(data),
                      "accuracy": 1 / len(data),
                      "errors": 0, "token_recall": 0.0,
                      "composite": 1 / len(data)}
            return results, scores, False
        raise KeyboardInterrupt

    monkeypatch.setattr(
        "api.services.prompt_eval.evaluate_prompt_cached", mock_eval_interrupt,
    )

    config = CycleConfig(
        max_rounds=3,
        patience=10,
        n_variants=1,
        creativity=0.5,
        improvement_threshold=0.01,
        backend_url="http://mock:8000",
        generate_suggestions=False,
        project_root=str(tmp_path),
        backend_id="test_backend",
    )

    result = await run_feedback_cycle(
        instruction="Rank candidates.",
        eval_data=eval_data,
        config=config,
    )
    assert result.stop_reason == "interrupted"

    from api.services.stores.campaign_store import CampaignStore
    store = CampaignStore(tmp_path)
    campaign = store.load("test_backend", result.cycle_id)
    assert campaign["status"] == "interrupted"


@pytest.mark.slow
@pytest.mark.asyncio
async def test_mid_round_resume_uses_persisted_candidates(
    monkeypatch, eval_data, tmp_path,
):
    """Abort mid-round, re-run -> GrowFilterNode skipped, same candidates used."""
    apply_init_mock(monkeypatch)
    apply_llm_mock(monkeypatch)

    grow_calls = [0]

    async def mock_generate(current_ps, accuracy, results, n, creativity,
                            llm_client, **kwargs):
        grow_calls[0] += 1
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
    apply_eval_mock(monkeypatch, round_hits=[2, 2])

    config = CycleConfig(
        max_rounds=1,
        patience=2,
        n_variants=3,
        creativity=0.5,
        improvement_threshold=0.01,
        backend_url="http://mock:8000",
        generate_suggestions=False,
        project_root=str(tmp_path),
        backend_id="test_backend",
    )

    result1 = await run_feedback_cycle(
        instruction="Rank candidates.",
        eval_data=eval_data,
        config=config,
    )
    assert grow_calls[0] == 1
    cycle_id = result1.cycle_id

    from api.services.stores.campaign_store import CampaignStore
    store = CampaignStore(tmp_path)
    store.update("test_backend", cycle_id, {"status": "active", "trials": []})

    grow_calls[0] = 0

    result2 = await run_feedback_cycle(
        instruction="Rank candidates.",
        eval_data=eval_data,
        config=config,
    )

    assert grow_calls[0] == 0
    assert result2.cycle_id == cycle_id


@pytest.mark.slow
@pytest.mark.asyncio
async def test_no_store_no_persistence(monkeypatch, eval_data, cycle_config):
    """Without project_root, cycle runs fresh every time with no cycle_id."""
    apply_init_mock(monkeypatch)
    apply_llm_mock(monkeypatch)
    apply_grow_mock(monkeypatch)
    apply_eval_mock(monkeypatch, round_hits=[0])

    result = await run_feedback_cycle(
        instruction="Test.",
        eval_data=eval_data,
        config=cycle_config,
    )

    assert result.cycle_id is None
    assert result.resumed_from_round == 0


# ---------------------------------------------------------------------------
# on_phase callback test
# ---------------------------------------------------------------------------


@pytest.mark.slow
@pytest.mark.asyncio
async def test_on_phase_callback(monkeypatch, eval_data, cycle_config):
    """on_phase is called with init, growth, and analysis_eval enter/exit events."""
    apply_init_mock(monkeypatch)
    apply_llm_mock(monkeypatch)
    apply_grow_mock(monkeypatch)
    apply_eval_mock(monkeypatch, round_hits=[0])

    events = []

    def on_phase(event):
        events.append(event)

    result = await run_feedback_cycle(
        instruction="Test.", eval_data=eval_data, config=cycle_config,
        on_phase=on_phase,
    )

    # Collect (phase, event) pairs
    pairs = [(e.phase, e.event) for e in events]

    # init enter/exit always present
    assert ("init", "enter") in pairs
    assert ("init", "exit") in pairs

    # Each round should have growth + analysis_eval enter/exit
    for round_num in range(result.n_rounds):
        growth_enters = [e for e in events
                         if e.phase == "growth" and e.event == "enter"
                         and e.round == round_num]
        growth_exits = [e for e in events
                        if e.phase == "growth" and e.event == "exit"
                        and e.round == round_num]
        eval_enters = [e for e in events
                       if e.phase == "analysis_eval" and e.event == "enter"
                       and e.round == round_num]
        eval_exits = [e for e in events
                      if e.phase == "analysis_eval" and e.event == "exit"
                      and e.round == round_num]
        assert len(growth_enters) == 1, f"round {round_num}: missing growth enter"
        assert len(growth_exits) == 1, f"round {round_num}: missing growth exit"
        assert len(eval_enters) == 1, f"round {round_num}: missing analysis_eval enter"
        assert len(eval_exits) == 1, f"round {round_num}: missing analysis_eval exit"

    # Verify init enter has expected data keys
    init_enter = next(e for e in events if e.phase == "init" and e.event == "enter")
    assert "max_rounds" in init_enter.data
    assert "eval_data_count" in init_enter.data
    assert init_enter.data["eval_data_count"] == len(eval_data)

    # Verify growth exit has rich candidate info
    growth_exit = next(e for e in events if e.phase == "growth" and e.event == "exit")
    assert "n_candidates" in growth_exit.data
    assert "n_eval_queries" in growth_exit.data
    assert growth_exit.data["n_eval_queries"] == len(eval_data)
    assert "candidates" in growth_exit.data
    candidates = growth_exit.data["candidates"]
    assert isinstance(candidates, list)
    assert len(candidates) > 0
    assert "changed_fields" in candidates[0]
    assert "changes_description" in candidates[0]

    # Verify analysis_eval exit has winner info
    eval_exit = next(e for e in events
                     if e.phase == "analysis_eval" and e.event == "exit")
    assert "winner_accuracy" in eval_exit.data
    assert "improved" in eval_exit.data
