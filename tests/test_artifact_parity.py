"""Artifact parity test — verifies CampaignPersistenceEmitter produces all session artifacts.

This is the sustenance guard for entry-point parity: if any artifact in
CAMPAIGN_SESSION_ARTIFACTS is missing after emitter lifecycle, this test fails.
See docs/specs/m-parity-entry-point-parity.md § Wave 4.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from promptpotter.services.campaign.state import CAMPAIGN_SESSION_ARTIFACTS


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


def _make_mock_session_store(tmp_path: Path):
    """Minimal SessionStore stand-in that writes campaign_log.md."""
    from unittest.mock import MagicMock

    store = MagicMock()

    def _append_log(bid: str, sid: str, text: str) -> None:
        log_path = tmp_path / bid / "sessions" / sid / "campaign_log.md"
        log_path.parent.mkdir(parents=True, exist_ok=True)
        with log_path.open("a", encoding="utf-8") as f:
            f.write(text + "\n\n")

    store.append_log = _append_log
    return store


def test_emitter_produces_all_session_artifacts(tmp_path: Path, session_dir: Path) -> None:
    """Emitter lifecycle (init + on_phase + on_query + on_candidate + on_round + finalize)
    must produce all CAMPAIGN_SESSION_ARTIFACTS."""
    from promptpotter.services.campaign.config import LoopConfig
    from promptpotter.services.campaign.persistence_emitter import CampaignPersistenceEmitter
    from promptpotter.services.campaign.state import PhaseEvent, RoundResult

    store = _make_mock_session_store(tmp_path)

    config = LoopConfig(
        backend_id="test_backend",
        session_id="test_session",
        max_rounds=5,
    )
    emitter = CampaignPersistenceEmitter(
        session_dir,
        config,
        session_store=store,
    )

    # Simulate a single round lifecycle
    emitter.on_phase(
        PhaseEvent(
            phase="init",
            event="exit",
            round=0,
            data={"cycle_id": "cycle_test_001", "baseline_accuracy": 0.5, "patience": 3},
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
    emitter.on_round_complete(round_result, stall_count=0)

    # Finalize
    emitter.finalize(
        n_rounds=1,
        best_accuracy=0.6,
        best_round=0,
        stop_reason="max_rounds",
        cycle_id="cycle_test_001",
    )

    # Assert ALL session artifacts exist
    for artifact in CAMPAIGN_SESSION_ARTIFACTS:
        artifact_path = session_dir / artifact
        assert artifact_path.exists(), (
            f"Missing artifact: {artifact} — entry-point parity violated. "
            f"All artifacts in CAMPAIGN_SESSION_ARTIFACTS must be produced by "
            f"CampaignPersistenceEmitter. See docs/specs/m-parity-entry-point-parity.md"
        )

    # Verify campaign_state.json structure
    state = json.loads((session_dir / "campaign_state.json").read_text())
    assert state["workflow"] == "idle"
    assert state["stop_reason"] == "max_rounds"
    assert state["cycle_id"] == "cycle_test_001"
    assert "control" in state
    assert state["rounds_completed"] == 0
    assert state["total_queries_evaluated"] == 2

    # Verify campaign_output.log has content
    log = (session_dir / "campaign_output.log").read_text()
    assert "HIT" in log
    assert "MISS" in log
    assert "Round 0" in log

    # Verify campaign_log.md has round entry + completion entry
    md = (session_dir / "campaign_log.md").read_text()
    assert "Round 0" in md
    assert "Optimization Complete" in md


def test_control_surface_reads_pause_signal(session_dir: Path) -> None:
    """CampaignControlReader reads control signals from campaign_state.json."""
    from promptpotter.services.campaign.persistence_emitter import CampaignControlReader

    state_path = session_dir / "campaign_state.json"
    state_path.write_text(
        json.dumps(
            {
                "control": {"requested_state": "pause", "pause_before_l2_scoring": False},
            }
        )
    )

    surface = CampaignControlReader(state_path)
    assert surface.check("after_round") == "pause"


def test_control_surface_resumes(session_dir: Path) -> None:
    """CampaignControlReader acknowledges resume by clearing to running."""
    from promptpotter.services.campaign.persistence_emitter import CampaignControlReader

    state_path = session_dir / "campaign_state.json"
    state_path.write_text(
        json.dumps(
            {
                "control": {"requested_state": "resume", "pause_before_l2_scoring": False},
            }
        )
    )

    surface = CampaignControlReader(state_path)
    assert surface.check("after_round") is None

    # Verify it wrote "running" back
    data = json.loads(state_path.read_text())
    assert data["control"]["requested_state"] == "running"


def test_control_surface_l2_pause(session_dir: Path) -> None:
    """CampaignControlReader honors pause_before_l2_scoring at before_l2_scoring checkpoint."""
    from promptpotter.services.campaign.persistence_emitter import CampaignControlReader

    state_path = session_dir / "campaign_state.json"
    state_path.write_text(
        json.dumps(
            {
                "control": {"requested_state": "running", "pause_before_l2_scoring": True},
            }
        )
    )

    surface = CampaignControlReader(state_path)
    # At non-L2 checkpoint: no pause
    assert surface.check("after_round") is None
    # At L2 checkpoint: pause
    assert surface.check("before_l2_scoring") == "pause"
