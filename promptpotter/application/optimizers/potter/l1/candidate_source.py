from __future__ import annotations

import logging
from collections import Counter
from dataclasses import replace
from typing import TYPE_CHECKING

from promptpotter.application.bench.node_context import (
    Frontier,
    Lineage,
    Ruler,
    Target,
)
from promptpotter.application.optimizers.nodes import Proposals
from promptpotter.application.optimizers.potter.dispatch.bundle import l1_target_schema
from promptpotter.application.optimizers.potter.knobs import L1GenerateKnobs
from promptpotter.application.optimizers.potter.l1.generate import L1Generation, l1_generate
from promptpotter.application.optimizers.potter.l1.population import parse_population
from promptpotter.application.optimizers.potter.records import L1Readout, L2Memory
from promptpotter.application.optimizers.potter.validators.l1_invariants import detect_invariants
from promptpotter.domain.arms import Proposal
from promptpotter.domain.optimizer_state import PARSE_FAILURE_ALL_NO_OP
from promptpotter.domain.paired_reading import ArmKey
from promptpotter.domain.wounds import fatal_validation_failures

if TYPE_CHECKING:
    from promptpotter.application.bench.node_context import NodeContext

logger = logging.getLogger(__name__)


def variants_this_round(n_variants: int, l2: L2Memory) -> int:
    """L2's `n_variants` steer, capped at 3× the knob so L2 cannot blow up the round budget."""
    steered = l2.steered("l1_generate")
    return min(int(steered.get("n_variants", n_variants)), n_variants * 3)


def rejoining(lineage: Lineage) -> list[Proposal]:
    """The population beside the parent — the leader a held round kept — as it was walked, ahead
    of L1's own, so each of those races it too (`election.py::elect_on_theta`)."""
    walked = lineage.rounds[-1].arms
    return [
        Proposal(individual=member, overlay=walked[ArmKey(member.id)].overlay)
        for member in lineage.population
        if member.id != lineage.parent.id
    ]


def l1_readout(ctx: NodeContext, generation: L1Generation) -> L1Readout:
    """Called once every reject posture has read *generation*: the yield is counted here alone."""
    axes = ctx.read(Ruler).axes
    rejected = Counter(
        fatal[0].reason
        for cp in generation.proposals
        if (fatal := fatal_validation_failures(cp.validation_failures))
    )
    proposed = len(generation.proposals)
    return L1Readout(
        l1_yield=(proposed - rejected.total()) / proposed if proposed else None,
        l1_proposed=proposed,
        l1_rejected=dict(rejected),
        l1_parse_failure=generation.parse_failure,
        l1_citable=list(generation.citable),
        l1_exploration_budget=generation.exploration_budget,
        axis_memory_peaked=sorted(axes.peaked_axes()) if axes else [],
    )


async def propose_l1_population(ctx: NodeContext) -> Proposals:
    parent_point = ctx.read(Frontier).parent_point
    lineage, target = ctx.read(Lineage), ctx.read(Target)
    # Outside L1's generation: no invariant reads them and its yield never counts them.
    kept = rejoining(lineage)
    generation = await _generate(ctx)
    checked = detect_invariants(
        generation.proposals, lineage.parent, parent_point.pipeline_params, lineage.rounds
    )
    # A batch moving none of the parent is missing data, not one proposer wound per variant.
    if checked and all(
        any(vf.reason == "no_op_variant" for vf in cp.validation_failures) for cp in checked
    ):
        logger.error("L1 R%d: every variant is a no-op — zero candidates", ctx.round_num)
        checked = []
        generation = replace(generation, parse_failure=PARSE_FAILURE_ALL_NO_OP)
    parsed = parse_population(
        checked,
        lineage.parent,
        parent_point.pipeline_params,
        l1_target_schema(target.schema),
        runtime_failures=target.runtime_failures(),
        inner_cells=target.inner_cells,
    )
    generation = replace(generation, proposals=parsed)
    ctx.emit(l1_readout(ctx, generation))
    return Proposals(proposals=[*kept, *generation.proposals])


async def _generate(ctx: NodeContext) -> L1Generation:
    l2 = ctx.slice(L2Memory)
    # Unsteered, the call runs at the node's own temperature; `NodeConfig` types a steered one.
    return await l1_generate(
        ctx,
        n_variants=variants_this_round(ctx.knobs(L1GenerateKnobs).n_variants, l2),
        temperature=l2.steered("l1_generate").get("temperature"),
    )
