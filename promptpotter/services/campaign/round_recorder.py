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

    def flush(self, state_snapshot: dict[str, Any] | None = None) -> Path | None:
        """Write the round file and reset. Returns the written path."""
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

        record: dict[str, Any] = {
            "round": self._current_round,
            "started_at": self._started_at,
            "finished_at": datetime.now(UTC).isoformat(),
            "actions": self._actions,
        }
        if state_snapshot:
            record["state_snapshot"] = state_snapshot

        write_json(path, record, default=str)
        logger.debug(
            "Round %d recorded: %d actions → %s",
            self._current_round,
            len(self._actions),
            filename,
        )

        self._actions = []
        self._has_escalation = False
        return path

    def record_round_outcome(
        self,
        round_result: Any,
        state: Any,
    ) -> Path | None:
        """Record eval + decision actions for a normal round, then flush.

        Encapsulates the serialization format so the optimization loop
        doesn't need to know the recorder's schema.
        """
        self.add_action(
            {
                "type": "l1_score",
                "n_candidates": round_result.candidates_scored,
                "n_queries": round_result.total,
                "candidates": round_result.candidate_scores,
            }
        )
        self.add_action(
            {
                "type": "decision",
                "winner": round_result.label,
                "accuracy": round_result.accuracy,
                "composite": round_result.composite,
                "improved": round_result.improved,
                "stall_count": state.stall_count,
                "winner_prompt_fields": round_result.prompt_fields,
                "winner_pipeline_params": round_result.pipeline_params,
            }
        )
        return self.flush(
            state_snapshot={
                "opt_search_point_id": state.opt_sp.id,
                "l2_directive": state.opt_sp.l2_directive or "",
                "escalation_counters": {
                    "l2_stall": state.escalation.l2_stall_count,
                    "l3_stall": state.escalation.l3_stall_count,
                    "l2_round": state.escalation.l2_round,
                    "l3_round": state.escalation.l3_round,
                },
            }
        )
