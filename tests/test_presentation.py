"""Presentation contracts — view round-trip + projection routing.

Two named invariants:
  1. View round-trip: ``from_phase_event(e) == from_disk_round(write(e))``
     on ``RoundCompleteView`` — the named correctness guarantee of the
     two-factories-onto-one-View unification. Holds for the improvement
     case AND the no-improvement case (delta=0, p_value=None).
  2. Projection routing: ``LiveDashboardProjection`` accepts only a
     ``RootCycleDir`` (sibling dirs under ``forks/`` / ``diag/`` /
     ``sweeps/`` are rejected at ``__init__``); ``AuditTrailProjection``
     accepts only a ``rounds_dir`` ending in ``.runtime/cache/rounds`` (or
     a CycleDir via ``from_cycle_dir`` that derives the subpath); the
     ``on_record`` ledger hook drives ``begin_round`` / ``flush`` on
     Phase('round', enter|complete), persists the baseline phase to
     ``round_baseline.json``, and ignores Decision records.
"""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest

from promptpotter.domain.cycle_paths import CycleDir, RootCycleDir
from promptpotter.domain.phases import PhaseEvent
from promptpotter.infrastructure.projections import (
    AuditTrailProjection,
    LiveDashboardProjection,
)
from promptpotter.presentation.views.view_factories import (
    from_disk_round,
    from_phase_event,
)
from promptpotter.presentation.views.view_models import RoundCompleteView
from promptpotter.shared.statistics import wilson_ci

# ===========================================================================
# View round-trip — named correctness invariant of factory unification
# ===========================================================================


def _candidate_score_dict(
    *,
    candidate_id: str,
    accuracy: float,
    composite_fitness: float,
    hits: int,
    total: int,
    aborted: bool = False,
) -> dict:
    """Mirror what ``CandidateScore.to_dict`` writes into ``trial.candidate_scores``."""
    ci_lo, ci_hi = wilson_ci(hits, total)
    return {
        "candidate_id": candidate_id,
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
        "scored_queries": total,
        "expected_queries": total,
        "invalid": False,
        "resumed_from_cache": False,
        "validation_failures": [],
        "runtime_failures": [],
        "elimination_context": {},
    }


def test_round_complete_view_roundtrip() -> None:
    """``from_phase_event(e) == from_disk(write_then_load(e))`` on
    ``RoundCompleteView`` — the named correctness invariant of the unification."""
    baseline_acc = 0.30
    winner_acc = 0.55
    winner_hits = 11
    winner_total = 20
    winner_composite_fitness = 0.60

    # Three candidates; the second one wins (highest composite_fitness).
    candidate_scores = [
        {
            "label": "C1",
            "accuracy": 0.40,
            "composite_fitness": 0.45,
            "hits": 8,
            "total": 20,
            "escalation_aborted": False,
        },
        {
            "label": "C2",
            "accuracy": winner_acc,
            "composite_fitness": winner_composite_fitness,
            "hits": winner_hits,
            "total": winner_total,
            "escalation_aborted": False,
        },
        {
            "label": "C3",
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
        "round_num": 0,
        "baseline_accuracy": baseline_acc,
        "baseline_composite_fitness": 0.40,
        "composite_fitness_formula": "0.7*acc + 0.3*recall",
        "composite_fitness_formula_short": "0.7*A + 0.3*R",
    }
    event = PhaseEvent(
        phase="l1_score",
        event="exit",
        round=0,
        data={
            "winner_label": "C2",
            "winner_accuracy": winner_acc,
            "winner_composite_fitness": winner_composite_fitness,
            "winner_evaluators": {"accuracy": winner_acc},
            "improved": True,
            "candidate_scores": candidate_scores,
        },
    )
    live_view = from_phase_event(event, ctx)
    assert isinstance(live_view, RoundCompleteView)

    # Disk path: build a trial dict shaped like ``RoundResult.model_dump()``
    # for the same round, then run from_disk_round.
    trial = {
        "trial_id": "round_0",
        "round": 0,
        "label": "round_0",
        "accuracy": winner_acc,
        "composite_fitness": winner_composite_fitness,
        "hits": winner_hits,
        "total": winner_total,
        "improved": True,
        "p_value": live_view.p_value,
        "baseline_accuracy": baseline_acc,
        "evaluators": {"accuracy": winner_acc},
        "candidate_scores": [
            _candidate_score_dict(
                candidate_id=f"cand_{i}",
                accuracy=cs["accuracy"],
                composite_fitness=cs["composite_fitness"],
                hits=cs["hits"],
                total=cs["total"],
            )
            for i, cs in enumerate(candidate_scores)
        ],
        "opt_search_point": {"l1_critique_text": ""},
    }
    disk_view = from_disk_round(
        trial,
        composite_fitness_formula=ctx["composite_fitness_formula"],
        composite_fitness_formula_short=ctx["composite_fitness_formula_short"],
        baseline_composite_fitness=ctx["baseline_composite_fitness"],
    )

    # The live view carries an in-memory ``next_action`` flag that the
    # runner uses for control flow but never persists. Strip it from
    # the live view so the comparison covers only the persisted contract.
    live_view = replace(live_view, next_action="")

    assert live_view == disk_view


def test_round_complete_view_no_improvement() -> None:
    """Round trip stays consistent when no candidate beats baseline."""
    baseline_acc = 0.50
    candidate_scores = [
        {
            "label": "C1",
            "accuracy": 0.40,
            "composite_fitness": 0.42,
            "hits": 8,
            "total": 20,
            "escalation_aborted": False,
        },
    ]
    ctx = {"round_num": 0, "baseline_accuracy": baseline_acc}
    event = PhaseEvent(
        phase="l1_score",
        event="exit",
        round=0,
        data={
            "winner_label": "C1",
            "winner_accuracy": 0.40,
            "winner_composite_fitness": 0.42,
            "winner_evaluators": {},
            "improved": False,
            "candidate_scores": candidate_scores,
        },
    )
    live_view = from_phase_event(event, ctx)
    assert isinstance(live_view, RoundCompleteView)

    trial = {
        "round": 0,
        "accuracy": 0.40,
        "composite_fitness": 0.42,
        "hits": 8,
        "total": 20,
        "improved": False,
        "p_value": None,
        "baseline_accuracy": baseline_acc,
        "evaluators": {},
        "candidate_scores": [
            _candidate_score_dict(
                candidate_id="cand_0",
                accuracy=0.40,
                composite_fitness=0.42,
                hits=8,
                total=20,
            )
        ],
        "opt_search_point": {"l1_critique_text": ""},
    }
    disk_view = from_disk_round(trial)

    live_view = replace(live_view, next_action="")

    # No improvement → delta is 0, p_value is None, both ends agree.
    assert live_view.improved is False
    assert live_view.delta == 0.0
    assert live_view.p_value is None
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
        LiveDashboardProjection(
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

    proj = LiveDashboardProjection(
        RootCycleDir(root_dir),
        session_dir,
        l1_patience=3,
        n_variants=5,
        sp_budget_ttest=20,
    )
    assert proj.state_path == root_dir / "dashboard.json"


def test_audit_trail_rejects_non_rounds_path(tmp_path: Path) -> None:
    """A rounds_dir must terminate in ``.runtime/cache/rounds`` — anything else is ad-hoc routing."""
    bad = tmp_path / "campaigns" / "cyc1"
    bad.mkdir(parents=True)
    with pytest.raises(ValueError, match=r"\.runtime/cache/rounds"):
        AuditTrailProjection(bad)


def test_audit_trail_from_cycle_dir_derives_subpath(tmp_path: Path) -> None:
    """The standard factory derives ``.runtime/cache/rounds`` from a cycle dir."""
    cycle_dir = tmp_path / "campaigns" / "cyc1"
    cycle_dir.mkdir(parents=True)
    proj = AuditTrailProjection.from_cycle_dir(CycleDir(cycle_dir))
    assert proj.rounds_dir == cycle_dir / ".runtime" / "cache" / "rounds"


def test_audit_trail_fork_dir_lands_under_fork(tmp_path: Path) -> None:
    """A fork's audit projection writes under the fork dir, never the parent root."""
    root_dir = tmp_path / "campaigns" / "root_xyz"
    fork_dir = root_dir / "forks" / "root_xyz_fork_abc"
    fork_dir.mkdir(parents=True)
    proj = AuditTrailProjection.from_cycle_dir(CycleDir(fork_dir))
    assert proj.rounds_dir == fork_dir / ".runtime" / "cache" / "rounds"
    assert root_dir not in proj.rounds_dir.parents or "forks" in proj.rounds_dir.parts


def test_audit_trail_on_record_handles_round_phase(tmp_path: Path) -> None:
    """Phase('round', 'enter')/'complete' from a ledger drives the recorder lifecycle.

    Phase 3 contract: an AuditTrailProjection bound to the ledger MUST
    react to round-boundary phase records — ``enter`` opens a fresh
    round, ``complete`` flushes ``round_NNNN.json`` to disk. Other
    record types are ignored at this projection's scope.
    """
    from promptpotter.domain.run_records import Decision, DecisionKind, Phase

    cycle_dir = tmp_path / "campaigns" / "cyc1"
    cycle_dir.mkdir(parents=True)
    proj = AuditTrailProjection.from_cycle_dir(CycleDir(cycle_dir))

    # Record an action so flush has state to write.
    proj.on_record(Phase(phase="round", event="enter", round=4), 0)
    proj.add_action({"type": "l1_generate", "response": "ok"})
    # An unrelated Decision must not crash or trigger a flush.
    proj.on_record(Decision(kind=DecisionKind.ROUND_WINNER, outcome="c1", round=4), 1)
    # Round-complete triggers the disk write.
    proj.on_record(Phase(phase="round", event="complete", round=4), 2)

    written = cycle_dir / ".runtime" / "cache" / "rounds" / "round_0004.json"
    assert written.exists(), "Phase('round', 'complete') must flush round_NNNN.json"


def test_audit_trail_persists_baseline_phase(tmp_path: Path) -> None:
    """Baseline LLM-call metadata must persist to ``round_baseline.json`` instead of being discarded.

    The baseline phase emits ``Phase('baseline', 'enter'|'exit')`` before
    the first round's enter/complete. Pre-fix, baseline node accumulation
    leaked into round 0's ``begin_round`` slot and was warn-and-discarded.
    """
    from promptpotter.domain.run_records import Phase

    cycle_dir = tmp_path / "campaigns" / "cyc_baseline"
    cycle_dir.mkdir(parents=True)
    proj = AuditTrailProjection.from_cycle_dir(CycleDir(cycle_dir))

    proj.on_record(Phase(phase="baseline", event="enter", round=0), 0)
    proj.add_action({"type": "llm_only", "response": "answer"})
    proj.on_record(Phase(phase="baseline", event="exit", round=0), 1)
    # Round 0 begins fresh — no warn-and-discard, no leakage of baseline.
    proj.on_record(Phase(phase="round", event="enter", round=0), 2)
    proj.add_action({"type": "l1_generate", "response": "ok"})
    proj.on_record(Phase(phase="round", event="complete", round=0), 3)

    baseline_path = cycle_dir / ".runtime" / "cache" / "rounds" / "round_baseline.json"
    round_0_path = cycle_dir / ".runtime" / "cache" / "rounds" / "round_0000.json"
    assert baseline_path.exists(), "baseline phase must flush to round_baseline.json"
    assert round_0_path.exists(), "round 0 must flush independently of baseline"
