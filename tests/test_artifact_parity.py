"""Artifact parity test — verifies CampaignPersistenceEmitter produces all session artifacts.

This is the sustenance guard for entry-point parity: if any artifact in
CAMPAIGN_SESSION_ARTIFACTS is missing after emitter lifecycle, this test fails.
See docs/specs/m-parity-entry-point-parity.md § Wave 4.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from promptpotter.infrastructure.persistence.session_emitter import CAMPAIGN_SESSION_ARTIFACTS


@pytest.fixture
def session_dir(tmp_path: Path) -> Path:
    """Create a session directory with a minimal session.json."""
    sdir = tmp_path / "test_backend" / "sessions" / "test_session"
    sdir.mkdir(parents=True)
    (sdir / "session.json").write_text(
        json.dumps(
            {
                "phase": "optimizing",
                "backend_id": "test_backend",
                "session_id": "test_session",
            }
        )
    )
    return sdir


def test_emitter_produces_all_session_artifacts(tmp_path: Path, session_dir: Path) -> None:
    """Emitter lifecycle (init + on_phase + on_query + on_candidate + on_round + finalize)
    must produce all CAMPAIGN_SESSION_ARTIFACTS."""
    from promptpotter.application.campaign.config import LoopConfig
    from promptpotter.application.optimization.loop_env import LoopEnv
    from promptpotter.application.optimization.loop_state import LoopState
    from promptpotter.application.optimization.phases import PhaseEvent
    from promptpotter.application.optimization.results import RoundResult
    from promptpotter.infrastructure.persistence.session_emitter import CampaignPersistenceEmitter

    config = LoopConfig(
        backend_id="test_backend",
        session_id="test_session",
        max_rounds=5,
        l1_patience=3,
    )
    emitter = CampaignPersistenceEmitter(session_dir, config)

    # Simulate a single round lifecycle
    init_state = LoopState(current_accuracy=0.5)
    init_env = LoopEnv(cycle_id="cycle_test_001")
    emitter.on_phase(
        PhaseEvent(
            phase="init",
            event="exit",
            round=0,
            data={"state": init_state, "env": init_env, "config": config},
        )
    )
    emitter.on_phase(
        PhaseEvent(
            phase="l1_generate",
            event="enter",
            round=0,
            data={"round": 0},
        )
    )
    emitter.on_phase(
        PhaseEvent(
            phase="l1_generate",
            event="exit",
            round=0,
            data={"candidates": [{"pipeline_params_override": {}}]},
        )
    )
    emitter.on_phase(
        PhaseEvent(
            phase="l1_score",
            event="enter",
            round=0,
            data={},
        )
    )

    # Simulate a query evaluation
    emitter.on_sample_scored(
        0,
        1,
        0,
        2,
        {
            "query": "test_query",
            "prediction": "test_pred",
            "hit": True,
            "cached": False,
            "pipeline_data": {"total_time": 0.1, "terminated_at": "llm_ranking"},
        },
    )
    emitter.on_sample_scored(
        0,
        1,
        1,
        2,
        {
            "query": "test_query_2",
            "prediction": "test_pred_2",
            "hit": False,
            "ground_truth_rank": 3,
            "n_candidates": 10,
            "cached": True,
            "pipeline_data": {"total_time": 0.05, "terminated_at": "llm_ranking"},
        },
    )

    # Simulate candidate eval
    emitter.on_candidate_scored(0, 1, {"accuracy": 0.6, "hits": 1, "total": 2})

    # Simulate round complete
    round_result = RoundResult(
        round=0,
        label="C1",
        accuracy=0.6,
        hits=1,
        total=2,
        improved=True,
        prompt_fields={"instruction": "test"},
        candidates_scored=1,
    )
    emitter.on_round_complete(round_result, l1_stall_count=0)

    # Finalize
    emitter.finalize(
        n_rounds=1,
        best_accuracy=0.6,
        best_round=0,
        stop_reason="max_rounds",
        cycle_id="cycle_test_001",
    )

    # The contract: every artifact in CAMPAIGN_SESSION_ARTIFACTS must exist.
    missing = [a for a in CAMPAIGN_SESSION_ARTIFACTS if not (session_dir / a).exists()]
    assert not missing, f"Entry-point parity violated — missing: {missing}"


def test_auto_mint_session_claims_active_pointer() -> None:
    """Non-CLI entry points (notebook/smoke) get a session auto-minted when
    ``session_id=''`` — and the active pointer is claimed, so CLI commands
    like ``show-status`` find the session without ``--session``."""
    from types import SimpleNamespace

    from promptpotter.application.campaign.data import CampaignBaseline
    from promptpotter.application.campaign.runner import _auto_mint_session

    created: dict = {}

    def fake_create(backend_id: str, state: dict) -> str:
        created["backend_id"] = backend_id
        created["state"] = state
        return "sess_test_abc"

    def fake_save_active_pointer(backend_id: str, session_id: str) -> None:
        created["active_pointer"] = (backend_id, session_id)

    sessions_store = SimpleNamespace(
        create=fake_create,
        save_active_pointer=fake_save_active_pointer,
    )
    store = SimpleNamespace(sessions=sessions_store)
    backend_client = SimpleNamespace(base_url="http://localhost:8000")
    session = SimpleNamespace(
        store=store,
        backend_id="bk_test",
        backend_client=backend_client,
        dataset_name="ds_test",
    )
    baseline = CampaignBaseline(
        baseline_ps={"instruction": "seed"},
        baseline_acc=0.42,
        baseline_results=None,
        instruction="seed",
    )

    minted = _auto_mint_session(session, {}, baseline, dataset_size=7, experiment_id=None)

    assert minted == "sess_test_abc"
    assert created["backend_id"] == "bk_test"
    assert created["state"]["dataset_count"] == 7
    assert created["state"]["baseline_accuracy"] == 0.42
    assert created["state"]["baseline_prompt_fields"] == {"instruction": "seed"}
    assert created["active_pointer"] == ("bk_test", "sess_test_abc")


def test_control_surface_reads_pause_signal(session_dir: Path) -> None:
    """CampaignControlReader reads control signals from campaign_control.json."""
    from promptpotter.infrastructure.persistence.control import CampaignControlReader

    control_path = session_dir / "campaign_control.json"
    control_path.write_text(
        json.dumps({"requested_state": "pause", "pause_before_l2_scoring": False})
    )

    surface = CampaignControlReader(session_dir)
    assert surface.check("after_round") == "pause"


def test_control_surface_resumes(session_dir: Path) -> None:
    """CampaignControlReader acknowledges resume by clearing to running."""
    from promptpotter.infrastructure.persistence.control import CampaignControlReader

    control_path = session_dir / "campaign_control.json"
    control_path.write_text(
        json.dumps({"requested_state": "resume", "pause_before_l2_scoring": False})
    )

    surface = CampaignControlReader(session_dir)
    assert surface.check("after_round") is None

    # Verify it wrote "running" back
    data = json.loads(control_path.read_text())
    assert data["requested_state"] == "running"


def test_control_surface_l2_pause(session_dir: Path) -> None:
    """CampaignControlReader honors pause_before_l2_scoring at before_l2_scoring checkpoint."""
    from promptpotter.infrastructure.persistence.control import CampaignControlReader

    control_path = session_dir / "campaign_control.json"
    control_path.write_text(
        json.dumps({"requested_state": "running", "pause_before_l2_scoring": True})
    )

    surface = CampaignControlReader(session_dir)
    # At non-L2 checkpoint: no pause
    assert surface.check("after_round") is None
    # At L2 checkpoint: pause
    assert surface.check("before_l2_scoring") == "pause"
