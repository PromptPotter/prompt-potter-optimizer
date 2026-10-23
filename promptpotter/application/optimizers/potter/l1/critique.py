from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any, cast

from promptpotter.application.bench.llm_call import run_optimizer_node
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
from promptpotter.infrastructure.llm.telemetry import emit_round_warning

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


async def distil_critique(ctx: NodeContext[Any]) -> Critique:
    """Of the round *ctx* stands on, whether the round adapts or a later walk re-runs it; the readout a dict so persistence does not drag Pydantic into the domain serialization path."""
    bundle = build_bundle(ctx, ctx.slice(Ladder))
    filled = DispatchHub.fill(load_optimizer_prompt("l1_critique"), bundle, node="l1_critique")
    round_num = ctx.round_num

    result, _prompt, _repairs = await run_optimizer_node(
        template_name="l1_critique",
        prompt_vars=filled.injection_vars,
        template=filled.template,
        response_model=L1CritiqueOutput,
        context=ctx.call_context(round_num=round_num, injections=filled.breakdown),
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
    return Critique(readout=critique)
