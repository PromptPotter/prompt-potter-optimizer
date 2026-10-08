"""What LEVI's llm nodes send (arXiv 2605.09764 App. E.6, E.7): the paper's own templates filled
from the manifest's ``resolved_prompts``. The sections the paper leaves unformatted are laid out
here."""

from __future__ import annotations

import random
from collections.abc import Mapping, Sequence
from typing import TYPE_CHECKING, Any

from promptpotter.application.optimizers.fence import fence_untrusted
from promptpotter.application.optimizers.paper_templates import fill, task_description
from promptpotter.domain.scoring import is_graded

if TYPE_CHECKING:
    from promptpotter.application.bench.cycle import Cycle
    from promptpotter.domain.opt_search_point import OptSearchPoint

__all__ = ["paradigm_shift_prompt", "refine_prompt"]


def _prompt(individual: OptSearchPoint) -> str:
    return f"--- PROMPT START ---\n{individual.render()}\n--- PROMPT END ---"


def _score(score: float) -> str:
    return f"{score:.4f}"


def paradigm_shift_prompt(
    cycle: Cycle, node: str, shown: Sequence[tuple[OptSearchPoint, float | None]]
) -> str:
    """E.7 over ``shown``; a seed of the calibration round is shown unscored, none being measured."""
    blocks = [
        f"### Representative {n}"
        + ("" if score is None else f" (score: {_score(score)})")
        + f"\n{_prompt(individual)}"
        for n, (individual, score) in enumerate(shown, start=1)
    ]
    return fill(
        cycle,
        node,
        problem_description=task_description(cycle),
        representatives="\n\n".join(blocks),
    )


def _failures(rows: Sequence[Mapping[str, Any]], n: int, rng: random.Random) -> str:
    failed = [r for r in rows if is_graded(r) and float(r["fitness"]) < 1.0]
    picked = rng.sample(failed, min(n, len(failed)))
    if not picked:
        return ""
    traces = "\n\n".join(
        f"### Failure {i}\nInput: {r['query']}\nExpected: {r['ground_truth']}\n"
        f"Got: {r['predicted']}"
        for i, r in enumerate(picked, start=1)
    )
    return f"## Failures\n{fence_untrusted(traces)}\n\n"


def _inspirations(inspirations: Sequence[tuple[OptSearchPoint, float]]) -> str:
    return "".join(
        f"## Inspiration\nScore: {_score(score)}\n{_prompt(individual)}\n\n"
        for individual, score in inspirations
    )


def refine_prompt(
    cycle: Cycle,
    node: str,
    *,
    parent: tuple[OptSearchPoint, float],
    parent_rows: Sequence[Mapping[str, Any]],
    inspirations: Sequence[tuple[OptSearchPoint, float]],
    n_failures: int,
    rng: random.Random,
) -> str:
    """E.6 on one parent. The meta-advice section stays empty: the paper publishes no template for
    the call that writes it (App. B)."""
    individual, score = parent
    return fill(
        cycle,
        node,
        problem_description=task_description(cycle),
        parent_score=_score(score),
        parent_prompt=individual.render(),
        feedback_section=_failures(parent_rows, n_failures, rng),
        inspirations_section=_inspirations(inspirations),
        meta_advice_section="",
    )
