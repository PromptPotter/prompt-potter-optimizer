"""What the example's proposing node sends."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from promptpotter.application.optimizers.paper_templates import task_description

if TYPE_CHECKING:
    from promptpotter.application.bench.node_context import NodeContext

__all__ = ["rephrase_prompt"]


def rephrase_prompt(ctx: NodeContext[Any], *, instruction: str) -> str:
    return ctx.fill(task_description=task_description(ctx), instruction=instruction)
