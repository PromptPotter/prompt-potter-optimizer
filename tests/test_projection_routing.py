"""Projection routing — newtype-guarded write targets.

Each projection in ``promptpotter/infrastructure/projections/`` accepts a
specific dir newtype. ``LiveDashboardProjection`` takes ``RootCycleDir``
(the family-root path, never a fork's nested dir). ``AuditTrailProjection``
takes ``CycleDir`` via ``from_cycle_dir`` and derives the
``.cache/rounds`` subpath, or accepts a raw rounds_dir path that MUST end
in ``.cache/rounds``. A runtime assertion in each ``__init__`` rejects a
mismatched path so a fork can never accidentally write to the parent's
tree (and a parent can never write per-cycle audit data into the
family-root telemetry stream).
"""

from __future__ import annotations

from pathlib import Path

import pytest

from promptpotter.domain.cycle_paths import CycleDir, RootCycleDir
from promptpotter.infrastructure.projections import (
    AuditTrailProjection,
    LiveDashboardProjection,
)


def test_live_dashboard_rejects_fork_path(tmp_path: Path) -> None:
    """A fork dir (containing 'forks/' segment) cannot host the live dashboard."""
    fork_dir = tmp_path / "campaigns" / "root_xyz" / "forks" / "root_xyz_fork_abc"
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
        assert proj.log_path == root_dir / "output.log"
    finally:
        proj.finalize()


def test_audit_trail_rejects_non_rounds_path(tmp_path: Path) -> None:
    """A rounds_dir must terminate in ``.cache/rounds`` — anything else is ad-hoc routing."""
    bad = tmp_path / "campaigns" / "cyc1"
    bad.mkdir(parents=True)
    with pytest.raises(ValueError, match="\\.cache/rounds"):
        AuditTrailProjection(bad)


def test_audit_trail_from_cycle_dir_derives_subpath(tmp_path: Path) -> None:
    """The standard factory derives ``.cache/rounds`` from a cycle dir."""
    cycle_dir = tmp_path / "campaigns" / "cyc1"
    cycle_dir.mkdir(parents=True)
    proj = AuditTrailProjection.from_cycle_dir(CycleDir(cycle_dir))
    assert proj.rounds_dir == cycle_dir / ".cache" / "rounds"


def test_audit_trail_fork_dir_lands_under_fork(tmp_path: Path) -> None:
    """A fork's audit projection writes under the fork dir, never the parent root."""
    root_dir = tmp_path / "campaigns" / "root_xyz"
    fork_dir = root_dir / "forks" / "root_xyz_fork_abc"
    fork_dir.mkdir(parents=True)
    proj = AuditTrailProjection.from_cycle_dir(CycleDir(fork_dir))
    assert proj.rounds_dir == fork_dir / ".cache" / "rounds"
    assert root_dir not in proj.rounds_dir.parents or "forks" in proj.rounds_dir.parts
