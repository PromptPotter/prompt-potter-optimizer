"""The paper's own templates (arXiv 2605.09764 App. E.6, E.7), filled from ``resolved_prompts``."""

from __future__ import annotations

import random
from collections.abc import Sequence
from typing import TYPE_CHECKING

from promptpotter.application.bench.node_context import Calls, Target
from promptpotter.application.optimizers.fence import fence_untrusted
from promptpotter.application.optimizers.paper_templates import shown, task_description
from promptpotter.domain.scoring import ROW_GRADES

if TYPE_CHECKING:
    from promptpotter.application.bench.node_context import NodeContext
    from promptpotter.domain.opt_search_point import OptSearchPoint
    from promptpotter.domain.scoring import GradedCell


def _prompt(ctx: NodeContext, individual: OptSearchPoint) -> str:
    node = ctx.read(Target).prompt_node
    return f"--- PROMPT START ---\n{shown(ctx, individual, node)}\n--- PROMPT END ---"


def _score(score: float) -> str:
    return f"{score:.4f}"


def paradigm_shift_prompt(
    ctx: NodeContext, shown: Sequence[tuple[OptSearchPoint, float | None]]
) -> str:
    """E.7; a calibration-round seed is shown unscored, none being measured."""
    blocks = [
        f"### Representative {n}"
        + ("" if score is None else f" (score: {_score(score)})")
        + f"\n{_prompt(ctx, individual)}"
        for n, (individual, score) in enumerate(shown, start=1)
    ]
    return ctx.read(Calls).fill(
        problem_description=task_description(ctx, ctx.read(Target).prompt_node),
        representatives="\n\n".join(blocks),
    )


def _failures(rows: Sequence[GradedCell], n: int, rng: random.Random) -> str:
    fitness = ROW_GRADES["fitness"]
    failed = [r.facts for r in rows if r.scored and float(fitness.read(r)) < 1.0]
    picked = rng.sample(failed, min(n, len(failed)))
    if not picked:
        return ""
    traces = "\n\n".join(
        f"### Failure {i}\nInput: {facts.query}\nExpected: {facts.ground_truth}\n"
        f"Got: {facts.predicted}"
        for i, facts in enumerate(picked, start=1)
    )
    return f"## Failures\n{fence_untrusted(traces)}\n\n"


def _inspirations(ctx: NodeContext, inspirations: Sequence[tuple[OptSearchPoint, float]]) -> str:
    return "".join(
        f"## Inspiration\nScore: {_score(score)}\n{_prompt(ctx, individual)}\n\n"
        for individual, score in inspirations
    )


def refine_prompt(
    ctx: NodeContext,
    *,
    parent: tuple[OptSearchPoint, float],
    parent_rows: Sequence[GradedCell],
    inspirations: Sequence[tuple[OptSearchPoint, float]],
    n_failures: int,
    rng: random.Random,
) -> str:
    """E.6; meta-advice stays empty: the paper publishes no template for it (App. B)."""
    individual, score = parent
    node = ctx.read(Target).prompt_node
    return ctx.read(Calls).fill(
        problem_description=task_description(ctx, node),
        parent_score=_score(score),
        parent_prompt=shown(ctx, individual, node),
        feedback_section=_failures(parent_rows, n_failures, rng),
        inspirations_section=_inspirations(ctx, inspirations),
        meta_advice_section="",
    )
