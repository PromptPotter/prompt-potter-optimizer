"""Small utilities shared across the campaign package.

These are used by optimization_loop, round_execution, escalation, and
campaign_lifecycle — extracted here to keep each module focused on
its primary responsibility.
"""

import asyncio
import logging
from collections.abc import Callable
from contextlib import contextmanager
from typing import TYPE_CHECKING, Any

from api.models.phase_event import PhaseEvent
from api.shared.constants import PROMPT_STRING_FIELDS

if TYPE_CHECKING:
    from api.services.campaign.state import LoopState

logger = logging.getLogger(__name__)


@contextmanager
def graceful(msg: str):
    """Suppress non-interrupt exceptions with a warning log."""
    try:
        yield
    except (KeyboardInterrupt, asyncio.CancelledError):
        raise
    except Exception:
        logger.warning(msg, exc_info=True)


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


def candidate_summaries(
    candidates: list[dict],
    current_prompt_fields: dict,
) -> list[dict]:
    """Build compact per-candidate summary dicts for phase event data."""
    summaries = []
    for i, c in enumerate(candidates):
        pp_override = c.get("__pipeline_params_override__")
        changed_fields = [
            f for f in PROMPT_STRING_FIELDS
            if c.get(f, "") != current_prompt_fields.get(f, "")
        ]
        summary: dict = {
            "idx": i,
            "changes_description": c.get("changes_description", ""),
            "changed_fields": changed_fields,
        }
        if pp_override:
            summary["pipeline_params_override"] = pp_override
        summaries.append(summary)
    return summaries
