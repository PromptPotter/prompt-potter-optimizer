"""Callback type aliases and CycleCallbacks dataclass for the optimization loop."""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING, TypeAlias

from api.models.phase_event import PhaseEvent

if TYPE_CHECKING:
    from api.services.campaign.results import CycleRoundResult

__all__ = [
    "CycleCallbacks",
    "OnCandidateEval",
    "OnCheckpoint",
    "OnPhase",
    "OnQueryEval",
    "OnRoundComplete",
]

# -- Callback type aliases (documented parameter semantics) ----------------

# (round_result, round_number)
OnRoundComplete: TypeAlias = Callable[["CycleRoundResult", int], None]
# (candidate_index, total_candidates, scores_dict)
OnCandidateEval: TypeAlias = Callable[[int, int, dict], None]
# (candidate_index, total_candidates, query_index, total_queries, result_dict)
OnQueryEval: TypeAlias = Callable[[int, int, int, int, dict], None]
# (phase_event)
OnPhase: TypeAlias = Callable[[PhaseEvent], None]
# (checkpoint_name) -> "pause" | "stop" | None
OnCheckpoint: TypeAlias = Callable[[str], str | None]


@dataclass
class CycleCallbacks:
    """Optional progress callbacks for the feedback cycle."""

    on_round_complete: OnRoundComplete | None = None
    on_candidate_eval: OnCandidateEval | None = None
    on_query_eval: OnQueryEval | None = None
    on_phase: OnPhase | None = None
    on_checkpoint: OnCheckpoint | None = None
