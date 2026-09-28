"""What GEPA's reflection sends and reads back (arXiv 2507.19457 App. C) — the code
``GepaRuntime.source_digest`` hashes beside ``paper_templates``."""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from typing import TYPE_CHECKING, Any

from promptpotter.application.optimization.dispatch.bundle import fence_untrusted
from promptpotter.application.optimizers.paper_templates import fill
from promptpotter.application.scoring.row_diagnostics import cell_feedback

if TYPE_CHECKING:
    from promptpotter.application.optimization.cycle import Cycle

__all__ = ["fenced", "reflection_prompt"]

_FENCE = "```"
# A language tag on the opening fence, as the reference implementation drops it.
_LANGUAGE_TAG = re.compile(r"^\S*\n")


def _example(n: int, row: Mapping[str, Any]) -> str:
    # The reference implementation's layout: the paper shows only a placeholder.
    trace = (row["pipeline_data"] or {}).get("reasoning_trace")
    outputs = (
        f"### reasoning\n{str(trace).strip()}\n\n### answer\n{str(row['predicted']).strip()}"
        if trace
        else str(row["predicted"]).strip()
    )
    return (
        f"# Example {n}\n## Inputs\n{str(row['query']).strip()}\n\n"
        f"## Generated Outputs\n{outputs}\n\n## Feedback\n{cell_feedback(row)}"
    )


def reflection_prompt(
    cycle: Cycle, node: str, *, instruction: str, rows: Sequence[Mapping[str, Any]]
) -> str:
    """The template over the parent's instruction and its minibatch rows, each one's inputs,
    outputs and feedback; the rows are dataset content, so they ride the untrusted fence."""
    examples = "\n\n".join(_example(n, row) for n, row in enumerate(rows, start=1))
    return fill(cycle, node, current_instruction=instruction, examples=fence_untrusted(examples))


def fenced(text: str) -> str | None:
    """The text between the reply's first and last fence; ``None`` where it fences nothing."""
    start, end = text.find(_FENCE), text.rfind(_FENCE)
    if start < 0 or end <= start:
        return None
    body = _LANGUAGE_TAG.sub("", text[start + len(_FENCE) : end], count=1).strip()
    return body or None
