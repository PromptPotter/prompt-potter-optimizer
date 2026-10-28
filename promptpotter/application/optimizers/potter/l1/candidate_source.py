from __future__ import annotations

import logging
from collections import Counter
from dataclasses import replace
from typing import TYPE_CHECKING, Any

from promptpotter.application.bench.node_context import (
    Calls,
    Frontier,
    Lineage,
    Replay,
    Ruler,
    Target,
)
from promptpotter.application.optimizers.nodes import Proposals
from promptpotter.application.optimizers.potter.knobs import L1GenerateKnobs
from promptpotter.application.optimizers.potter.l1.generate import L1Generation, l1_generate
from promptpotter.application.optimizers.potter.l1.population import parse_population
from promptpotter.application.optimizers.potter.records import L1Readout, L2Memory
from promptpotter.application.optimizers.potter.validators.l1_invariants import detect_invariants
from promptpotter.domain.arms import candidate_label
from promptpotter.domain.optimizer_state import PARSE_FAILURE_ALL_NO_OP
from promptpotter.domain.search_point import SHOTS_KEY
from promptpotter.domain.wounds import fatal_validation_failures
from promptpotter.infrastructure.llm.telemetry import emit_round_warning

if TYPE_CHECKING:
    from promptpotter.application.bench.node_context import NodeContext
    from promptpotter.domain.arms import Proposal

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
        l1_citable=None if generation.citable is None else list(generation.citable),
        l1_exploration_budget=generation.exploration_budget,
        axis_memory_peaked=sorted(axes.peaked_axes()) if axes else [],
    )


async def propose_l1_population(ctx: NodeContext) -> Proposals:
    parent_point = ctx.read(Frontier).parent_point
    lineage, target = ctx.read(Lineage), ctx.read(Target)
    generation = await _generate_or_load(ctx)
    checked = detect_invariants(
        generation.proposals, lineage.parent, parent_point.pipeline_params, lineage.rounds
    )
    # Every variant a no-op is missing data, not N proposer wounds the stall would count.
    if checked and all(
        any(vf.reason == "no_op_variant" for vf in cp.validation_failures) for cp in checked
    ):
        model = ctx.read(Calls).model
        logger.error("L1 R%d: every variant is a no-op — zero candidates", ctx.round_num)
        emit_round_warning(
            kind="l1_zero_candidates",
            severity="error",
            message=(
                f"Optimizer produced 0 candidates this round — each of its {len(checked)} "
                f"variant(s) leaves its parent unchanged (model {model})."
            ),
            detail={"reason": PARSE_FAILURE_ALL_NO_OP, "model": model},
        )
        generation = replace(generation, proposals=[], parse_failure=PARSE_FAILURE_ALL_NO_OP)
        ctx.emit(l1_readout(ctx, generation))
        return Proposals(proposals=[])
    parsed = parse_population(
        checked,
        lineage.parent,
        parent_point.pipeline_params,
        target.schema,
        runtime_failures=target.runtime_failures(),
        inner_cells=target.inner_cells,
    )
    generation = replace(generation, proposals=parsed)
    ctx.emit(l1_readout(ctx, generation))
    return Proposals(proposals=generation.proposals)


async def _generate_or_load(ctx: NodeContext) -> L1Generation:
    round_num = ctx.round_num
    calls = ctx.read(Calls)
    # A round that replays its candidates makes no LLM call.
    persisted = ctx.read(Replay).banked_proposals()
    if persisted is not None:
        logger.debug("Loaded %d persisted candidates for round %d", len(persisted), round_num)
        calls.note_synthesized_call(
            {
                "type": "l1_generate",
                "input": {"source": "loaded_from_disk", "round": round_num},
                "response": {"candidates": _summaries(persisted, round_num)},
            }
        )
        return L1Generation(persisted)

    logger.debug("No persisted candidates for round %d — generating fresh", round_num)
    l2 = ctx.slice(L2Memory)
    return await l1_generate(
        ctx,
        n_variants=variants_this_round(ctx.knobs(L1GenerateKnobs).n_variants, l2),
        temperature=float({**calls.node_config, **l2.steered("l1_generate")}["temperature"]),
    )
