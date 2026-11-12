from __future__ import annotations

from typing import TYPE_CHECKING, cast

from promptpotter.application.bench.llm_call import run_optimizer_node
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
    L1CritiqueOutput,
    critique_response_model,
)
from promptpotter.application.optimizers.potter.records import Ladder
from promptpotter.domain.optimizer_state import Critique, CritiqueReadout

if TYPE_CHECKING:
    from promptpotter.application.bench.node_context import NodeContext


async def distil_critique(ctx: NodeContext) -> Critique:
    """Of the round *ctx* stands on, whether the round adapts or a later walk re-runs it; the readout a dict so persistence does not drag Pydantic into the domain serialization path."""
    bundle = build_bundle(ctx, ctx.slice(Ladder))
    context = ctx.read(Calls).call_context()
    filled = DispatchHub.fill(context.template, bundle, node=context.node)
    # A cluster an edit could fix whole and still not separate from its parent is noise to target.
    least = max(CLUSTER_ROWS_MIN, detectable_rows(bundle) or 0)
    result, _prompt, _repairs = await run_optimizer_node(
        context=context,
        fields=filled.fields,
        prompt_vars=filled.injection_vars,
        injections=filled.breakdown,
        response_model=critique_response_model(frozenset(clustered_sample_ids(bundle)), least),
    )
    assert isinstance(result, L1CritiqueOutput), (
        f"l1_critique must return L1CritiqueOutput, got {type(result).__name__}"
    )
    return Critique(readout=cast(CritiqueReadout, result.model_dump()))
