"""What CAPO's llm nodes send (arXiv 2504.16005 App. D): the paper's own templates filled from the
manifest's ``resolved_prompts``."""

from __future__ import annotations

from typing import TYPE_CHECKING

from promptpotter.application.optimizers.paper_templates import fill, task_description

if TYPE_CHECKING:
    from promptpotter.application.bench.cycle import Cycle
    from promptpotter.domain.opt_search_point import OptSearchPoint

__all__ = ["crossover_prompt", "init_prompt", "mutation_prompt"]


def init_prompt(cycle: Cycle, node: str) -> str:
    return fill(cycle, node, task_description=task_description(cycle))


def crossover_prompt(
    cycle: Cycle, node: str, mother: OptSearchPoint, father: OptSearchPoint
) -> str:
    return fill(
        cycle,
        node,
        task_description=task_description(cycle),
        mother=mother.instruction,
        father=father.instruction,
    )


def mutation_prompt(cycle: Cycle, node: str, individual: OptSearchPoint) -> str:
    return fill(
        cycle, node, task_description=task_description(cycle), instruction=individual.instruction
    )
