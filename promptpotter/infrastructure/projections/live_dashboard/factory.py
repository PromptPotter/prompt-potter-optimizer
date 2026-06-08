"""Bootstrap helpers for ``LiveDashboardView.for_session``.

The factory classmethod resolves the cycle's own dir, reads any prior
``dashboard.json`` (from a seed dir — the cycle's own, or the parent's for a
fork), and self-heals its stale ``round`` / ``best`` pointers against what
completed-round checkpoints actually exist on disk. That disk-reconciliation
logic lives here as a free function so the classmethod stays a thin assembly
of: resolve ids → resolve resume state → construct.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any


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

    ``best`` is the rolling-max composite for the active cycle. The prior
    ``dashboard.json`` may hold a number from an earlier run. That number is
    not surfaced until the active cycle has produced an on-disk round to back
    it; otherwise every percentage chip shows a value the loop will never
    confirm. The stale ``round`` pointer is self-healed in the same pass — both
    signals depend on ``disk_round``.
    """
    prior_state = seed_dir / "dashboard.json"
    if not prior_state.exists():
        return None
    try:
        resume_from: dict[str, Any] = json.loads(prior_state.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None

    disk_round = _max_round_on_disk(active_cycle_dir / "rounds")
    if disk_round > int(resume_from.get("round") or 0):
        resume_from["round"] = disk_round
    if disk_round == 0 or (resumed_from_round is not None and max(resumed_from_round - 1, 0) == 0):
        resume_from["best"] = 0.0
    return resume_from


__all__ = ["resolve_resume_state"]
