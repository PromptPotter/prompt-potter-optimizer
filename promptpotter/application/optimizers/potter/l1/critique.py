from __future__ import annotations

from typing import TYPE_CHECKING, cast

from promptpotter.application.bench.node_context import Calls
from promptpotter.application.optimizers.potter.dispatch.facade import (
    DispatchHub,
    build_bundle,
)
from promptpotter.application.optimizers.potter.dispatch.injections.panels import (
    clustered_sample_ids,
    detectable_rows,
)
from promptpotter.application.optimizers.potter.dispatch.schemas import (
    CLUSTER_ROWS_MIN,
    critique_response_model,
)
from promptpotter.application.optimizers.potter.records import Ladder
from promptpotter.domain.optimizer_state import Critique, CritiqueReadout

if TYPE_CHECKING:
    from promptpotter.application.bench.node_context import NodeContext


async def distil_critique(ctx: NodeContext) -> Critique:
    """Of the round *ctx* stands on, whether the round adapts or a later walk re-runs it; the readout a dict so persistence does not drag Pydantic into the domain serialization path."""
    bundle = build_bundle(ctx, ctx.slice(Ladder))
    calls = ctx.read(Calls)
    filled = DispatchHub.fill(calls.prompt(), bundle, node=ctx.node)
    # A cluster an edit could fix whole and still not separate from its parent is noise to target.
    least = max(CLUSTER_ROWS_MIN, detectable_rows(bundle) or 0)
    result = await calls.answer(
        critique_response_model(frozenset(clustered_sample_ids(bundle)), least), filled
    )
    return Critique(readout=cast(CritiqueReadout, result.model_dump()))
