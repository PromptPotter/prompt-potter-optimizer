"""Bidirectional control surface — reads control signals from campaign_state.json.

Separated from persistence: the emitter writes state, the control surface reads
commands back. Entry points (CLI, web app) opt in by providing a control surface
as ``on_checkpoint``; the notebook uses kernel interrupt instead.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

logger = logging.getLogger(__name__)

__all__ = ["FileControlSurface"]


class FileControlSurface:
    """Reads control signals from ``campaign_state.json`` at checkpoints.

    The persistence emitter writes the file; this class only reads the
    ``control`` section back.  Returns ``"pause"`` or ``"stop"`` when the
    user requested it, else ``None``.
    """

    def __init__(self, state_path: Path) -> None:
        self.state_path = state_path

    def check(self, checkpoint_name: str) -> str | None:
        """Read control section. Returns action or None.

        Called at natural checkpoints (after_round, before_l2, before_l3).
        """
        try:
            data = json.loads(self.state_path.read_text(encoding="utf-8"))
        except (FileNotFoundError, json.JSONDecodeError):
            return None

        control = data.get("control", {})
        requested = control.get("requested_state", "running")

        if requested == "resume":
            # Acknowledge resume: overwrite control to running
            data["control"]["requested_state"] = "running"
            self.state_path.write_text(
                json.dumps(data, indent=2, default=str), encoding="utf-8",
            )
            logger.info("Control: resume acknowledged at %s", checkpoint_name)
            return None

        if requested == "pause":
            logger.info("Control: pause requested at %s", checkpoint_name)
            return "pause"

        if requested == "stop":
            logger.info("Control: stop requested at %s", checkpoint_name)
            return "stop"

        # Check L2-specific pause
        if checkpoint_name == "before_l2_eval" and control.get("pause_before_l2_eval"):
            logger.info("Control: pause_before_l2_eval active at %s", checkpoint_name)
            return "pause"

        return None
