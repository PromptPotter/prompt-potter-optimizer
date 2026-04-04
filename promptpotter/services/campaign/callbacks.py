"""Callback type aliases and CycleCallbacks dataclass for the optimization loop."""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING, TypeAlias

from promptpotter.models.phase_event import PhaseEvent

if TYPE_CHECKING:
    from promptpotter.services.campaign.results import CycleRoundResult

__all__ = [
    "CycleCallbacks",
    "OnCandidateEval",
    "OnCheckpoint",
    "OnPhase",
    "OnQueryEval",
    "OnRoundComplete",
    "chain_callbacks",
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


def chain_callbacks(a: CycleCallbacks, b: CycleCallbacks) -> CycleCallbacks:
    """Compose two CycleCallbacks so both fire on every event.

    ``a`` fires first (typically persistence), ``b`` second (typically display).
    For ``on_checkpoint``, returns the first non-None result.
    """

    def _chain(fn_a: Callable | None, fn_b: Callable | None) -> Callable | None:
        if fn_a and fn_b:
            return lambda *args, **kw: (fn_a(*args, **kw), fn_b(*args, **kw))
        return fn_a or fn_b

    def _chain_checkpoint(
        fn_a: Callable | None, fn_b: Callable | None,
    ) -> Callable | None:
        """For on_checkpoint: return the first non-None result."""
        if fn_a and fn_b:
            def _both(*args: object, **kw: object) -> str | None:
                r = fn_a(*args, **kw)
                if r is not None:
                    return r
                return fn_b(*args, **kw)
            return _both
        return fn_a or fn_b

    return CycleCallbacks(
        on_round_complete=_chain(a.on_round_complete, b.on_round_complete),
        on_candidate_eval=_chain(a.on_candidate_eval, b.on_candidate_eval),
        on_query_eval=_chain(a.on_query_eval, b.on_query_eval),
        on_phase=_chain(a.on_phase, b.on_phase),
        on_checkpoint=_chain_checkpoint(a.on_checkpoint, b.on_checkpoint),
    )
