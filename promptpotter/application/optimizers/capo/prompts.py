from __future__ import annotations

from typing import TYPE_CHECKING

from promptpotter.application.bench.node_context import Calls, Target
from promptpotter.application.optimizers.paper_templates import shown, task_description

if TYPE_CHECKING:
    from promptpotter.application.bench.node_context import NodeContext
    from promptpotter.domain.opt_search_point import OptSearchPoint


def init_prompt(ctx: NodeContext) -> str:
    node = ctx.read(Target).prompt_node
    return ctx.read(Calls).fill(task_description=task_description(ctx, node))


def crossover_prompt(ctx: NodeContext, mother: OptSearchPoint, father: OptSearchPoint) -> str:
    node = ctx.read(Target).prompt_node
    return ctx.read(Calls).fill(
        task_description=task_description(ctx, node),
        mother=shown(ctx, mother, node),
        father=shown(ctx, father, node),
    )


def mutation_prompt(ctx: NodeContext, individual: OptSearchPoint) -> str:
    node = ctx.read(Target).prompt_node
    return ctx.read(Calls).fill(
        task_description=task_description(ctx, node), instruction=shown(ctx, individual, node)
    )
