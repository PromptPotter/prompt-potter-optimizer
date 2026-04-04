"""Round-level session corpus — one JSON per round with full action trace.

Each round file contains an ordered array of actions: LLM calls (with
template + variables decomposed), evaluation results, critique, decisions,
HITL pauses, and escalation transitions. The session's ``rounds/``
directory is the complete inspectable record of the optimization.
"""

from __future__ import annotations

import json
import logging
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


class RoundRecorder:
    """Accumulates actions within a round, writes ``round_NNN.json`` on flush."""

    def __init__(self, rounds_dir: Path) -> None:
        self.rounds_dir = rounds_dir
        self._current_round: int = 0
        self._actions: list[dict[str, Any]] = []
        self._started_at: str = ""
        self._has_escalation = False

    def begin_round(self, round_num: int) -> None:
        """Start recording a new round. Flushes any pending actions."""
        if self._actions:
            logger.warning(
                "RoundRecorder: unflushed actions from round %d discarded",
                self._current_round,
            )
        self._current_round = round_num
        self._actions = []
        self._started_at = datetime.now(UTC).isoformat()
        self._has_escalation = False

    def add_action(self, action: dict[str, Any]) -> None:
        """Append an action to the current round's trace."""
        action.setdefault("timestamp", datetime.now(UTC).isoformat())
        if action.get("type") in ("l2_refine_context", "l3_modify_plan"):
            self._has_escalation = True
        self._actions.append(action)

    def flush(self, state_snapshot: dict[str, Any] | None = None) -> Path | None:
        """Write the round file and reset. Returns the written path."""
        if not self._actions:
            return None

        self.rounds_dir.mkdir(parents=True, exist_ok=True)

        suffix = ""
        if self._has_escalation:
            for a in self._actions:
                if a.get("type") == "l2_refine_context":
                    suffix = "_l2"
                    break
                if a.get("type") == "l3_modify_plan":
                    suffix = "_l3"
                    break

        filename = f"round_{self._current_round:03d}{suffix}.json"
        path = self.rounds_dir / filename

        record = {
            "round": self._current_round,
            "started_at": self._started_at,
            "finished_at": datetime.now(UTC).isoformat(),
            "actions": self._actions,
        }
        if state_snapshot:
            record["state_snapshot"] = state_snapshot

        path.write_text(
            json.dumps(record, indent=2, default=str, ensure_ascii=False),
            encoding="utf-8",
        )
        logger.debug(
            "Round %d recorded: %d actions → %s",
            self._current_round,
            len(self._actions),
            filename,
        )

        self._actions = []
        self._has_escalation = False
        return path
