from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING, cast

from promptpotter.application.optimization.dispatch.facade import (
    DispatchHub,
    build_bundle,
    injection_char_counts,
    injection_coverage_counts,
    injection_silent_panels,
)
from promptpotter.application.optimization.dispatch.llm_call.call import (
    LLMCallContext,
    run_optimizer_node,
)
from promptpotter.application.optimization.dispatch.llm_call.prompts import (
    load_optimizer_prompt,
)
from promptpotter.application.optimization.dispatch.schemas import L1CritiqueOutput
from promptpotter.application.run_phase_control import declare_run_phase
from promptpotter.domain.phases import RunPhase, StopLoop, StopReason
from promptpotter.domain.results import CritiqueReadout
from promptpotter.infrastructure.llm.telemetry import emit_round_warning
from promptpotter.shared.errors import graceful

if TYPE_CHECKING:
    from promptpotter.application.optimization.cycle import Cycle
    from promptpotter.domain.results import RoundResult
    from promptpotter.infrastructure.ledger import CycleEventLog

logger = logging.getLogger(__name__)

__all__ = [
    "ensure_prior_critique",
    "run_l1_critique",
]


CRITIQUE_RESEND_ATTEMPTS = 3
"""Distillations one round will buy before the cycle halts. Each is a whole call — ``llm_call``'s
own deadline retry and 429 backoff sit INSIDE one attempt and do not count against this."""


async def ensure_prior_critique(cycle: Cycle) -> None:
    """Re-send the previous round's critique when it has none, and HALT if it never arrives.

    ``critique`` is ``L1_MANDATORY``: without one the generator is asked to fix a prompt nothing
    told it how to fix, so running the round anyway spends a full panel on a choice made blind.
    Two ways the panel comes up empty, both covered here and neither stated on any other channel —
    the producer skips the last round of an invocation, which a ``resume`` walks straight past,
    and a terminal provider failure there is absorbed so the round can still close.

    The stop is ``PAUSED``, the same resumable halt a holed panel takes: nothing is lost, and the
    operator resumes into a round that re-sends against a provider that has recovered."""
    prior = cycle.rounds[-1] if cycle.rounds else None
    if prior is None or prior.round == 0 or prior.critique or not prior.results:
        return
    session = cycle.session
    last: Exception | None = None
    for attempt in range(1, CRITIQUE_RESEND_ATTEMPTS + 1):
        try:
            prior.critique = await run_l1_critique(
                cycle, prior, round_num=prior.round, ledger=session.state.ledger
            )
            break
        except (KeyboardInterrupt, asyncio.CancelledError):
            raise
        except Exception as exc:
            last = exc
            logger.warning(
                "round %d critique re-send %d/%d failed: %s",
                prior.round,
                attempt,
                CRITIQUE_RESEND_ATTEMPTS,
                exc,
            )
    if not prior.critique:
        emit_round_warning(
            kind="l1_critique_unavailable",
            message=(
                f"round {prior.round} produced no critique and {CRITIQUE_RESEND_ATTEMPTS} re-sends "
                f"failed — halting rather than deciding this round with its mandatory critique "
                f"panel empty; `resume` re-sends it"
            ),
            severity="error",
            detail={
                "prior_round": prior.round,
                "attempts": CRITIQUE_RESEND_ATTEMPTS,
                "error": str(last)[:200],
            },
        )
        declare_run_phase(session, RunPhase.PAUSED)
        raise StopLoop(StopReason.PAUSED)
    # On disk, or the next resume re-sends a call this one already paid for — and the round file
    # would keep saying the generator had no steer when it did.
    if session.state.cycle_id:
        with graceful(f"round {prior.round} critique re-send not persisted"):
            session.store.campaigns.save_round_file(session.hop, prior)
    logger.info("Round %d critique distilled late; this round's generator reads it.", prior.round)


async def run_l1_critique(
    cycle: Cycle,
    round_result: RoundResult,
    *,
    round_num: int,
    ledger: CycleEventLog | None = None,
) -> CritiqueReadout:
    """Build the critique from pipeline stats + LLM analysis. The output is materialized to a dict so persistence does not
    drag Pydantic into the domain serialization path."""
    bundle = build_bundle(cycle, latest_round=round_result)
    template, prompt_vars, rendered, coverage = DispatchHub.fill(
        load_optimizer_prompt("l1_critique"), bundle, node="l1_critique"
    )

    result, _prompt, _repairs = await run_optimizer_node(
        template_name="l1_critique",
        prompt_vars=prompt_vars,
        template=template,
        context=LLMCallContext(
            ledger=ledger,
            round_num=round_num,
            cache=cycle.session.store.optimizer_reuse,
            injection_chars=injection_char_counts(rendered, prompt_vars),
            injection_dropped=injection_coverage_counts(coverage),
            injection_silent=tuple(injection_silent_panels(coverage)),
        ),
    )
    assert isinstance(result, L1CritiqueOutput), (
        f"l1_critique must return L1CritiqueOutput, got {type(result).__name__}"
    )
    return cast(CritiqueReadout, result.model_dump())
