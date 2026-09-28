"""What CAPO's llm nodes send beyond the shared paper templates (arXiv 2504.16005 App. D): the
initial population, drawn from the instructions ``capo_init`` answers."""

from __future__ import annotations

import ast
from typing import TYPE_CHECKING

from promptpotter.application.optimizers.paper_templates import (
    ask,
    fill,
    task_description,
    walk_rng,
)
from promptpotter.domain.opt_search_point import OptSearchPoint, node_source
from promptpotter.domain.optimizer_state import CAPO_MANIFEST

if TYPE_CHECKING:
    from promptpotter.application.optimizers.nodes import RoundContext

__all__ = ["INIT_NODE", "initial_population"]

INIT_NODE = "capo_init"
"""The llm node generating the initial instructions. It runs once, when the first crossover finds
the population empty, so the manifest declares it in a one-step pipeline of its own."""


async def initial_population(ctx: RoundContext, *, size: int, k_max: int) -> list[OptSearchPoint]:
    """App. D.2's instructions, ``size`` of them drawn at random, each given 0..k_max demo-pool
    shots at random (Alg. 1 lines 3-8). Every one derives from the origin, keeping its other fields."""
    cycle = ctx.cycle
    origin = cycle.origin_round.opt_sp
    assert origin is not None, "round 0 closes with the origin's individual"
    raw = await ask(
        ctx, INIT_NODE, None, fill(cycle, INIT_NODE, task_description=task_description(cycle))
    )
    start, end = raw.find("["), raw.rfind("]")
    listed = ast.literal_eval(raw[start : end + 1]) if 0 <= start < end else None
    if not isinstance(listed, list) or not all(isinstance(s, str) for s in listed):
        raise ValueError(f"{INIT_NODE} answered no array of instructions: {raw[:300]!r}")
    instructions = [s.strip() for s in listed if s.strip()]
    pool = [s.id for s in cycle.session.scoring.require_partition().demo]
    rng = walk_rng(cycle, ctx.round_num, INIT_NODE)
    drawn = rng.sample(instructions, min(size, len(instructions)))
    return [
        OptSearchPoint.derive(
            [origin],
            source=node_source(CAPO_MANIFEST, INIT_NODE),
            changes_description=f"initial instruction {n + 1}",
            instruction=text,
            shot_ids=rng.sample(pool, min(rng.randint(0, k_max), len(pool))),
        )
        for n, text in enumerate(drawn)
    ]
