from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING, Any, cast

from promptpotter.application.bench.llm_call import (
    LLMCallContext,
    run_optimizer_node,
)
from promptpotter.application.optimizers.potter.dispatch.facade import (
    DispatchHub,
    build_bundle,
)
from promptpotter.application.optimizers.potter.dispatch.injections.panels import (
    clustered_sample_ids,
)
from promptpotter.application.optimizers.potter.dispatch.prompts import (
    load_optimizer_prompt,
)
from promptpotter.application.optimizers.potter.dispatch.schemas import (
    CLUSTER_ROWS_MIN,
    L1CritiqueOutput,
)
from promptpotter.application.optimizers.potter.records import PotterRoundState
from promptpotter.domain.optimizer_state import CritiqueReadout, FailureClusterReadout
from promptpotter.domain.phases import StopLoop, StopReason
from promptpotter.infrastructure.llm.telemetry import emit_round_warning
from promptpotter.shared.errors import SendRefusedError

if TYPE_CHECKING:
    from collections.abc import Iterable, Sequence

    from promptpotter.application.bench.node_context import NodeContext
    from promptpotter.application.optimizers.potter.state import PotterState
    from promptpotter.domain.results import RoundResult

logger = logging.getLogger(__name__)

__all__ = [
    "critique_owed",
    "ensure_prior_critique",
    "run_l1_critique",
    "spanning_clusters",
]


CRITIQUE_RESEND_ATTEMPTS = 3
"""Each is a whole call: the client's backpressure and 5xx retries sit INSIDE one attempt and do not count against this."""


def critique_owed(rounds: Sequence[RoundResult]) -> bool:
    prior = rounds[-1] if rounds else None
    if prior is None or prior.round == 0 or not prior.results:
        return False
    return not prior.optimizer_state.payload_as(PotterRoundState).critique


async def ensure_prior_critique(ctx: NodeContext[Any], state: PotterState) -> None:
    """HALTS (``PAUSED``, resumable) where the re-send never arrives: without its critique the round spends a panel on a blind choice."""
    if not critique_owed(ctx.rounds):
        return
    prior = ctx.rounds[-1]
    critique: CritiqueReadout | None = None
    last: Exception | None = None
    for attempt in range(1, CRITIQUE_RESEND_ATTEMPTS + 1):
        try:
            critique = await run_l1_critique(ctx, state)
            break
        # A refused send is decided: swallowed, it halts as a PAUSE that `resume` re-enters forever.
        except (KeyboardInterrupt, asyncio.CancelledError, SendRefusedError):
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
    if not critique:
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
        raise StopLoop(StopReason.PAUSED)
    # On the ledger, or the next resume re-sends a call this one already paid for.
    ctx.restate(
        prior.optimizer_state.payload_as(PotterRoundState).model_copy(update={"critique": critique})
    )
    logger.info("Round %d critique distilled late; this round's generator reads it.", prior.round)


def spanning_clusters(
    clusters: Iterable[FailureClusterReadout], rows: Iterable[int]
) -> tuple[list[FailureClusterReadout], list[FailureClusterReadout]]:
    """The seam the wire cannot hold (``strict: False``): kept on the marked rows it names, ``CLUSTER_ROWS_MIN`` at least."""
    marked = set(rows)
    kept: list[FailureClusterReadout] = []
    dropped: list[FailureClusterReadout] = []
    for cluster in clusters:
        ids = sorted({i for i in cluster["sample_ids"] if i in marked})
        if len(ids) >= CLUSTER_ROWS_MIN:
            kept.append({**cluster, "sample_ids": ids})
        else:
            dropped.append(cluster)
    return kept, dropped


async def run_l1_critique(ctx: NodeContext[Any], state: PotterState) -> CritiqueReadout:
    """Materialized to a dict so persistence does not drag Pydantic into the domain serialization path."""
    session = state.session
    bundle = build_bundle(ctx, state)
    filled = DispatchHub.fill(load_optimizer_prompt("l1_critique"), bundle, node="l1_critique")
    round_num = ctx.rounds[-1].round

    result, _prompt, _repairs = await run_optimizer_node(
        template_name="l1_critique",
        prompt_vars=filled.injection_vars,
        template=filled.template,
        response_model=L1CritiqueOutput,
        context=LLMCallContext(
            ledger=session.state.ledger,
            round_num=round_num,
            cache=session.store.optimizer_reuse,
            injections=filled.breakdown,
        ),
    )
    assert isinstance(result, L1CritiqueOutput), (
        f"l1_critique must return L1CritiqueOutput, got {type(result).__name__}"
    )
    critique = cast(CritiqueReadout, result.model_dump())
    kept, dropped = spanning_clusters(critique["failure_highlights"], clustered_sample_ids(bundle))
    if dropped:
        emit_round_warning(
            kind="critique_cluster_dropped",
            message=(
                f"round {round_num} critique: {len(dropped)} of {len(kept) + len(dropped)} "
                f"clusters span fewer than {CLUSTER_ROWS_MIN} rows the panels marked — dropped; "
                f"the generator targets the {len(kept)} that do"
            ),
            detail={
                "round": round_num,
                "kept": len(kept),
                "dropped": [
                    {"mechanism": c["mechanism"], "sample_ids": c["sample_ids"]} for c in dropped
                ],
            },
        )
    critique["failure_highlights"] = kept
    return critique
