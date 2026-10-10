from __future__ import annotations

from typing import TYPE_CHECKING, Any

from promptpotter.application.optimizers.paper_templates import shown, task_description

if TYPE_CHECKING:
    from promptpotter.application.bench.node_context import NodeContext
    from promptpotter.domain.opt_search_point import OptSearchPoint

__all__ = ["crossover_prompt", "init_prompt", "mutation_prompt"]


def init_prompt(ctx: NodeContext[Any]) -> str:
    return ctx.fill(task_description=task_description(ctx))


def crossover_prompt(ctx: NodeContext[Any], mother: OptSearchPoint, father: OptSearchPoint) -> str:
    return ctx.fill(
        task_description=task_description(ctx),
        mother=shown(ctx, mother),
        father=shown(ctx, father),
    )


def mutation_prompt(ctx: NodeContext[Any], individual: OptSearchPoint) -> str:
    return ctx.fill(task_description=task_description(ctx), instruction=shown(ctx, individual))
