from __future__ import annotations

import re
from collections.abc import Callable, Sequence
from typing import TYPE_CHECKING, Annotated, Any, Literal

from pydantic import Field

from promptpotter.application.campaign_config import Estimand, Knob, Scope
from promptpotter.domain.opt_search_point import Locus
from promptpotter.domain.optimizer_state import PARSE_FAILURE_MALFORMED, PARSE_FAILURE_TOOLING
from promptpotter.domain.strict_model import StrictModel
from promptpotter.domain.wounds import ValidationFailure

if TYPE_CHECKING:
    from promptpotter.application.bench.node_context import NodeContext
    from promptpotter.domain.opt_search_point import OptSearchPoint
    from promptpotter.domain.pipeline_schema import PipelineNode
    from promptpotter.domain.results import Proposal

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


def task_description(ctx: NodeContext[Any], node: PipelineNode) -> str:
    """Ends on *node*'s origin answer format: the extraction contract a paper's task descriptions state (CAPO App. D.1)."""
    parts = [v for v in ctx.framing.to_dict().values() if v]
    if answer_format := ctx.origin.prompt(node.name).fields.get("answer_format"):
        parts.append(answer_format)
    if not parts:
        raise ValueError(
            f"{ctx.optimizer_name} fills its templates with the task description, and this "
            "campaign declares no framing (task_context) and its origin no answer_format"
        )
    return "\n".join(parts)


Rewrites = Literal["prompt", "field"]


class RewriteKnobs(StrictModel):
    rewrites: Annotated[Rewrites, Knob(Scope.POLICY, Estimand.SEARCH)] = Field(
        description="Which loci of a prompt node the reply replaces. `prompt`: the whole prompt "
        "as one text — the model is shown every field rendered together, its reply rides `field` "
        "and the node's other fields empty, as the papers' one-text prompts do. `field`: that "
        "field alone — the model is shown it alone, and the child inherits every other locus "
        "from its first parent."
    )
    field: Annotated[str, Knob(Scope.POLICY, Estimand.SEARCH)] = Field(
        min_length=1,
        description="The prompt field the reply is written into; every prompt node the member "
        "rewrites declares it, or the campaign is refused before it mints.",
    )


def shown(ctx: NodeContext[Any], individual: OptSearchPoint, node: PipelineNode) -> str:
    knobs: RewriteKnobs = ctx.knobs
    if knobs.rewrites == "prompt":
        return individual.render(node)
    return individual.prompt(node.name).fields.get(knobs.field, "")


def rewritten(ctx: NodeContext[Any], node: PipelineNode, text: str) -> dict[Locus, str]:
    """Under ``prompt`` every other field is emptied: the reply replaced the whole ``render()`` shown."""
    knobs: RewriteKnobs = ctx.knobs
    written = {Locus(node.name, knobs.field): text}
    if knobs.rewrites == "field":
        return written
    return {**{Locus(node.name, f): "" for f in node.prompt_fields}, **written}


def marked(text: str) -> str | None:
    """The last ``<prompt>`` block: an answer comes after any echo of the template's example."""
    found = [m.strip() for m in _PROMPT.findall(text) if m.strip()]
    return found[-1] if found else None


def unmarked(node: str, raw: str) -> ValidationFailure:
    """An empty reply is the provider's failure, not the prompt's: missing data, never a verdict."""
    reason = PARSE_FAILURE_MALFORMED if raw.strip() else PARSE_FAILURE_TOOLING
    return ValidationFailure(axis=f"{node}.output", value=raw[:300], allowed=[], reason=reason)


def reply_fields(
    ctx: NodeContext[Any],
    node: PipelineNode,
    raw: str,
    parse: Callable[[str], str | None] = marked,
) -> tuple[dict[Locus, str], list[ValidationFailure]]:
    text = parse(raw)
    return ({}, [unmarked(ctx.node, raw)]) if text is None else (rewritten(ctx, node, text), [])


def child(
    ctx: NodeContext[Any],
    node: PipelineNode,
    parents: Sequence[OptSearchPoint],
    raw: str,
    changes: str,
    *,
    parse: Callable[[str], str | None] = marked,
) -> Proposal:
    written, failures = reply_fields(ctx, node, raw, parse)
    return ctx.child(parents, failures=failures, prompt_loci=written, changes_description=changes)


def rewrite(
    ctx: NodeContext[Any],
    proposal: Proposal,
    raw: str,
    changes: str | None = None,
    *,
    parse: Callable[[str], str | None] = marked,
) -> Proposal:
    written, failures = reply_fields(ctx, ctx.prompt_node, raw, parse)
    if failures:
        failures = [*proposal.validation_failures, *failures]
        return proposal.model_copy(update={"validation_failures": failures})
    return ctx.edit(proposal, prompt_loci=written, changes_description=changes)
