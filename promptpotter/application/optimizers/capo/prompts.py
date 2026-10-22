from __future__ import annotations

from typing import TYPE_CHECKING, Any

from promptpotter.application.optimizers.paper_templates import shown, task_description

if TYPE_CHECKING:
    from promptpotter.application.bench.node_context import NodeContext
    from promptpotter.domain.opt_search_point import OptSearchPoint

__all__ = ["crossover_prompt", "init_prompt", "mutation_prompt"]


def init_prompt(ctx: NodeContext[Any]) -> str:
    return ctx.fill(task_description=task_description(ctx, ctx.prompt_node))


def crossover_prompt(ctx: NodeContext[Any], mother: OptSearchPoint, father: OptSearchPoint) -> str:
    node = ctx.prompt_node
    return ctx.fill(
        task_description=task_description(ctx, node),
        mother=shown(ctx, mother, node),
        father=shown(ctx, father, node),
    )


def mutation_prompt(ctx: NodeContext[Any], individual: OptSearchPoint) -> str:
    node = ctx.prompt_node
    return ctx.fill(
        task_description=task_description(ctx, node), instruction=shown(ctx, individual, node)
    )
