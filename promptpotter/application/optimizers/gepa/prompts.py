"""What GEPA's reflection sends and reads back (arXiv 2507.19457 App. C)."""

from __future__ import annotations

import re
from collections.abc import Iterable
from typing import TYPE_CHECKING, Any

from promptpotter.application.optimizers.fence import fence_untrusted
from promptpotter.application.scoring.row_diagnostics import cell_feedback

if TYPE_CHECKING:
    from promptpotter.application.bench.node_context import NodeContext
    from promptpotter.domain.scoring import GradedCell

__all__ = ["fenced", "reflection_prompt"]

_FENCE = "```"
_LANGUAGE_TAG = re.compile(r"^\S*\n")


def _example(n: int, cell: GradedCell) -> str:
    # The reference implementation's layout: the paper shows only a placeholder.
    facts = cell.facts
    trace = facts.pipeline.reasoning_trace
    outputs = (
        f"### reasoning\n{trace.strip()}\n\n### answer\n{facts.predicted.strip()}"
        if trace
        else facts.predicted.strip()
    )
    return (
        f"# Example {n}\n## Inputs\n{facts.query.strip()}\n\n"
        f"## Generated Outputs\n{outputs}\n\n## Feedback\n{cell_feedback(cell)}"
    )


def reflection_prompt(
    ctx: NodeContext[Any], *, instruction: str, rows: Iterable[GradedCell]
) -> str:
    examples = "\n\n".join(_example(n, row) for n, row in enumerate(rows, start=1))
    return ctx.fill(current_instruction=instruction, examples=fence_untrusted(examples))


def fenced(text: str) -> str | None:
    start, end = text.find(_FENCE), text.rfind(_FENCE)
    if start < 0 or end <= start:
        return text.strip() or None
    body = _LANGUAGE_TAG.sub("", text[start + len(_FENCE) : end], count=1).strip()
    return body or None
