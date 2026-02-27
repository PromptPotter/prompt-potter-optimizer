"""Tests for feedback cycle resume — same config picks up where it left off.

Covers:
- cycle_config_identity stability and sensitivity
- Eval data order invariance
- Completed cycle returns cached result (zero new evals)
- Interrupted cycle resumes from last completed round
- Mid-round resume with persisted candidates
- No store → runs fresh every time (backward compat)
"""

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
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def base_config():
    return CycleConfig(
        max_rounds=5,
        patience=2,
        n_variants=3,
        creativity=0.5,
        improvement_threshold=0.01,
        backend_url="http://mock:8000",
        generate_suggestions=False,
    )


# ---------------------------------------------------------------------------
# Identity hash tests
# ---------------------------------------------------------------------------


class TestCycleConfigIdentity:
    def test_stable(self, base_config, eval_data):
        """Same inputs produce the same cycle_id."""
        rendered = "You are an expert."
        id1 = cycle_config_identity(base_config, rendered, eval_data)
        id2 = cycle_config_identity(base_config, rendered, eval_data)
        assert id1 == id2
        assert id1.startswith("cycle_")
        assert len(id1) == len("cycle_") + 12

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

    def test_differs_on_baseline_change(self, base_config, eval_data):
        """Different baseline rendered prompt produces different cycle_id."""
        id1 = cycle_config_identity(base_config, "prompt A", eval_data)
        id2 = cycle_config_identity(base_config, "prompt B", eval_data)
        assert id1 != id2

    def test_eval_order_invariant(self, base_config):
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
        assert cycle_config_identity(base_config, rendered, data_a) == \
            cycle_config_identity(base_config, rendered, data_b)

    def test_infra_fields_excluded(self, eval_data):
        """backend_url, project_root, backend_id don't affect identity."""
        rendered = "test"
        c1 = CycleConfig(
            max_rounds=5, patience=2, n_variants=3,
            backend_url="http://host-a:8000",
            project_root="/path/a",
            backend_id="id_a",
        )
        c2 = CycleConfig(
            max_rounds=5, patience=2, n_variants=3,
            backend_url="http://host-b:9000",
            project_root="/path/b",
            backend_id="id_b",
        )
        assert cycle_config_identity(c1, rendered, eval_data) == \
            cycle_config_identity(c2, rendered, eval_data)


# ---------------------------------------------------------------------------
# Completed cycle returns cached result
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_completed_cycle_returns_cached(
    monkeypatch, eval_data, base_config, tmp_path,
):
    """Re-running a completed cycle returns the cached result with 0 new evals."""
    apply_init_mock(monkeypatch)
    apply_llm_mock(monkeypatch)
    apply_grow_mock(monkeypatch)
    call_count = apply_eval_mock(monkeypatch, round_hits=[1, 2, 3])

    # First run — writes to disk, runs 3 rounds (perfect score at round 2)
    config = base_config.model_copy(update={
        "project_root": str(tmp_path),
        "backend_id": "test_backend",
    })

    result1 = await run_feedback_cycle(
        instruction="Rank candidates.",
        eval_data=eval_data,
        config=config,
    )
    assert result1.stop_reason == "perfect_score"
    assert result1.cycle_id is not None
    evals_after_first = call_count[0]

    # Second run — same config, should return cached result immediately
    result2 = await run_feedback_cycle(
        instruction="Rank candidates.",
        eval_data=eval_data,
        config=config,
    )

    assert result2.cycle_id == result1.cycle_id
    assert result2.n_rounds == result1.n_rounds
    assert result2.best_accuracy == result1.best_accuracy
    assert result2.stop_reason == result1.stop_reason
    # No new evals ran
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

    # Round 0: 1/3 hit, round 1: 2/3 hits, round 2: 3/3 hits
    call_count = apply_eval_mock(monkeypatch, round_hits=[1, 2, 3])

    config = CycleConfig(
        max_rounds=5,
        patience=10,
        n_variants=3,
        creativity=0.5,
        improvement_threshold=0.01,
        backend_url="http://mock:8000",
        generate_suggestions=False,
        project_root=str(tmp_path),
        backend_id="test_backend",
    )

    # First run: abort after round 0 by setting max_rounds=1
    config_r1 = config.model_copy(update={"max_rounds": 1})
    result1 = await run_feedback_cycle(
        instruction="Rank candidates.",
        eval_data=eval_data,
        config=config_r1,
    )
    assert result1.n_rounds == 1
    assert result1.stop_reason == "max_rounds"
    evals_after_r1 = call_count[0]

    # Second run: same config except max_rounds=5 → different identity
    # To test resume, we need the same identity. Use the same max_rounds.
    # Instead, let's manually check the resume path by using the same config
    # but NOT marking it completed (max_rounds > completed rounds).
    #
    # Actually, the first run with max_rounds=1 will be marked "completed".
    # To simulate an interrupt, we need to un-complete it.
    from api.services.stores.campaign_store import CampaignStore
    store = CampaignStore(tmp_path)
    cycle_id = result1.cycle_id
    assert cycle_id is not None

    # Revert status to "active" so next run sees it as in-progress
    store.update("test_backend", cycle_id, {"status": "active"})

    # Now re-run with the SAME config (max_rounds=1). It will see 1 completed
    # round and start_round=1 which equals max_rounds → loop doesn't execute.
    # Use a larger max_rounds to actually test continuation.
    # We need the same identity → same config fields. But max_rounds is in the hash.
    # So let's use max_rounds=1 again — the loop range(1, 1) won't run any rounds
    # and we get the resumed state.
    result2 = await run_feedback_cycle(
        instruction="Rank candidates.",
        eval_data=eval_data,
        config=config_r1,
    )

    # Resumed from round 1, but max_rounds=1 means no new rounds run
    assert result2.resumed_from_round == 1
    assert result2.cycle_id == cycle_id
    # No new evals should have run
    assert call_count[0] == evals_after_r1
    # Still has the round from the first run
    assert result2.n_rounds == 1


# ---------------------------------------------------------------------------
# Mid-round resume with persisted candidates
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_mid_round_resume_uses_persisted_candidates(
    monkeypatch, eval_data, tmp_path,
):
    """Abort mid-round, re-run → GrowFilterNode skipped, same candidates used."""
    apply_init_mock(monkeypatch)
    apply_llm_mock(monkeypatch)

    # Track whether generate_candidates was called
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

    # --- First run: completes round 0 normally ---
    result1 = await run_feedback_cycle(
        instruction="Rank candidates.",
        eval_data=eval_data,
        config=config,
    )
    assert result1.n_rounds == 1
    assert grow_calls[0] == 1  # GrowFilterNode ran once
    cycle_id = result1.cycle_id
    assert cycle_id is not None

    # Verify candidates file was persisted
    from api.services.stores.campaign_store import CampaignStore
    store = CampaignStore(tmp_path)
    candidates_path = (
        store._trial_dir("test_backend", cycle_id)
        / "round_0000_candidates.json"
    )
    assert candidates_path.exists()

    # Read persisted candidates for comparison
    from api.services.stores.base import read_json
    persisted_candidates = read_json(candidates_path)
    assert len(persisted_candidates) == 3  # n_variants=3

    # --- Simulate mid-round abort: revert to "active" with no trials ---
    store.update("test_backend", cycle_id, {"status": "active", "trials": []})

    # Reset grow call counter
    grow_calls[0] = 0

    # --- Second run: should load persisted candidates, skip GrowFilterNode ---
    result2 = await run_feedback_cycle(
        instruction="Rank candidates.",
        eval_data=eval_data,
        config=config,
    )

    # GrowFilterNode was NOT called — candidates loaded from disk
    assert grow_calls[0] == 0
    # Same cycle_id
    assert result2.cycle_id == cycle_id
    # Round still executed (eval happened)
    assert result2.n_rounds == 1


# ---------------------------------------------------------------------------
# No store → no persistence (backward compat)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_no_store_no_persistence(monkeypatch, eval_data, base_config):
    """Without project_root, cycle runs fresh every time with no cycle_id."""
    apply_init_mock(monkeypatch)
    apply_llm_mock(monkeypatch)
    apply_grow_mock(monkeypatch)
    apply_eval_mock(monkeypatch, round_hits=[0])

    # No project_root or backend_id set
    result = await run_feedback_cycle(
        instruction="Test.",
        eval_data=eval_data,
        config=base_config,
    )

    assert result.cycle_id is None
    assert result.resumed_from_round == 0
