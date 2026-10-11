from __future__ import annotations

import re
from collections.abc import Callable, Sequence
from typing import TYPE_CHECKING, Annotated, Any, Literal

from pydantic import Field

from promptpotter.application.campaign_config import Estimand, Knob, Scope
from promptpotter.domain.optimizer_state import PARSE_FAILURE_MALFORMED, PARSE_FAILURE_TOOLING
from promptpotter.domain.search_point import PROMPT_STRING_FIELDS
from promptpotter.domain.strict_model import StrictModel
from promptpotter.domain.wounds import ValidationFailure

if TYPE_CHECKING:
    from promptpotter.application.bench.node_context import NodeContext
    from promptpotter.domain.opt_search_point import OptSearchPoint
    from promptpotter.domain.results import CandidateProposal

__all__ = [
    "RewriteKnobs",
    "Rewrites",
    "child",
    "marked",
    "reply_fields",
    "rewrite",
    "rewritten",
    "shown",
    "task_description",
    "unmarked",
]

_PROMPT = re.compile(r"<prompt>(.*?)</prompt>", re.DOTALL)


def task_description(ctx: NodeContext[Any]) -> str:
    """Ends on the origin's answer format: the extraction contract a paper's task descriptions state (CAPO App. D.1)."""
    origin = ctx.origin
    parts = [v for v in ctx.framing.to_dict().values() if v]
    if origin.answer_format:
        parts.append(origin.answer_format)
    if not parts:
        raise ValueError(
            f"{ctx.optimizer.name} fills its templates with the task description, and this "
            "campaign declares no framing (task_context) and its origin no answer_format"
        )
    return "\n".join(parts)


Rewrites = Literal["prompt", "instruction"]


class RewriteKnobs(StrictModel):
    rewrites: Annotated[Rewrites, Knob(Scope.POLICY, Estimand.SEARCH)] = Field(
        description="Which loci the node's reply replaces. `prompt`: the whole prompt as one "
        "text — the model is shown every field rendered together, its reply rides `instruction` "
        "and the other five empty, as the papers' one-text prompts do. `instruction`: that field "
        "alone — the model is shown it alone, and the child inherits every other locus from its "
        "first parent."
    )


def shown(ctx: NodeContext[Any], individual: OptSearchPoint) -> str:
    return individual.render() if ctx.knobs.rewrites == "prompt" else individual.instruction


def rewritten(text: str, rewrites: Rewrites) -> dict[str, Any]:
    """Under ``prompt`` every other field is emptied: the reply replaced the whole ``render()`` shown."""
    if rewrites == "instruction":
        return {"instruction": text}
    return {**dict.fromkeys(PROMPT_STRING_FIELDS, ""), "instruction": text}


def marked(text: str) -> str | None:
    """The last ``<prompt>`` block: an answer comes after any echo of the template's example."""
    found = [m.strip() for m in _PROMPT.findall(text) if m.strip()]
    return found[-1] if found else None


def unmarked(node: str, raw: str) -> ValidationFailure:
    """An empty reply is the provider's failure, not the prompt's: missing data, never a verdict."""
    reason = PARSE_FAILURE_MALFORMED if raw.strip() else PARSE_FAILURE_TOOLING
    return ValidationFailure(axis=f"{node}.output", value=raw[:300], allowed=[], reason=reason)


def reply_fields(
    node: str, raw: str, rewrites: Rewrites, parse: Callable[[str], str | None] = marked
) -> tuple[dict[str, Any], list[ValidationFailure]]:
    text = parse(raw)
    return ({}, [unmarked(node, raw)]) if text is None else (rewritten(text, rewrites), [])


def child(
    ctx: NodeContext[Any],
    parents: Sequence[OptSearchPoint],
    raw: str,
    changes: str,
    *,
    parse: Callable[[str], str | None] = marked,
    **fields: Any,
) -> CandidateProposal:
    written, failures = reply_fields(ctx.node, raw, ctx.knobs.rewrites, parse)
    return ctx.child(parents, failures=failures, changes_description=changes, **written, **fields)


def rewrite(
    ctx: NodeContext[Any],
    proposal: CandidateProposal,
    raw: str,
    changes: str | None = None,
    *,
    parse: Callable[[str], str | None] = marked,
) -> CandidateProposal:
    fields, failures = reply_fields(ctx.node, raw, ctx.knobs.rewrites, parse)
    if failures:
        failures = [*proposal.validation_failures, *failures]
        return proposal.model_copy(update={"validation_failures": failures})
    return ctx.edit(proposal, changes_description=changes, **fields)
