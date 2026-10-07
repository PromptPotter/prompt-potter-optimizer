"""What the example's proposing node sends."""

from __future__ import annotations

from typing import TYPE_CHECKING

from promptpotter.application.optimizers.paper_templates import fill, task_description

if TYPE_CHECKING:
    from promptpotter.application.bench.cycle import Cycle

__all__ = ["rephrase_prompt"]


def rephrase_prompt(cycle: Cycle, node: str, *, instruction: str) -> str:
    return fill(cycle, node, task_description=task_description(cycle), instruction=instruction)
