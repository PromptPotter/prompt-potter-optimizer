"""Bootstrap helpers for ``LiveDashboardView.for_session``.

The factory classmethod resolves a session-family dir, reads any prior
``dashboard.json``, and self-heals its stale ``round`` / ``best`` pointers
against what completed-round checkpoints actually exist on disk. That
disk-reconciliation logic lives here as a free function so the classmethod
stays a thin assembly of: resolve ids → resolve resume state → construct.
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
    family_dir: Path,
    active_cycle_dir: Path,
    origin_accuracy: float,
    resumed_from_round: int | None,
) -> dict[str, Any] | None:
    """Read the prior ``dashboard.json`` and self-heal its round/best pointers.

    Returns the resume payload for the :class:`LiveDashboardView` constructor,
    or ``None`` when no prior file exists (or it is unreadable).

    ``best`` is the rolling-max composite for the active cycle. The prior
    ``dashboard.json`` may hold a number from an earlier run of the same
    session (resume re-instantiates the projection against the same
    session-family dir). That number is not surfaced until the active cycle
    has produced an on-disk round to back it; otherwise every percentage chip
    shows a value the loop will never confirm. The stale ``round`` pointer is
    self-healed in the same pass — both signals depend on ``disk_round``.
    """
    prior_state = family_dir / "dashboard.json"
    if not prior_state.exists():
        return None
    try:
        resume_from: dict[str, Any] = json.loads(prior_state.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None

    origin_block = dict(resume_from.get("origin") or {})
    origin_block["accuracy"] = origin_accuracy
    # samples stays whatever the prior dashboard recorded; INIT:exit will
    # refresh both fields once the new run hits origin scoring.
    resume_from["origin"] = origin_block
    disk_round = _max_round_on_disk(active_cycle_dir / "rounds")
    if disk_round > int(resume_from.get("round") or 0):
        resume_from["round"] = disk_round
    if disk_round == 0 or (resumed_from_round is not None and max(resumed_from_round - 1, 0) == 0):
        resume_from["best"] = 0.0
    return resume_from


__all__ = ["resolve_resume_state"]
