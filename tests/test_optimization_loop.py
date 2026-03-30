"""Tests for feedback cycling (WP 3.4) and scan winner selection.

Covers:
- Multi-round optimization with improving candidates
- Patience-based stopping (consecutive non-improvements)
- Perfect score early exit
- next_action routing from analysis node
- L1EvaluateOutput.next_action field
- select_scan_winner greedy composition from OAT scan results
"""

import pandas as pd
import pytest
from _helpers import (
    apply_eval_mock,
    apply_grow_mock,
    apply_llm_mock,
    run_simple_cycle,
)

from api.models.opt_search_point import OptSearchPoint
from api.services.campaign.campaign_lifecycle import (
    _validate_config_match,
    cycle_config_identity,
)
from api.services.campaign.config import CycleConfig
from api.services.campaign.optimization_loop import run_optimization
from api.services.search import select_scan_winner


@pytest.mark.slow
@pytest.mark.asyncio
async def test_multi_round_improvement(monkeypatch, eval_data, cycle_config):
    result = await run_simple_cycle(
        monkeypatch, eval_data, cycle_config, round_hits=[1, 2, 3],
    )
    assert result.n_rounds == 3
    assert result.best_accuracy == 1.0
    assert result.stop_reason == "perfect_score"
    assert result.baseline_accuracy == pytest.approx(1 / 3, abs=0.01)
    assert result.rounds[0].accuracy == pytest.approx(1 / 3, abs=0.01)
    assert result.rounds[1].accuracy == pytest.approx(2 / 3, abs=0.01)
    assert result.rounds[2].accuracy == 1.0


@pytest.mark.slow
@pytest.mark.asyncio
async def test_patience_exhaustion(monkeypatch, eval_data, cycle_config):
    result = await run_simple_cycle(
        monkeypatch, eval_data, cycle_config, round_hits=[0],
    )
    assert result.stop_reason == "patience_exhausted"
    assert result.n_rounds == 2
    assert result.best_accuracy == 0.0


@pytest.mark.slow
@pytest.mark.asyncio
async def test_max_rounds(monkeypatch, eval_data):
    config = CycleConfig(
        max_rounds=3, l1_patience=10, n_variants=2,
        backend_url="http://mock:8000",
        enable_critique=False, enable_l2=False,
    )
    result = await run_simple_cycle(
        monkeypatch, eval_data, config, round_hits=[1, 1, 2],
    )
    assert result.stop_reason == "max_rounds"
    assert result.n_rounds == 3
    assert result.best_accuracy > 0



@pytest.mark.slow
@pytest.mark.asyncio
async def test_result_structure(monkeypatch, eval_data, cycle_config):
    result = await run_simple_cycle(
        monkeypatch, eval_data, cycle_config, round_hits=[0],
    )
    assert result.started_at < result.finished_at
    assert result.winner_prompt_fields


@pytest.mark.slow
@pytest.mark.asyncio
async def test_baseline_acceptance(monkeypatch, eval_data, cycle_config):
    baseline_ps = OptSearchPoint(
        instruction="Pre-existing baseline",
        persona="Expert pharmacologist",
        changes_description="manual_baseline",
    )
    result = await run_simple_cycle(
        monkeypatch, eval_data, cycle_config, round_hits=[0],
        instruction="",
        baseline_prompt_fields=baseline_ps.model_dump(),
        baseline_accuracy=0.5,
        baseline_results=[{"query": "test", "hit": True}],
    )
    assert result.n_rounds == 2  # patience=2, all 0% < 0.5 baseline
    assert result.stop_reason == "patience_exhausted"


@pytest.mark.slow
@pytest.mark.asyncio
async def test_results_tracked_across_rounds(monkeypatch, eval_data, cycle_config):
    apply_llm_mock(monkeypatch)

    # Track what results l1_generate receives each round
    grow_results_received = []

    async def mock_generate(osp, accuracy, results, n, creativity,
                            llm_client, **kwargs):
        grow_results_received.append(list(results))
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

    # First candidate always gets 1/3 accuracy with real results
    apply_eval_mock(monkeypatch, round_hits=[1])

    result = await run_optimization(
        instruction="Test.", eval_data=eval_data, config=cycle_config,
        baseline_prompt_fields=OptSearchPoint(instruction="Test.").model_dump(),
        baseline_accuracy=0.0,
        baseline_results=[],
    )

    # Round 0: starts with empty results (baseline has no eval results)
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


@pytest.mark.slow
@pytest.mark.asyncio
async def test_on_round_complete_callback(monkeypatch, eval_data, cycle_config):
    callbacks = []

    def on_round(round_result, stall_count):
        callbacks.append((round_result.round, stall_count))

    from api.services.campaign.callbacks import CycleCallbacks

    result = await run_simple_cycle(
        monkeypatch, eval_data, cycle_config, round_hits=[0],
        callbacks=CycleCallbacks(on_round_complete=on_round),
    )
    assert len(callbacks) == result.n_rounds
    for i, (round_num, stall) in enumerate(callbacks):
        assert round_num == i
        assert stall == i + 1  # All rounds are non-improving


class TestSelectScanWinner:
    """Tests for select_scan_winner greedy composition."""

    @pytest.fixture
    def baseline_osp(self):
        return OptSearchPoint(
            instruction="Rank candidates",
            thinking_style="baseline_style",
            task_intent="baseline_intent",
        )

    @pytest.fixture
    def baseline_sp(self, baseline_osp):
        return baseline_osp.to_job_search_point()

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
        self, scan_df, axis_profiles, baseline_sp, baseline_osp, scan_variants,
    ):
        best_sp = select_scan_winner(
            scan_df, axis_profiles, baseline_sp, scan_variants,
            baseline_opt=baseline_osp,
        )
        # thinking_style should be "analytical reasoning" (idx 2) — check via rendered prompt
        assert "analytical reasoning" in best_sp.render()
        # task_intent unchanged (best_delta=0) — baseline_intent still in prompt
        assert "baseline_intent" in best_sp.render()
        # ranking_temperature should be 0.3 (idx 1)
        assert best_sp.pipeline_params["ranking_temperature"] == 0.3

    def test_no_improving_axes(self, baseline_sp, baseline_osp, scan_variants):
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
            baseline_opt=baseline_osp,
        )
        # No improvement -> returns baseline unchanged
        assert best_sp.render() == baseline_sp.render()
        assert best_sp.pipeline_params == baseline_sp.pipeline_params

    def test_skipped_axes_excluded(
        self, scan_df, baseline_sp, baseline_osp, scan_variants,
    ):
        """Axes with exploration_budget='skip' are excluded even if best_delta > 0."""
        profiles = [
            {"axis": "thinking_style", "axis_type": "prompt_field",
             "cardinality": 4, "sensitivity_range": 0.15,
             "best_delta": 0.10, "exploration_budget": "skip"},
        ]
        best_sp = select_scan_winner(
            scan_df, profiles, baseline_sp, scan_variants,
            baseline_opt=baseline_osp,
        )
        assert best_sp.render() == baseline_sp.render()

    def test_pipeline_params_merged(
        self, scan_df, axis_profiles, scan_variants,
    ):
        osp = OptSearchPoint(
            instruction="Rank candidates",
            thinking_style="baseline_style",
            task_intent="baseline_intent",
        )
        sp = osp.to_job_search_point(
            base_pipeline_params={"existing_key": "keep_me"},
        )
        best_sp = select_scan_winner(
            scan_df, axis_profiles, sp, scan_variants,
            baseline_opt=osp,
        )
        assert best_sp.pipeline_params["existing_key"] == "keep_me"
        assert best_sp.pipeline_params["ranking_temperature"] == 0.3


class TestCycleConfigIdentity:
    def test_differs_on_config_change(self, eval_data):
        rendered = "You are an expert."
        c1 = CycleConfig(
            max_rounds=5, l1_patience=2, n_variants=3, creativity=0.5,
            backend_url="http://mock:8000",
        )
        c2 = CycleConfig(
            max_rounds=10, l1_patience=2, n_variants=3, creativity=0.5,
            backend_url="http://mock:8000",
        )
        assert cycle_config_identity(c1, rendered, eval_data) != \
            cycle_config_identity(c2, rendered, eval_data)

    def test_differs_on_baseline_change(self, cycle_config, eval_data):
        id1 = cycle_config_identity(cycle_config, "prompt A", eval_data)
        id2 = cycle_config_identity(cycle_config, "prompt B", eval_data)
        assert id1 != id2

    def test_eval_order_invariant(self, cycle_config):
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


class TestValidateConfigMatch:
    """Tests for _validate_config_match (EXPERIMENT_ID invariant)."""

    def test_raises_on_mismatch(self, cycle_config):
        stored = {
            "n_variants": 99,  # data-affecting → mismatch
            "creativity": cycle_config.creativity,
            "improvement_threshold": cycle_config.improvement_threshold,
            "model": cycle_config.model,
            "sample_size": cycle_config.sample_size,
        }
        with pytest.raises(ValueError, match="Config mismatch"):
            _validate_config_match(cycle_config, stored, "test-cycle-id")

    def test_passes_on_match(self, cycle_config):
        stored = {
            "n_variants": cycle_config.n_variants,
            "creativity": cycle_config.creativity,
            "improvement_threshold": cycle_config.improvement_threshold,
            "model": cycle_config.model,
            "sample_size": cycle_config.sample_size,
        }
        _validate_config_match(cycle_config, stored, "test-cycle-id")

    def test_loop_control_fields_allowed_to_differ(self, cycle_config):
        stored = {
            "max_rounds": 99,
            "patience": 99,
            "n_variants": cycle_config.n_variants,
            "creativity": cycle_config.creativity,
            "improvement_threshold": cycle_config.improvement_threshold,
            "model": cycle_config.model,
            "sample_size": cycle_config.sample_size,
        }
        _validate_config_match(cycle_config, stored, "test-cycle-id")


@pytest.mark.slow
@pytest.mark.asyncio
async def test_completed_cycle_returns_cached(
    monkeypatch, eval_data, cycle_config, tmp_path,
):
    apply_llm_mock(monkeypatch)
    apply_grow_mock(monkeypatch)
    call_count = apply_eval_mock(monkeypatch, round_hits=[1, 2, 3])

    config = cycle_config.model_copy(update={
        "project_root": str(tmp_path),
        "backend_id": "test_backend",
    })

    _bl = OptSearchPoint(instruction="Rank candidates.").model_dump()
    result1 = await run_optimization(
        instruction="Rank candidates.",
        eval_data=eval_data,
        config=config,
        baseline_prompt_fields=_bl,
        baseline_accuracy=0.0,
        baseline_results=[],
    )
    assert result1.stop_reason == "perfect_score"
    evals_after_first = call_count[0]

    result2 = await run_optimization(
        instruction="Rank candidates.",
        eval_data=eval_data,
        config=config,
        baseline_prompt_fields=_bl,
        baseline_accuracy=0.0,
        baseline_results=[],
    )

    assert result2.cycle_id == result1.cycle_id
    assert result2.best_accuracy == result1.best_accuracy
    # Replay re-enters the loop: round 0 evals from cache, hits perfect_score
    assert call_count[0] > evals_after_first


@pytest.mark.slow
@pytest.mark.asyncio
async def test_resume_from_interrupted_cycle(
    monkeypatch, eval_data, tmp_path,
):
    apply_llm_mock(monkeypatch)
    apply_grow_mock(monkeypatch)
    call_count = apply_eval_mock(monkeypatch, round_hits=[1, 2, 3])

    config = CycleConfig(
        max_rounds=1,
        l1_patience=10,
        n_variants=3,
        creativity=0.5,
        improvement_threshold=0.01,
        backend_url="http://mock:8000",
        enable_critique=False,
        enable_l2=False,
        project_root=str(tmp_path),
        backend_id="test_backend",
    )

    _bl = OptSearchPoint(instruction="Rank candidates.").model_dump()
    result1 = await run_optimization(
        instruction="Rank candidates.",
        eval_data=eval_data,
        config=config,
        baseline_prompt_fields=_bl,
        baseline_accuracy=0.0,
        baseline_results=[],
    )
    assert result1.n_rounds == 1
    evals_after_r1 = call_count[0]

    from api.services.stores.campaign_store import CampaignStore
    store = CampaignStore(tmp_path)
    cycle_id = result1.cycle_id
    store.update("test_backend", cycle_id, {"status": "interrupted"})

    result2 = await run_optimization(
        instruction="Rank candidates.",
        eval_data=eval_data,
        config=config,
        baseline_prompt_fields=_bl,
        baseline_accuracy=0.0,
        baseline_results=[],
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
        "api.services.prompt_eval.eval_search_point", mock_eval_interrupt,
    )

    config = CycleConfig(
        max_rounds=3,
        l1_patience=10,
        n_variants=1,
        creativity=0.5,
        improvement_threshold=0.01,
        backend_url="http://mock:8000",
        enable_critique=False,
        enable_l2=False,
        project_root=str(tmp_path),
        backend_id="test_backend",
    )

    result = await run_optimization(
        instruction="Rank candidates.",
        eval_data=eval_data,
        config=config,
        baseline_prompt_fields=OptSearchPoint(instruction="Rank candidates.").model_dump(),
        baseline_accuracy=0.0,
        baseline_results=[],
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
    """Abort mid-round, re-run -> candidate generation skipped, same candidates used."""
    apply_llm_mock(monkeypatch)

    grow_calls = [0]

    async def mock_generate(osp, accuracy, results, n, creativity,
                            llm_client, **kwargs):
        grow_calls[0] += 1
        return [
            osp.derive_candidate(
                instruction=f"variant_{i}_acc{accuracy:.0%}",
                changes_description=f"gen_{i}",
            ).prompt_field_dict()
            for i in range(n)
        ]

    monkeypatch.setattr(
        "api.services.l1_optimizer.l1_generate",
        mock_generate,
    )
    apply_eval_mock(monkeypatch, round_hits=[2, 2])

    config = CycleConfig(
        max_rounds=1,
        l1_patience=2,
        n_variants=3,
        creativity=0.5,
        improvement_threshold=0.01,
        backend_url="http://mock:8000",
        enable_critique=False,
        enable_l2=False,
        project_root=str(tmp_path),
        backend_id="test_backend",
    )

    _bl = OptSearchPoint(instruction="Rank candidates.").model_dump()
    result1 = await run_optimization(
        instruction="Rank candidates.",
        eval_data=eval_data,
        config=config,
        baseline_prompt_fields=_bl,
        baseline_accuracy=0.0,
        baseline_results=[],
    )
    assert grow_calls[0] == 1
    cycle_id = result1.cycle_id

    from api.services.stores.campaign_store import CampaignStore
    store = CampaignStore(tmp_path)
    store.update("test_backend", cycle_id, {"status": "active", "trials": []})

    grow_calls[0] = 0

    result2 = await run_optimization(
        instruction="Rank candidates.",
        eval_data=eval_data,
        config=config,
        baseline_prompt_fields=_bl,
        baseline_accuracy=0.0,
        baseline_results=[],
    )

    assert grow_calls[0] == 0
    assert result2.cycle_id == cycle_id


@pytest.mark.slow
@pytest.mark.asyncio
async def test_no_store_no_persistence(monkeypatch, eval_data, cycle_config):
    result = await run_simple_cycle(
        monkeypatch, eval_data, cycle_config, round_hits=[0],
    )
    assert result.cycle_id is None
    assert result.resumed_from_round == 0


@pytest.mark.slow
@pytest.mark.asyncio
async def test_on_phase_callback(monkeypatch, eval_data, cycle_config):
    events = []
    from api.services.campaign.callbacks import CycleCallbacks

    result = await run_simple_cycle(
        monkeypatch, eval_data, cycle_config, round_hits=[0],
        callbacks=CycleCallbacks(on_phase=lambda e: events.append(e)),
    )

    pairs = [(e.phase, e.event) for e in events]
    assert ("init", "enter") in pairs
    assert ("init", "exit") in pairs

    def _count(phase, event, round_num):
        return sum(1 for e in events
                   if e.phase == phase and e.event == event
                   and e.round == round_num)

    for rn in range(result.n_rounds):
        for phase in ("l1_generate", "l1_evaluate"):
            assert _count(phase, "enter", rn) == 1
            assert _count(phase, "exit", rn) == 1

    init_enter = next(e for e in events if e.phase == "init" and e.event == "enter")
    assert init_enter.data["eval_data_count"] == len(eval_data)

    gen_exit = next(e for e in events if e.phase == "l1_generate" and e.event == "exit")
    assert gen_exit.data["n_eval_queries"] == len(eval_data)
    assert len(gen_exit.data["candidates"]) > 0

    eval_exit = next(e for e in events if e.phase == "l1_evaluate" and e.event == "exit")
    assert "winner_accuracy" in eval_exit.data
