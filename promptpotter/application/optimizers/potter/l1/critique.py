from __future__ import annotations

import logging
from typing import TYPE_CHECKING, cast

from promptpotter.application.bench.llm_call import run_optimizer_node
from promptpotter.application.bench.node_context import Calls
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
from promptpotter.application.optimizers.potter.records import Ladder
from promptpotter.domain.optimizer_state import Critique, CritiqueReadout, FailureClusterReadout

if TYPE_CHECKING:
    from collections.abc import Iterable

    from promptpotter.application.bench.node_context import NodeContext

logger = logging.getLogger(__name__)


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


async def distil_critique(ctx: NodeContext) -> Critique:
    """Of the round *ctx* stands on, whether the round adapts or a later walk re-runs it; the readout a dict so persistence does not drag Pydantic into the domain serialization path."""
    bundle = build_bundle(ctx, ctx.slice(Ladder))
    filled = DispatchHub.fill(load_optimizer_prompt("l1_critique"), bundle, node="l1_critique")

    result, _prompt, _repairs = await run_optimizer_node(
        template_name="l1_critique",
        prompt_vars=filled.injection_vars,
        template=filled.template,
        response_model=L1CritiqueOutput,
        context=ctx.read(Calls).call_context(filled.breakdown),
    )
    assert isinstance(result, L1CritiqueOutput), (
        f"l1_critique must return L1CritiqueOutput, got {type(result).__name__}"
    )
    critique = cast(CritiqueReadout, result.model_dump())
    kept, dropped = spanning_clusters(critique["failure_highlights"], clustered_sample_ids(bundle))
    if dropped:
        logger.info(
            "round %d critique: %d of %d clusters span fewer than %d marked rows — dropped",
            ctx.round_num,
            len(dropped),
            len(kept) + len(dropped),
            CLUSTER_ROWS_MIN,
        )
    critique["failure_highlights"] = kept
    return Critique(readout=critique)
