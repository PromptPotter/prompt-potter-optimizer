"""Campaign control surface — bidirectional HITL signals.

``campaign_control.json`` carries ``requested_state`` (pause / resume / stop)
and ``pause_before_l2_scoring``.  Writers: CLI ``control`` command, webapp,
and hand-edits.  Reader: the optimization loop at natural checkpoints.

Kept in its own file (separate from ``campaign_state.json``) so the emitter
never races user intent edits on a hot code path — see
``session_emitter.py``.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

from promptpotter.infrastructure.store.base import write_json

logger = logging.getLogger(__name__)

CONTROL_FILENAME = "campaign_control.json"

__all__ = ["CONTROL_FILENAME", "CampaignControlReader", "ensure_control_file"]


def ensure_control_file(session_dir: Path, *, pause_before_scoring: bool) -> None:
    """Seed ``campaign_control.json`` with defaults if it doesn't exist.

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


class CampaignControlReader:
    """Reads control signals from ``campaign_control.json`` at checkpoints.

    Accepts either the session directory or the control-file path directly.
    The emitter seeds the file with defaults on init; the CLI ``control``
    command and (eventually) the webapp are the writers.  This class reads
    the file, and — for ``resume`` — writes it back to ``running`` to
    acknowledge the signal.  Returns ``"pause"`` or ``"stop"`` when the user
    requested it, else ``None``.
    """

    def __init__(self, path: Path) -> None:
        # Accept either the session directory or the control file itself.
        self.control_path = path / CONTROL_FILENAME if path.is_dir() else path

    def check(self, checkpoint_name: str) -> str | None:
        """Read the control file. Returns action or None.

        Called at natural checkpoints (after_round, before_l2, before_l3).
        """
        try:
            control = json.loads(self.control_path.read_text(encoding="utf-8"))
        except (FileNotFoundError, json.JSONDecodeError):
            return None

        requested = control.get("requested_state", "running")

        if requested == "resume":
            # Acknowledge resume: overwrite to running
            control["requested_state"] = "running"
            write_json(self.control_path, control)
            logger.info("Control: resume acknowledged at %s", checkpoint_name)
            return None

        if requested == "pause":
            logger.info("Control: pause requested at %s", checkpoint_name)
            return "pause"

        if requested == "stop":
            logger.info("Control: stop requested at %s", checkpoint_name)
            return "stop"

        # Check L2-specific pause
        if checkpoint_name == "before_l2_scoring" and control.get("pause_before_l2_scoring"):
            logger.info("Control: pause_before_l2_scoring active at %s", checkpoint_name)
            return "pause"

        return None
