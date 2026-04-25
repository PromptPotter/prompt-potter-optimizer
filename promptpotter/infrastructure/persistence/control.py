"""Campaign control surface — bidirectional HITL signals.

``control.json`` carries ``requested_state`` (pause / resume / stop)
and ``pause_before_l2_scoring``.  Writers: CLI ``control`` command, webapp,
and hand-edits.  Reader: the optimization loop at natural checkpoints
(via :func:`make_control_check`).

Kept in its own file (separate from ``dashboard.json``) so the emitter
never races user intent edits on a hot code path — see
``session_emitter.py``.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Callable
from pathlib import Path

from promptpotter.infrastructure.store.base import write_json

logger = logging.getLogger(__name__)

CONTROL_FILENAME = "control.json"

__all__ = ["CONTROL_FILENAME", "ensure_control_file", "make_control_check"]


def ensure_control_file(session_dir: Path, *, pause_before_scoring: bool) -> None:
    """Seed ``control.json`` with defaults if it doesn't exist.

    A lingering pause from a previous session survives ``init`` — the file
    is only written when missing, never overwritten.
    """
    path = session_dir / CONTROL_FILENAME
    if path.exists():
        return
    write_json(
        path,
        {
            "requested_state": "running",
            "pause_before_l2_scoring": pause_before_scoring,
        },
    )


def make_control_check(path: Path) -> Callable[[str], str | None]:
    """Return a checkpoint reader for ``control.json``.

    Accepts either the session directory or the control file itself. The
    returned callable is invoked at natural checkpoints (after_round,
    before_l2_scoring, ...) and returns:

    - ``"pause"`` / ``"stop"`` when the user requested it,
    - ``None`` otherwise.

    On a pending ``resume`` it overwrites the file back to ``running`` to
    acknowledge the signal — same write-back contract as the prior class.
    """
    control_path = path / CONTROL_FILENAME if path.is_dir() else path

    def check(checkpoint_name: str) -> str | None:
        try:
            control = json.loads(control_path.read_text(encoding="utf-8"))
        except (FileNotFoundError, json.JSONDecodeError):
            return None

        requested = control.get("requested_state", "running")

        if requested == "resume":
            control["requested_state"] = "running"
            write_json(control_path, control)
            logger.info("Control: resume acknowledged at %s", checkpoint_name)
            return None

        if requested in ("pause", "stop"):
            logger.info("Control: %s requested at %s", requested, checkpoint_name)
            return requested

        if checkpoint_name == "before_l2_scoring" and control.get("pause_before_l2_scoring"):
            logger.info("Control: pause_before_l2_scoring active at %s", checkpoint_name)
            return "pause"

        return None

    return check
