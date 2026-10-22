from __future__ import annotations

import logging
from collections import Counter
from dataclasses import replace
from typing import TYPE_CHECKING, Any

from promptpotter.application.optimizers.nodes import Proposals
from promptpotter.application.optimizers.potter.l1.generate import L1Generation, l1_generate
from promptpotter.application.optimizers.potter.l1.population import parse_population
from promptpotter.application.optimizers.potter.records import L1Readout, L2Memory
from promptpotter.application.optimizers.potter.validators.l1_invariants import detect_invariants
from promptpotter.domain.results import candidate_label
from promptpotter.domain.search_point import SHOTS_KEY
from promptpotter.domain.wounds import fatal_validation_failures

if TYPE_CHECKING:
    from promptpotter.application.bench.node_context import NodeContext
    from promptpotter.application.optimizers.potter.knobs import L1GenerateKnobs
    from promptpotter.domain.results import Proposal

logger = logging.getLogger(__name__)


def _summaries(proposals: list[Proposal], round_num: int) -> list[dict[str, Any]]:
    """``prompt_fields`` nests as the wire's ``{node: {key: value}}``, the node's shots under ``shots``."""
    return [
        {
            "idx": i,
            "label": candidate_label(round_num, i),
            "changes_description": cp.individual.lineage.changes_description,
            "pipeline_overlay": cp.overlay,
            "prompt_fields": {
                node: {**p.fields, **({SHOTS_KEY: list(p.shots)} if p.shots else {})}
                for node, p in cp.individual.prompts.items()
            },
        }
        for i, cp in enumerate(proposals)
    ]


def variants_this_round(n_variants: int, l2: L2Memory) -> int:
    """L2's `n_variants` steer, capped at 3× the knob so L2 cannot blow up the round budget."""
    steered = l2.steered("l1_generate")
    return min(int(steered.get("n_variants", n_variants)), n_variants * 3)


def l1_readout(ctx: NodeContext[L1GenerateKnobs], generation: L1Generation) -> L1Readout:
    """Called once every reject posture has read *generation*: the yield is counted here alone."""
    axes = ctx.axes
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
        l1_citable=None if generation.citable is None else list(generation.citable),
        l1_exploration_budget=generation.exploration_budget,
        axis_memory_peaked=sorted(axes.peaked_axes()) if axes else [],
    )


async def propose_l1_population(ctx: NodeContext[L1GenerateKnobs]) -> Proposals:
    parent_point = ctx.parent_point
    assert parent_point is not None
    generation = await _generate_or_load(ctx)
    parsed = parse_population(
        generation.proposals,
        ctx.parent,
        parent_point.pipeline_params,
        ctx.schema,
        runtime_failures=ctx.runtime_failures(),
        inner_cells=ctx.inner_cells,
    )
    generation = replace(generation, proposals=parsed)
    ctx.emit(l1_readout(ctx, generation))
    return Proposals(proposals=generation.proposals)


async def _generate_or_load(ctx: NodeContext[L1GenerateKnobs]) -> L1Generation:
    round_num = ctx.round_num
    l2 = ctx.slice(L2Memory)
    _n_variants = variants_this_round(ctx.knobs.n_variants, l2)
    _temperature = float({**ctx.node_config, **l2.steered("l1_generate")}["temperature"])

    assert ctx.parent_point is not None
    # Bound once: the narrowing does not survive the await.
    parent_pipeline_params = ctx.parent_point.pipeline_params

    # A round that replays its candidates makes no LLM call.
    persisted = ctx.banked_proposals()
    if persisted is not None:
        logger.debug("Loaded %d persisted candidates for round %d", len(persisted), round_num)
        persisted = detect_invariants(persisted, ctx.parent, parent_pipeline_params, ctx.rounds)
        ctx.note_synthesized_call(
            {
                "type": "l1_generate",
                "input": {"source": "loaded_from_disk", "round": round_num},
                "response": {"candidates": _summaries(persisted, round_num)},
            }
        )
        return L1Generation(persisted)

    logger.debug("No persisted candidates for round %d — generating fresh", round_num)
    generation = await l1_generate(ctx, n_variants=_n_variants, temperature=_temperature)
    checked = detect_invariants(
        generation.proposals, ctx.parent, parent_pipeline_params, ctx.rounds
    )
    return replace(generation, proposals=checked)


__all__ = ["l1_readout", "propose_l1_population", "variants_this_round"]
