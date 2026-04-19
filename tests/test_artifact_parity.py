"""Artifact parity test — verifies CampaignPersistenceEmitter produces all
required per-cycle and per-session artifacts.

If any artifact in CAMPAIGN_ARTIFACTS or SESSION_ARTIFACTS is missing
after the emitter lifecycle, this test fails.  Sessions and campaigns
are separate trees; a campaign records its parent session via
``index.json::parent_session_id``.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from promptpotter.infrastructure.persistence.session_emitter import (
    CAMPAIGN_ARTIFACTS,
    SESSION_ARTIFACTS,
)


@pytest.fixture
def session_and_campaign_dirs(tmp_path: Path) -> tuple[Path, Path]:
    """Create a session dir + campaign dir + minimal session.json + index.json.

    Layout: ``{tmp_path}/sessions/{session_id}/`` and
    ``{tmp_path}/campaigns/{cycle_id}/``. ``tmp_path`` stands in for the
    tenant root.
    """
    session_id = "s_test1234"
    cycle_id = "cycle_test_abc"
    sdir = tmp_path / "sessions" / session_id
    cdir = tmp_path / "campaigns" / cycle_id
    sdir.mkdir(parents=True)
    cdir.mkdir(parents=True)

    (sdir / "session.json").write_text(
        json.dumps(
            {
                "session_id": session_id,
                "phase": "optimizing",
                "current_cycle_id": cycle_id,
            }
        )
    )
    (cdir / "index.json").write_text(
        json.dumps(
            {
                "campaign_id": cycle_id,
                "backend_id": "test_backend",
                "parent_session_id": session_id,
                "trials": [],
                "n_trials": 0,
            }
        )
    )
    return sdir, cdir


def test_emitter_produces_all_artifacts(session_and_campaign_dirs: tuple[Path, Path]) -> None:
    """Emitter lifecycle must produce all CAMPAIGN_ARTIFACTS in the campaign
    dir and ensure all SESSION_ARTIFACTS in the session dir."""
    from promptpotter.application.campaign.config import CampaignConfig
    from promptpotter.application.optimization.loop_env import LoopEnv
    from promptpotter.application.optimization.loop_state import LoopState
    from promptpotter.application.optimization.phases import PhaseEvent
    from promptpotter.application.optimization.results import RoundResult, RunResult
    from promptpotter.infrastructure.persistence.session_emitter import CampaignPersistenceEmitter

    session_dir, campaign_dir = session_and_campaign_dirs
    config = CampaignConfig(optimization={"max_rounds": 5, "l1_patience": 3})
    emitter = CampaignPersistenceEmitter(
        campaign_dir,
        session_dir,
        max_rounds=5,
        l1_patience=3,
        active_nodes=[],
        model="",
        n_variants=5,
        sp_budget_ttest=20,
        pause_before_scoring=False,
    )

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
    emitter.write_result(
        RunResult(
            rounds=[round_result],
            n_rounds=1,
            best_accuracy=0.6,
            best_round=0,
            baseline_accuracy=0.5,
            winner_prompt_fields={"instruction": "test"},
            stop_reason="max_rounds",
            started_at="2026-04-19T00:00:00+00:00",
            finished_at="2026-04-19T00:01:00+00:00",
            cycle_id="cycle_test_001",
        )
    )

    missing_campaign = [a for a in CAMPAIGN_ARTIFACTS if not (campaign_dir / a).exists()]
    assert not missing_campaign, f"Campaign-tree parity violated — missing: {missing_campaign}"
    missing_session = [a for a in SESSION_ARTIFACTS if not (session_dir / a).exists()]
    assert not missing_session, f"Session-tree parity violated — missing: {missing_session}"


def test_campaign_records_parent_session(session_and_campaign_dirs: tuple[Path, Path]) -> None:
    """Every campaign records its parent session id in index.json."""
    _session_dir, campaign_dir = session_and_campaign_dirs
    data = json.loads((campaign_dir / "index.json").read_text(encoding="utf-8"))
    assert data["parent_session_id"], "index.json must carry parent_session_id"


def test_auto_mint_session_claims_active_pointer() -> None:
    """Non-CLI entry points (notebook/smoke/scan) get a session+cycle
    auto-minted when ``session_id=''`` — and the active pointer is claimed,
    so CLI commands like ``show-status`` find them without ``--session``.

    Mints two distinct ids: a fresh ``session_id`` (``s_<8hex>``) and a
    deterministic ``cycle_id`` (``cycle_<12hex>`` from the problem hash).
    """
    from types import SimpleNamespace
    from unittest.mock import patch

    from promptpotter.application.campaign.config import CampaignConfig
    from promptpotter.application.campaign.session_state import auto_mint_session

    sessions_calls: dict = {}
    campaigns_calls: dict = {}

    def fake_session_create(sid: str, state: dict) -> None:
        sessions_calls["create"] = (sid, state)

    def fake_session_ensure(sid: str) -> None:
        sessions_calls["ensure"] = sid

    def fake_campaign_create(bid: str, cid: str, meta: dict) -> None:
        campaigns_calls["create"] = (bid, cid, meta)

    sessions_store = SimpleNamespace(
        create=fake_session_create,
        ensure_narrative_files=fake_session_ensure,
    )
    campaigns_store = SimpleNamespace(create=fake_campaign_create)
    store = SimpleNamespace(
        sessions=sessions_store,
        campaigns=campaigns_store,
        tenant_id="default",
    )
    backend_client = SimpleNamespace(base_url="http://localhost:8000")
    session = SimpleNamespace(
        store=store,
        backend_id="bk_test",
        backend_client=backend_client,
        dataset_name="ds_test",
    )

    pointer_calls: dict = {}

    def fake_save_pointer(tid: str, sid: str, cid: str) -> None:
        pointer_calls["pointer"] = (tid, sid, cid)

    with patch(
        "promptpotter.infrastructure.store.save_active_pointer",
        side_effect=fake_save_pointer,
    ):
        sid, cid = auto_mint_session(
            session,
            CampaignConfig(),
            cycle_hash="abcdef012345",
            baseline_acc=0.42,
            baseline_prompt_fields={"instruction": "seed"},
            dataset_size=7,
            experiment_id=None,
        )

    assert sid.startswith("s_")
    assert cid == "cycle_abcdef012345"
    assert sessions_calls["create"][0] == sid
    assert sessions_calls["create"][1]["dataset_count"] == 7
    assert sessions_calls["create"][1]["baseline_accuracy"] == 0.42
    assert sessions_calls["create"][1]["current_cycle_id"] == cid
    assert sessions_calls["ensure"] == sid
    assert campaigns_calls["create"] == ("bk_test", cid, {"parent_session_id": sid})
    assert pointer_calls["pointer"] == ("default", sid, cid)


def test_control_surface_reads_pause_signal(
    session_and_campaign_dirs: tuple[Path, Path],
) -> None:
    """CampaignControlReader reads control signals from control.json."""
    from promptpotter.infrastructure.persistence.control import CampaignControlReader

    session_dir, _ = session_and_campaign_dirs
    control_path = session_dir / "control.json"
    control_path.write_text(
        json.dumps({"requested_state": "pause", "pause_before_l2_scoring": False})
    )

    surface = CampaignControlReader(session_dir)
    assert surface.check("after_round") == "pause"


def test_control_surface_resumes(session_and_campaign_dirs: tuple[Path, Path]) -> None:
    """CampaignControlReader acknowledges resume by clearing to running."""
    from promptpotter.infrastructure.persistence.control import CampaignControlReader

    session_dir, _ = session_and_campaign_dirs
    control_path = session_dir / "control.json"
    control_path.write_text(
        json.dumps({"requested_state": "resume", "pause_before_l2_scoring": False})
    )

    surface = CampaignControlReader(session_dir)
    assert surface.check("after_round") is None

    data = json.loads(control_path.read_text())
    assert data["requested_state"] == "running"


def test_control_surface_l2_pause(session_and_campaign_dirs: tuple[Path, Path]) -> None:
    """CampaignControlReader honors pause_before_l2_scoring at before_l2_scoring."""
    from promptpotter.infrastructure.persistence.control import CampaignControlReader

    session_dir, _ = session_and_campaign_dirs
    control_path = session_dir / "control.json"
    control_path.write_text(
        json.dumps({"requested_state": "running", "pause_before_l2_scoring": True})
    )

    surface = CampaignControlReader(session_dir)
    assert surface.check("after_round") is None
    assert surface.check("before_l2_scoring") == "pause"
