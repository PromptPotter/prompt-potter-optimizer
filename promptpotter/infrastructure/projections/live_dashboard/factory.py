"""Bootstrap helpers for ``LiveDashboardView.for_session``.

The factory classmethod resolves the cycle's own dir, reads any prior
``dashboard.json`` (from a seed dir — the cycle's own, or the parent's for a
fork), and self-heals its stale ``round`` / ``best`` pointers against what
completed-round checkpoints actually exist on disk. That disk-reconciliation
logic lives here as a free function so the classmethod stays a thin assembly
of: resolve ids → resolve resume state → construct.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from promptpotter.domain.results import best_round_by_cumulative_accuracy
from promptpotter.infrastructure.store.io import read_json_tolerant


def _max_round_on_disk(rounds_dir: Path) -> int:
    """Highest ``round_NNNN.json`` index in ``rounds_dir``, or ``0`` if none."""
    if not rounds_dir.is_dir():
        return 0
    highest = 0
    for path in rounds_dir.glob("round_*.json"):
        stem = path.stem  # ``round_0003``
        suffix = stem[len("round_") :]
        if suffix.isdigit():
            highest = max(highest, int(suffix))
    return highest


def resolve_resume_state(
    seed_dir: Path,
    active_cycle_dir: Path,
    resumed_from_round: int | None,
) -> dict[str, Any] | None:
    """Read the prior ``dashboard.json`` and self-heal its round/best pointers.

    Returns the resume payload for the :class:`LiveDashboardView` constructor,
    or ``None`` when no prior file exists (or it is unreadable).

    ``seed_dir`` is where the prior ``dashboard.json`` is read from — the
    cycle's own dir on a root/resume, or the parent cycle's dir on a fork (the
    fork inherits the parent's trajectory up to the cut). ``active_cycle_dir``
    is always the cycle's own dir; its ``rounds/`` checkpoints drive the
    round-pointer self-heal. Origin rides ``rounds[0]`` like any round, so the
    seeded ``rounds`` already carries it — no separate origin reconciliation.

    ``best`` is re-derived from the SURVIVING trajectory via the shared
    ``best_round_by_cumulative_accuracy`` (the cycle index's ``_apply_best`` rides the
    same helper) — never trusted from the prior scalar. The live writer's
    rolling max is monotonic, so a stale high-water from a rewound-away round
    would otherwise survive forever and disagree with
    ``index.json::best_accuracy``. Rounds ``>= resumed_from_round`` are about
    to be overwritten (the view clamps them at L1_GENERATE enter), so they
    don't back the headline. The stale ``round`` pointer is self-healed in the
    same pass.
    """
    resume_from = read_json_tolerant(seed_dir / "dashboard.json")
    if not isinstance(resume_from, dict):
        return None

    disk_round = _max_round_on_disk(active_cycle_dir / "rounds")
    if disk_round > int(resume_from.get("round") or 0):
        resume_from["round"] = disk_round
    surviving = [
        r
        for r in resume_from.get("rounds") or []
        if isinstance(r, dict)
        and (resumed_from_round is None or int(r.get("round") or 0) < resumed_from_round)
    ]
    resume_from["best"], _ = best_round_by_cumulative_accuracy(surviving)
    return resume_from


__all__ = ["resolve_resume_state"]
