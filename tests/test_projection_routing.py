"""Projection routing — newtype-guarded write targets.

Each projection in ``promptpotter/infrastructure/projections/`` accepts a
specific dir newtype. ``LiveDashboardProjection`` takes ``RootCycleDir``
(the family-root path, never a sibling dir under ``forks/``, ``diag/``, or
``sweeps/``). ``AuditTrailProjection`` takes ``CycleDir`` via
``from_cycle_dir`` and derives the ``.runtime/cache/rounds`` subpath, or
accepts a raw rounds_dir path that MUST end in ``.runtime/cache/rounds``.
A runtime assertion in each ``__init__`` rejects a mismatched path so a
fork can never accidentally write to the parent's tree (and a parent can
never write per-cycle audit data into the family-root telemetry stream).
"""

from __future__ import annotations

from pathlib import Path

import pytest

from promptpotter.domain.cycle_paths import CycleDir, RootCycleDir
from promptpotter.infrastructure.projections import (
    AuditTrailProjection,
    LiveDashboardProjection,
)


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
    try:
        assert proj.state_path == root_dir / "dashboard.json"
    finally:
        proj.finalize()


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
