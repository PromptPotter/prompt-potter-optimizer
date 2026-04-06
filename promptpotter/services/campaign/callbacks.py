"""Callback types and event helpers shared across the campaign package.

RunCallbacks, chain_callbacks, callback type aliases, and emit_phase.
Used by optimization_loop, round_execution, escalation, and lifecycle.
"""

import logging
from collections.abc import Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, TypeAlias

from promptpotter.models.phase_event import PhaseEvent

if TYPE_CHECKING:
    from promptpotter.services.campaign.state import LoopState, RoundResult

logger = logging.getLogger(__name__)

__all__ = [
    "OnCandidateEval",
    "OnCheckpoint",
    "OnPhase",
    "OnQueryEval",
    "OnRoundComplete",
    "RunCallbacks",
    "chain_callbacks",
    "emit_phase",
    "get_obs_trace",
]

# ---------------------------------------------------------------------------
# Callback type aliases (documented parameter semantics)
# ---------------------------------------------------------------------------

# (round_result, round_number)
OnRoundComplete: TypeAlias = Callable[["RoundResult", int], None]
# (candidate_index, total_candidates, scores_dict)
OnCandidateEval: TypeAlias = Callable[[int, int, dict], None]
# (candidate_index, total_candidates, query_index, total_queries, result_dict)
OnQueryEval: TypeAlias = Callable[[int, int, int, int, dict], None]
# (phase_event)
OnPhase: TypeAlias = Callable[[PhaseEvent], None]
# (checkpoint_name) -> "pause" | "stop" | None
OnCheckpoint: TypeAlias = Callable[[str], str | None]


@dataclass
class RunCallbacks:
    """Optional progress callbacks for the feedback cycle."""

    on_round_complete: OnRoundComplete | None = None
    on_candidate_eval: OnCandidateEval | None = None
    on_query_eval: OnQueryEval | None = None
    on_phase: OnPhase | None = None
    on_checkpoint: OnCheckpoint | None = None


def chain_callbacks(a: RunCallbacks, b: RunCallbacks) -> RunCallbacks:
    """Compose two RunCallbacks so both fire on every event.

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

    return RunCallbacks(
        on_round_complete=_chain(a.on_round_complete, b.on_round_complete),
        on_candidate_eval=_chain(a.on_candidate_eval, b.on_candidate_eval),
        on_query_eval=_chain(a.on_query_eval, b.on_query_eval),
        on_phase=_chain(a.on_phase, b.on_phase),
        on_checkpoint=_chain_checkpoint(a.on_checkpoint, b.on_checkpoint),
    )


# ---------------------------------------------------------------------------
# Utility functions
# ---------------------------------------------------------------------------


def emit_phase(
    on_phase: Callable[[PhaseEvent], None] | None,
    phase: str,
    event: str,
    *,
    round: int | None = None,
    **data: Any,
) -> None:
    """Construct a PhaseEvent and call the callback if set."""
    if on_phase is None:
        return
    pe = PhaseEvent(phase=phase, event=event, round=round, data=data)
    on_phase(pe)


def get_obs_trace(
    state: "LoopState", obs_campaign_id: str,
) -> tuple[Any | None, str | None]:
    """Extract obs logger and trace_id from loop state."""
    obs = state.eval_ctx.obs if state.eval_ctx else None
    trace_id = obs.get_file_trace_id(obs_campaign_id) if obs else None
    return obs, trace_id


