"""Presentation contracts — view round-trip + projection routing.

Two named invariants:
  1. View round-trip: ``from_phase_event(e) == from_disk_round(write(e))``
     on ``RoundCompleteView`` — the named correctness guarantee of the
     two-factories-onto-one-View unification. Holds for the improvement
     case AND the no-improvement case (delta=0, p_value=None).
  2. Projection routing: ``LiveDashboardView`` accepts only a
     ``RootCycleDir`` (sibling dirs under ``forks/`` / ``diag/`` /
     ``sweeps/`` are rejected at ``__init__``); ``AuditTrailView``
     accepts only a ``rounds_dir`` ending in ``.runtime/cache/rounds`` (or
     a CycleDir via ``from_cycle_dir`` that derives the subpath); the
     ``on_record`` ledger hook drives ``begin_round`` / ``flush`` on
     PhaseRecord('round', enter|complete), persists the origin phase to
     ``round_origin.json``, and ignores ResumeCheckpointRecord records.
"""

from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path

import pytest

from promptpotter.domain.cycle_paths import CycleDir, RootCycleDir
from promptpotter.domain.phases import PhaseEvent
from promptpotter.infrastructure.projections import (
    AuditTrailView,
    LiveDashboardView,
)
from promptpotter.presentation.views.view_ingress import from_phase_event
from promptpotter.presentation.views.view_models import RoundCompleteView
from promptpotter.presentation.writers import from_disk_round
from promptpotter.shared.statistics import wilson_ci

# ===========================================================================
# View round-trip — named correctness invariant of factory unification
# ===========================================================================


def _candidate_score_dict(
    *,
    candidate_id: str,
    label: str,
    accuracy: float,
    composite_fitness: float,
    hits: int,
    total: int,
    aborted: bool = False,
) -> dict:
    """Mirror what ``CandidateScore.to_dict`` writes into ``round_data.candidate_scores``."""
    ci_lo, ci_hi = wilson_ci(hits, total)
    return {
        "candidate_id": candidate_id,
        "label": label,
        "changes_description": "",
        "pipeline_params_override": None,
        "accuracy": accuracy,
        "composite_fitness": composite_fitness,
        "hits": hits,
        "total": total,
        "ci_lo": ci_lo,
        "ci_hi": ci_hi,
        "evaluators": {"accuracy": accuracy},
        "escalation_aborted": aborted,
        "elimination_stopped": False,
        "scored_samples": total,
        "expected_samples": total,
        "invalid": False,
        "resumed_from_cache": False,
        "validation_failures": [],
        "runtime_failures": [],
        "elimination_context": {},
    }


def test_round_complete_view_roundtrip() -> None:
    """``from_phase_event(e) == from_disk(write_then_load(e))`` on
    ``RoundCompleteView`` — the named correctness invariant of the unification."""
    origin_acc = 0.30
    winner_acc = 0.55
    winner_hits = 11
    winner_total = 20
    winner_composite_fitness = 0.60

    # Three candidates in round 1; the second one wins.
    # Labels follow the canonical CN.M scheme (round=1, idx=0/1/2 → C1.1/C1.2/C1.3).
    candidate_scores = [
        {
            "label": "C1.1",
            "accuracy": 0.40,
            "composite_fitness": 0.45,
            "hits": 8,
            "total": 20,
            "escalation_aborted": False,
        },
        {
            "label": "C1.2",
            "accuracy": winner_acc,
            "composite_fitness": winner_composite_fitness,
            "hits": winner_hits,
            "total": winner_total,
            "escalation_aborted": False,
        },
        {
            "label": "C1.3",
            "accuracy": 0.20,
            "composite_fitness": 0.25,
            "hits": 4,
            "total": 20,
            "escalation_aborted": False,
        },
    ]

    # Live path: build a phase event identical to what l1.py emits at
    # L1_SCORE:exit, run from_phase_event with a fresh ctx.
    ctx = {
        "round_num": 1,
        "origin_accuracy": origin_acc,
        "origin_composite_fitness": 0.40,
        "composite_fitness_formula": "0.7*acc + 0.3*recall",
        "composite_fitness_formula_short": "0.7*A + 0.3*R",
    }
    event = PhaseEvent(
        phase="l1_score",
        event="exit",
        round=1,
        data={
            "winner_label": "C1.2",
            "winner_accuracy": winner_acc,
            "winner_composite_fitness": winner_composite_fitness,
            "winner_evaluators": {"accuracy": winner_acc},
            "improved": True,
            "candidate_scores": candidate_scores,
        },
    )
    live_view = from_phase_event(event, ctx)
    assert isinstance(live_view, RoundCompleteView)

    # Disk path: build a round_data dict shaped like ``RoundResult.model_dump()``
    # for the same round, then run from_disk_round.
    round_data = {
        "round_id": "round_1",
        "round": 1,
        "label": "round_1",
        "accuracy": winner_acc,
        "composite_fitness": winner_composite_fitness,
        "hits": winner_hits,
        "total": winner_total,
        "improved": True,
        "p_value": live_view.p_value,
        "origin_accuracy": origin_acc,
        "evaluators": {"accuracy": winner_acc},
        "candidate_scores": [
            _candidate_score_dict(
                candidate_id=f"cand_{i}",
                label=cs["label"],
                accuracy=cs["accuracy"],
                composite_fitness=cs["composite_fitness"],
                hits=cs["hits"],
                total=cs["total"],
            )
            for i, cs in enumerate(candidate_scores)
        ],
        "critique": {},
    }
    disk_view = from_disk_round(
        round_data,
        composite_fitness_formula=ctx["composite_fitness_formula"],
        composite_fitness_formula_short=ctx["composite_fitness_formula_short"],
        origin_composite_fitness=ctx["origin_composite_fitness"],
    )

    # The live view carries an in-memory ``next_action`` flag that the
    # runner uses for control flow but never persists. Strip it from
    # the live view so the comparison covers only the persisted contract.
    live_view = replace(live_view, next_action="")

    assert live_view == disk_view


def test_round_complete_view_no_improvement() -> None:
    """Round trip stays consistent when no candidate beats origin."""
    origin_acc = 0.50
    candidate_scores = [
        {
            "label": "C1.1",
            "accuracy": 0.40,
            "composite_fitness": 0.42,
            "hits": 8,
            "total": 20,
            "escalation_aborted": False,
        },
    ]
    ctx = {"round_num": 1, "origin_accuracy": origin_acc}
    event = PhaseEvent(
        phase="l1_score",
        event="exit",
        round=1,
        data={
            "winner_label": "C1.1",
            "winner_accuracy": 0.40,
            "winner_composite_fitness": 0.42,
            "winner_evaluators": {},
            "improved": False,
            "candidate_scores": candidate_scores,
        },
    )
    live_view = from_phase_event(event, ctx)
    assert isinstance(live_view, RoundCompleteView)

    round_data = {
        "round": 1,
        "accuracy": 0.40,
        "composite_fitness": 0.42,
        "hits": 8,
        "total": 20,
        "improved": False,
        "p_value": None,
        "origin_accuracy": origin_acc,
        "evaluators": {},
        "candidate_scores": [
            _candidate_score_dict(
                candidate_id="cand_0",
                label="C1.1",
                accuracy=0.40,
                composite_fitness=0.42,
                hits=8,
                total=20,
            )
        ],
        "critique": {},
    }
    disk_view = from_disk_round(round_data)

    live_view = replace(live_view, next_action="")

    # Delta now reflects the true gap vs origin even when ``improved=False``
    # — the renderer surfaces it on the ``✗ NOT PROMOTED`` path so the
    # operator sees the actual difference. Round-trip parity holds: both
    # live and disk views compute the gap the same way.
    assert live_view.improved is False
    assert live_view.delta == pytest.approx(-0.10)
    assert live_view.improved_reason is None
    assert live_view == disk_view


# ===========================================================================
# Projection routing — newtype-guarded write targets
# ===========================================================================


@pytest.mark.parametrize("kind", ["forks", "diag", "sweeps"])
def test_live_dashboard_rejects_sibling_path(tmp_path: Path, kind: str) -> None:
    """A sibling dir (under ``forks/``, ``diag/``, or ``sweeps/``) cannot host the live dashboard."""
    fork_dir = tmp_path / "campaigns" / "root_xyz" / kind / f"root_xyz_{kind}_abc"
    fork_dir.mkdir(parents=True)
    session_dir = tmp_path / "sessions" / "s_test"
    session_dir.mkdir(parents=True)

    with pytest.raises(ValueError, match="family root"):
        LiveDashboardView(
            RootCycleDir(fork_dir),  # newtype-cast at the wrong site
            session_dir,
            l1_patience=3,
            n_variants=5,
            sp_budget_ttest=20,
        )


def test_live_dashboard_accepts_root_path(tmp_path: Path) -> None:
    """A family-root cycle dir is the only valid live-dashboard target."""
    root_dir = tmp_path / "campaigns" / "root_xyz"
    root_dir.mkdir(parents=True)
    session_dir = tmp_path / "sessions" / "s_test"

    proj = LiveDashboardView(
        RootCycleDir(root_dir),
        session_dir,
        l1_patience=3,
        n_variants=5,
        sp_budget_ttest=20,
    )
    assert proj.state_path == root_dir / "dashboard.json"


def test_live_dashboard_for_session_recovers_round_after_interrupt(
    tmp_path: Path,
) -> None:
    """Resume invariant: when ``rounds/round_NNNN.json`` checkpoints exist on
    disk but the prior ``dashboard.json::round`` is stale (e.g. zeroed by an
    earlier re-init), ``for_session`` must restore ``round`` to the highest
    checkpoint — otherwise the webapp polls ``rounds/round_0000.json``
    forever on a cycle that has rounds 1–3 completed.
    """
    project_root = tmp_path / "default"
    cycle_id = "cycle_abc123"
    root_dir = project_root / "campaigns" / cycle_id
    rounds_dir = root_dir / "rounds"
    rounds_dir.mkdir(parents=True)
    for n in (1, 2, 3):
        (rounds_dir / f"round_{n:04d}.json").write_text("{}", encoding="utf-8")
    (root_dir / "dashboard.json").write_text(
        json.dumps({"state": "init", "round": 0, "best": 0.5}),
        encoding="utf-8",
    )

    proj = LiveDashboardView.for_session(
        origin_accuracy=0.1,
        cycle_id=cycle_id,
        project_root=str(project_root),
        session_id="s_test",
        l1_patience=3,
        n_variants=5,
        sp_budget_ttest=20,
    )
    assert proj is not None
    assert proj.state["round"] == 3


def test_audit_trail_rejects_non_rounds_path(tmp_path: Path) -> None:
    """A rounds_dir must terminate in ``.runtime/cache/rounds`` — anything else is ad-hoc routing."""
    bad = tmp_path / "campaigns" / "cyc1"
    bad.mkdir(parents=True)
    with pytest.raises(ValueError, match=r"\.runtime/cache/rounds"):
        AuditTrailView(bad)


def test_audit_trail_from_cycle_dir_derives_subpath(tmp_path: Path) -> None:
    """The standard factory derives ``.runtime/cache/rounds`` from a cycle dir."""
    cycle_dir = tmp_path / "campaigns" / "cyc1"
    cycle_dir.mkdir(parents=True)
    proj = AuditTrailView.from_cycle_dir(CycleDir(cycle_dir))
    assert proj.rounds_dir == cycle_dir / ".runtime" / "cache" / "rounds"


def test_audit_trail_fork_dir_lands_under_fork(tmp_path: Path) -> None:
    """A fork's audit projection writes under the fork dir, never the parent root."""
    root_dir = tmp_path / "campaigns" / "root_xyz"
    fork_dir = root_dir / "forks" / "root_xyz_fork_abc"
    fork_dir.mkdir(parents=True)
    proj = AuditTrailView.from_cycle_dir(CycleDir(fork_dir))
    assert proj.rounds_dir == fork_dir / ".runtime" / "cache" / "rounds"
    assert root_dir not in proj.rounds_dir.parents or "forks" in proj.rounds_dir.parts


def test_audit_trail_on_record_handles_round_phase(tmp_path: Path) -> None:
    """PhaseRecord('round', 'enter')/'complete' from a ledger drives the recorder lifecycle.

    PhaseRecord 3 contract: an AuditTrailView bound to the ledger MUST
    react to round-boundary phase records — ``enter`` opens a fresh
    round, ``complete`` flushes ``round_NNNN.json`` to disk. Other
    record types are ignored at this projection's scope.
    """
    from promptpotter.domain.run_records import (
        LLMCallRecord,
        PhaseRecord,
        ResumeCheckpointKind,
        ResumeCheckpointRecord,
    )

    cycle_dir = tmp_path / "campaigns" / "cyc1"
    cycle_dir.mkdir(parents=True)
    proj = AuditTrailView.from_cycle_dir(CycleDir(cycle_dir))

    # Record an LLMCallRecord so flush has state to write.
    proj.on_record(PhaseRecord(phase="round", event="enter", round=4), 0)
    proj.on_record(
        LLMCallRecord(
            node="l1_generate", round=4, payload={"type": "l1_generate", "response": "ok"}
        ),
        1,
    )
    # An unrelated ResumeCheckpointRecord must not crash or trigger a flush.
    proj.on_record(
        ResumeCheckpointRecord(kind=ResumeCheckpointKind.ROUND_WINNER, outcome="c1", round=4), 2
    )
    # Round-complete triggers the disk write.
    proj.on_record(PhaseRecord(phase="round", event="complete", round=4), 3)

    written = cycle_dir / ".runtime" / "cache" / "rounds" / "round_0004.json"
    assert written.exists(), "PhaseRecord('round', 'complete') must flush round_NNNN.json"


def test_audit_trail_drain_flushes_partial_round(tmp_path: Path) -> None:
    """`drain()` flushes a buffered round even when `round:complete` never arrives.

    Doctrine: ledger is the truth; projection files are caches. An operator
    Ctrl+C mid-candidate produces a ledger with the round's `enter` event
    and LLMCallRecord(s), but no `complete`. Without `drain()`, the audit
    projection's `_nodes` buffer is silently discarded on teardown. With
    `drain()`, the partial round lands on disk tagged `"interrupted": true`
    so future tooling can read it.
    """
    from promptpotter.domain.run_records import LLMCallRecord, PhaseRecord

    cycle_dir = tmp_path / "campaigns" / "cyc_drain"
    cycle_dir.mkdir(parents=True)
    proj = AuditTrailView.from_cycle_dir(CycleDir(cycle_dir))

    # Round 1 begins, an L1 call is recorded, then the run is interrupted —
    # no `complete` event arrives. Simulate the runner's teardown by setting
    # the interrupted flag and calling drain().
    proj.on_record(PhaseRecord(phase="round", event="enter", round=1), 0)
    proj.on_record(
        LLMCallRecord(
            node="l1_generate", round=1, payload={"type": "l1_generate", "response": "partial"}
        ),
        1,
    )
    proj._cycle_was_interrupted = True
    proj.drain()

    path = cycle_dir / ".runtime" / "cache" / "rounds" / "round_0001.json"
    assert path.exists(), "drain() must flush the partial round to disk"
    payload = json.loads(path.read_text(encoding="utf-8"))
    assert payload.get("interrupted") is True, "interrupted flag must surface on the partial round"
    assert "l1_generate" in payload["nodes"], "the buffered LLM call must be in the flushed nodes"


def test_audit_trail_persists_origin_as_round_0000(tmp_path: Path) -> None:
    """Origin IS round 0 on disk: ``PhaseRecord('origin','enter'|'exit',round=0)``
    flushes to ``round_0000.json``; the first L1 round (round=1) flushes to
    ``round_0001.json``. No ``round_origin.json``, no collision.
    """
    from promptpotter.domain.run_records import LLMCallRecord, PhaseRecord

    cycle_dir = tmp_path / "campaigns" / "cyc_origin"
    cycle_dir.mkdir(parents=True)
    proj = AuditTrailView.from_cycle_dir(CycleDir(cycle_dir))

    proj.on_record(PhaseRecord(phase="origin", event="enter", round=0), 0)
    proj.on_record(
        LLMCallRecord(node="llm_only", payload={"type": "llm_only", "response": "answer"}),
        1,
    )
    proj.on_record(PhaseRecord(phase="origin", event="exit", round=0), 2)
    # First L1 round = round 1 (origin = round 0 is already on disk).
    proj.on_record(PhaseRecord(phase="round", event="enter", round=1), 3)
    proj.on_record(
        LLMCallRecord(
            node="l1_generate", round=1, payload={"type": "l1_generate", "response": "ok"}
        ),
        4,
    )
    proj.on_record(PhaseRecord(phase="round", event="complete", round=1), 5)

    origin_path = cycle_dir / ".runtime" / "cache" / "rounds" / "round_0000.json"
    round_1_path = cycle_dir / ".runtime" / "cache" / "rounds" / "round_0001.json"
    legacy_origin_path = cycle_dir / ".runtime" / "cache" / "rounds" / "round_origin.json"
    assert origin_path.exists(), "origin phase must flush to round_0000.json"
    assert round_1_path.exists(), "first L1 round must flush to round_0001.json"
    assert not legacy_origin_path.exists(), "round_origin.json must not be written"
