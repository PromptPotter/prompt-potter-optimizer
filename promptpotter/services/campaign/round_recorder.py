"""Per-round action trace recorder.

Accumulates L1 / L2 / L3 actions within a single round and flushes them
atomically to ``{session_dir}/rounds/round_NNN{_l2|_l3}.json``.  The round
file is the source of truth for optimizer *layer* decisions that the
``campaign_store`` trial files don't capture — reviewing a round means
reading this file.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from promptpotter.services.store.base import write_json

logger = logging.getLogger(__name__)

__all__ = ["RoundRecorder"]


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
        if action.get("type") in ("l2_refine_strategy", "l3_modify_plan"):
            self._has_escalation = True
        self._actions.append(action)

    def flush(self) -> Path | None:
        """Write the round file and reset. Returns the written path.

        Round files are the LLM-call audit trail — every ``add_action`` from
        ``pipeline.py``'s ``llm_call()`` during the round. Round *outcome*
        (accuracy, hits, decision, opt_sp) lives in ``trial_NNNN.json`` via
        ``_checkpoint_round`` — this file is not the place for that data.
        """
        if not self._actions:
            return None

        self.rounds_dir.mkdir(parents=True, exist_ok=True)

        suffix = ""
        if self._has_escalation:
            for a in self._actions:
                if a.get("type") == "l2_refine_strategy":
                    suffix = "_l2"
                    break
                if a.get("type") == "l3_modify_plan":
                    suffix = "_l3"
                    break

        filename = f"round_{self._current_round:03d}{suffix}.json"
        path = self.rounds_dir / filename

        write_json(
            path,
            {
                "round": self._current_round,
                "started_at": self._started_at,
                "finished_at": datetime.now(UTC).isoformat(),
                "actions": self._actions,
            },
            default=str,
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
