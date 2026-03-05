"""Tests for feedback cycle resume — same config picks up where it left off."""

import pytest

from api.services.campaign.feedback_cycle import (
    CycleConfig,
    cycle_config_identity,
    run_feedback_cycle,
)

from _helpers import (
    apply_eval_mock,
    apply_grow_mock,
    apply_init_mock,
    apply_llm_mock,
)


# ---------------------------------------------------------------------------
# Identity hash tests
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


# ---------------------------------------------------------------------------
# Completed cycle returns cached result
# ---------------------------------------------------------------------------


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


# ---------------------------------------------------------------------------
# Resume from interrupted cycle
# ---------------------------------------------------------------------------


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
    store.update("test_backend", cycle_id, {"status": "active"})

    result2 = await run_feedback_cycle(
        instruction="Rank candidates.",
        eval_data=eval_data,
        config=config,
    )

    assert result2.resumed_from_round == 1
    assert result2.cycle_id == cycle_id
    assert call_count[0] == evals_after_r1


# ---------------------------------------------------------------------------
# Mid-round resume with persisted candidates
# ---------------------------------------------------------------------------


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


# ---------------------------------------------------------------------------
# No store -> no persistence
# ---------------------------------------------------------------------------


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
