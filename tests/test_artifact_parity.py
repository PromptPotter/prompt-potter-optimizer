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


def test_artifact_sets_are_disjoint_and_well_formed() -> None:
    """CAMPAIGN_ARTIFACTS and SESSION_ARTIFACTS must never overlap; each
    fixed key must land in the expected set. Prevents a file from being
    written into both trees."""
    assert CAMPAIGN_ARTIFACTS.isdisjoint(SESSION_ARTIFACTS)
    assert {"journal.md", "notes.md", "session.json", "control.json"} <= SESSION_ARTIFACTS
    assert {
        "dashboard.json",
        "index.json",
        "log.md",
        "phase_events.jsonl",
    } <= CAMPAIGN_ARTIFACTS


def test_emitter_produces_all_artifacts(session_and_campaign_dirs: tuple[Path, Path]) -> None:
    """Emitter lifecycle must produce all CAMPAIGN_ARTIFACTS in the campaign
    dir and ensure all SESSION_ARTIFACTS in the session dir."""
    from types import SimpleNamespace

    from promptpotter.application.campaign.config import CampaignConfig
    from promptpotter.application.optimization.cycle import Cycle
    from promptpotter.application.optimization.results import RoundResult, RunResult
    from promptpotter.domain.phases import PhaseEvent
    from promptpotter.infrastructure.persistence.session_emitter import CampaignPersistenceEmitter

    session_dir, campaign_dir = session_and_campaign_dirs
    config = CampaignConfig(
        optimization={
            "max_rounds": 5,
            "l1_patience": 3,
            "improvement_threshold": 0.01,
            "seed": 42,
            "max_failures": 15,
            "degradation_threshold": 0.4,
            "enable_l2": True,
            "enable_l3": True,
        }
    )
    emitter = CampaignPersistenceEmitter(
        campaign_dir,
        session_dir,
        l1_patience=3,
        n_variants=5,
        sp_budget_ttest=20,
    )

    # Simulate a single round lifecycle.  The emitter only reads a handful of
    # fields off the ``env`` payload (cycle_id, scoring_dataset, obs,
    # resumed_from_round, pipeline_schema) so a SimpleNamespace is enough.
    init_state = Cycle(current_accuracy=0.5)
    init_env = SimpleNamespace(
        cycle_id="cycle_test_001",
        scoring_dataset=[],
        obs=None,
        resumed_from_round=0,
        pipeline_schema=None,
    )
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

    # Mirror runner._finalize_run: fold the run summary into index.json::final
    # and render log.md from the index + trial dump.
    from promptpotter.presentation.views import render_log_md

    final = RunResult(
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
    ).model_dump(exclude={"rounds"})
    index_path = campaign_dir / "index.json"
    index_data = json.loads(index_path.read_text(encoding="utf-8")) | {"final": final}
    index_path.write_text(json.dumps(index_data), encoding="utf-8")
    (campaign_dir / "log.md").write_text(
        render_log_md(index_data, [round_result.model_dump()]), encoding="utf-8"
    )
    emitter.finalize("max_rounds")

    missing_campaign = [a for a in CAMPAIGN_ARTIFACTS if not (campaign_dir / a).exists()]
    assert not missing_campaign, f"Campaign-tree parity violated — missing: {missing_campaign}"
    missing_session = [a for a in SESSION_ARTIFACTS if not (session_dir / a).exists()]
    assert not missing_session, f"Session-tree parity violated — missing: {missing_session}"


def test_campaign_records_parent_session(session_and_campaign_dirs: tuple[Path, Path]) -> None:
    """Every campaign records its parent session id in index.json."""
    _session_dir, campaign_dir = session_and_campaign_dirs
    data = json.loads((campaign_dir / "index.json").read_text(encoding="utf-8"))
    assert data["parent_session_id"], "index.json must carry parent_session_id"


def test_file_sink_wire_format_parity(tmp_path: Path) -> None:
    """FileSink's Langfuse shadow must be wire-format compatible: camelCase
    fields on observations/scores, and nested spans carry parentObservationId.

    A JSON from ``campaigns/{cycle_id}/langfuse/`` should be uploadable to
    Langfuse's ingestion API without a transform pass.
    """
    from promptpotter.infrastructure.tracing.events import (
        CampaignEnd,
        CampaignStart,
        NodeEnd,
        NodeStart,
        RoundEnd,
        RoundStart,
    )
    from promptpotter.infrastructure.tracing.sinks.file_sink import FileSink

    tenant_root = tmp_path / "default"
    tenant_root.mkdir()
    sink = FileSink(str(tenant_root), backend_id="bk_test")

    cycle_id = "cycle_wire_parity"
    campaign_id = "cmp_wire_parity"

    sink.on_campaign_start(
        CampaignStart(
            campaign_id=campaign_id,
            config={"max_rounds": 1},
            baseline_accuracy=0.5,
            session_id=cycle_id,
        )
    )
    sink.on_round_start(RoundStart(campaign_id=campaign_id, round_num=0))
    sink.on_node_start(
        NodeStart(
            campaign_id=campaign_id,
            round_num=0,
            node_id="l1_generate",
            node_type="llm",
            obs_type="generation",
            input_data={"prompt": "hello"},
        )
    )
    sink.on_node_end(
        NodeEnd(
            campaign_id=campaign_id,
            round_num=0,
            node_id="l1_generate",
            output_data={"text": "world"},
        )
    )
    sink.on_round_end(
        RoundEnd(
            campaign_id=campaign_id,
            round_num=0,
            accuracy=0.7,
            hits=7,
            total=10,
            improved=True,
            winner_prompt_fields_id="abc",
            candidate_scores=[{"candidate": "c0", "accuracy": 0.7}],
            next_action="continue",
        )
    )
    sink.on_campaign_end(
        CampaignEnd(
            campaign_id=campaign_id,
            best_accuracy=0.7,
            n_rounds=1,
            stop_reason="max_rounds",
            best_round=0,
        )
    )

    campaign_root = tenant_root / "campaigns" / cycle_id

    trace_files = list((campaign_root / "langfuse" / "traces").glob("*.json"))
    assert len(trace_files) == 1
    trace_id = json.loads(trace_files[0].read_text())["id"]

    obs_dir = campaign_root / "langfuse" / "observations" / trace_id
    observations = [json.loads(p.read_text()) for p in obs_dir.glob("*.json")]
    assert len(observations) == 2, "round + node observation expected (single obs per round)"

    for obs in observations:
        assert "traceId" in obs and obs["traceId"] == trace_id
        assert "startTime" in obs
        assert "endTime" in obs
        for snake in ("trace_id", "start_time", "end_time"):
            assert snake not in obs, f"snake_case field {snake!r} leaked into {obs['name']}"

    round_obs = next(o for o in observations if o["name"].startswith("round_"))
    node_obs = next(o for o in observations if o["name"] == "l1_generate")
    assert "parentObservationId" not in round_obs, "round span has no parent (trace-level)"
    assert node_obs["parentObservationId"] == round_obs["id"], "node must nest under round"

    score_files = list((campaign_root / "langfuse" / "scores").glob("*.jsonl"))
    assert score_files, "at least one score jsonl expected"
    scores = [json.loads(line) for p in score_files for line in p.read_text().splitlines() if line]
    assert scores, "at least one score entry expected"
    for score in scores:
        assert "traceId" in score and score["traceId"] == trace_id
        assert "dataType" in score
        for snake in ("trace_id", "data_type"):
            assert snake not in score, f"snake_case field {snake!r} leaked into score"
