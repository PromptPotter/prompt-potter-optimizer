from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from promptpotter.application.optimizer_manifest import bound_inner_optimizer
from promptpotter.application.optimizers.nodes import Proposals
from promptpotter.application.optimizers.potter.l1.critique import ensure_prior_critique
from promptpotter.application.optimizers.potter.l1.generate import L1Generation, l1_generate
from promptpotter.application.optimizers.potter.l1.population import parse_population
from promptpotter.application.optimizers.potter.validators.l1_invariants import detect_invariants
from promptpotter.application.runner.round import proposal_summaries
from promptpotter.domain.run_records import LLMCallRecord

if TYPE_CHECKING:
    from promptpotter.application.bench.node_context import NodeContext
    from promptpotter.application.optimizers.potter.knobs import L1GenerateKnobs
    from promptpotter.application.optimizers.potter.state import PotterState

logger = logging.getLogger(__name__)


def variants_this_round(n_variants: int, state: PotterState) -> int:
    """L2's `n_variants` steer, capped at 3× the knob so L2 cannot blow up the round budget."""
    steered = state.memory.steered("l1_generate")
    return min(int(steered.get("n_variants", n_variants)), n_variants * 3)


async def propose_l1_population(ctx: NodeContext[L1GenerateKnobs], state: PotterState) -> Proposals:
    schema = state.session.pipeline_schema
    parent_point = ctx.parent_point
    assert schema is not None and parent_point is not None
    generation = await _generate_or_load(ctx, state)
    knobs = ctx.knobs
    parse_population(
        generation.proposals,
        ctx.parent,
        parent_point.pipeline_params,
        schema,
        runtime_failures=state.memory.wounds.runtime_failures,
        demo_ids=frozenset(s.id for s in ctx.demo_pool),
        shot_k_max=knobs.k_max,
        inner_optimizer=bound_inner_optimizer(),
        prompt_block_catalogue=knobs.prompt_block_catalogue,
    )
    state.in_flight = state.snapshot(ctx.sample_index, generation)
    return Proposals(proposals=generation.proposals)


async def _generate_or_load(ctx: NodeContext[L1GenerateKnobs], state: PotterState) -> L1Generation:
    session = state.session
    round_num = ctx.round_num
    _n_variants = variants_this_round(ctx.knobs.n_variants, state)
    _temperature = float(
        {**ctx.optimizer.node_config("l1_generate"), **state.memory.steered("l1_generate")}[
            "temperature"
        ]
    )

    assert ctx.parent_point is not None
    # Bound once: the narrowing does not survive the await.
    parent_pipeline_params = ctx.parent_point.pipeline_params

    # A round that replays its candidates makes no LLM call, so a missing critique is not re-sent.
    persisted = ctx.banked_proposals()
    if persisted is None:
        await ensure_prior_critique(ctx, state)

    if persisted is not None:
        logger.debug("Loaded %d persisted candidates for round %d", len(persisted), round_num)
        detect_invariants(persisted, ctx.parent, parent_pipeline_params, ctx.rounds)
        # Synthesized: llm_call never fires here, yet the audit trail and dashboard must see the node.
        if (_ledger := session.state.ledger) is not None:
            _ledger.append(
                LLMCallRecord(
                    node="l1_generate",
                    round=round_num,
                    payload_kind="synthesized",
                    payload={
                        "type": "l1_generate",
                        "input": {"source": "loaded_from_disk", "round": round_num},
                        "response": {"candidates": proposal_summaries(persisted, round_num)},
                    },
                )
            )
        return L1Generation(persisted)

    logger.debug("No persisted candidates for round %d — generating fresh", round_num)

    generation = await l1_generate(ctx, state, n_variants=_n_variants, temperature=_temperature)
    candidates = generation.proposals
    detect_invariants(candidates, ctx.parent, parent_pipeline_params, ctx.rounds)

    # A banked EMPTY population replays as zero candidates on every resume: a permanent parse failure.
    if candidates:
        ctx.bank_proposals(candidates)
    return generation


__all__ = ["propose_l1_population", "variants_this_round"]
