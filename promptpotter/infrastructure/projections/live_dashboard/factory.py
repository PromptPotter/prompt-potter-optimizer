"""Construction helper for ``LiveDashboardView.for_session``: resolve ids, resolve resume state, construct. The disk
reconciliation lives here as a free function so the classmethod stays a thin assembly."""

from __future__ import annotations

import logging
from pathlib import Path

from pydantic import ValidationError

from promptpotter.domain.results import best_round_by_measured_accuracy
from promptpotter.infrastructure.projections.live_dashboard.state import LiveDashboardState
from promptpotter.infrastructure.store.io import read_json_tolerant
from promptpotter.infrastructure.store.layout import CycleLayout

__all__ = ["resolve_resume_state"]

logger = logging.getLogger(__name__)


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

    try:
        prior = LiveDashboardState.model_validate(raw)
    except ValidationError:
        # A PROJECTION may not kill the run it seeds. This model is `extra="forbid"`, so a
        # dashboard written by an earlier build fails here on the one field that moved — and
        # uncaught, that error propagated out of `for_session` and took down the resume whose
        # whole job was to not lose the cycle. The LEDGER is the truth and it is untouched;
        # everything this file adds on top (the spend rollup, the counters, the trajectory) is
        # re-derived forward from the next record. Dropped whole and loudly rather than salvaged
        # field by field — a partial read is a compatibility shim, and there is nothing here to
        # be compatible with.
        logger.exception("unreadable dashboard.json at %s — resuming without its state", seed_dir)
        return None
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
