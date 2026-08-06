"""Construction helper for ``LiveDashboardView.for_session``: resolve ids, resolve resume state, construct. The disk
reconciliation lives here as a free function so the classmethod stays a thin assembly."""

from __future__ import annotations

from pathlib import Path

from promptpotter.domain.results import best_round_by_measured_accuracy
from promptpotter.infrastructure.projections.live_dashboard.state import LiveDashboardState
from promptpotter.infrastructure.store.io import read_json_tolerant
from promptpotter.infrastructure.store.layout import CycleLayout

__all__ = ["resolve_resume_state"]


def _max_round_on_disk(rounds_dir: Path) -> int:
    if not rounds_dir.is_dir():
        return 0
    highest = 0
    for path in rounds_dir.glob("round_*.json"):
        suffix = path.stem[len("round_") :]  # ``round_0003`` → ``0003``
        if suffix.isdigit():
            highest = max(highest, int(suffix))
    return highest


def resolve_resume_state(
    seed_dir: Path,
    active_cycle_dir: Path,
    resumed_from_round: int | None,
) -> LiveDashboardState | None:
    """Seed state from the prior ``dashboard.json``. **The one place the trajectory is cut**: rounds at or
    past ``resumed_from_round`` drop and ``best`` is RE-DERIVED — a carried rolling max keeps a dead peak."""
    raw = read_json_tolerant(CycleLayout(seed_dir).dashboard)
    if not isinstance(raw, dict):
        return None

    prior = LiveDashboardState.model_validate(raw)
    surviving = [
        r for r in prior.rounds if resumed_from_round is None or r.round < resumed_from_round
    ]
    best, _ = best_round_by_measured_accuracy([r.model_dump() for r in surviving])
    return prior.model_copy(
        update={
            "rounds": surviving,
            "round": max(prior.round, _max_round_on_disk(CycleLayout(active_cycle_dir).rounds)),
            "best": best,
        }
    )
