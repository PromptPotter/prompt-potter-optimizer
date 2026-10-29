"""What the example's proposing node sends."""

from __future__ import annotations

from typing import TYPE_CHECKING

from promptpotter.application.bench.node_context import Calls, Target
from promptpotter.application.optimizers.paper_templates import task_description

if TYPE_CHECKING:
    from promptpotter.application.bench.node_context import NodeContext

__all__ = ["rephrase_prompt"]


def rephrase_prompt(ctx: NodeContext, *, instruction: str) -> str:
    node = ctx.read(Target).prompt_node
    return ctx.read(Calls).fill(
        task_description=task_description(ctx, node), instruction=instruction
    )
