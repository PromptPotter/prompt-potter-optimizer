from __future__ import annotations

import re
from collections.abc import Callable, Sequence
from typing import TYPE_CHECKING

from promptpotter.application.bench.node_context import Children, Lineage, Target
from promptpotter.application.optimizers.nodes import RewriteKnobs
from promptpotter.domain.opt_search_point import Locus
from promptpotter.domain.optimizer_state import PARSE_FAILURE_MALFORMED, PARSE_FAILURE_TOOLING
from promptpotter.domain.wounds import ValidationFailure

if TYPE_CHECKING:
    from promptpotter.application.bench.node_context import NodeContext
    from promptpotter.domain.arms import Proposal
    from promptpotter.domain.opt_search_point import OptSearchPoint
    from promptpotter.domain.pipeline_schema import PipelineNode


_PROMPT = re.compile(r"<prompt>(.*?)(?:</prompt>|\Z)", re.DOTALL)


def task_description(ctx: NodeContext, node: PipelineNode) -> str:
    """Ends on *node*'s origin answer format: the extraction contract a paper's task descriptions state (CAPO App. D.1)."""
    parts = [v for v in ctx.read(Target).framing.to_dict().values() if v]
    if answer_format := ctx.read(Lineage).origin.prompt(node.name).fields.get("answer_format"):
        parts.append(answer_format)
    if not parts:
        raise ValueError(
            f"{ctx.node} fills its template with the task description, and this "
            "campaign declares no framing (task_context) and its origin no answer_format"
        )
    return "\n".join(parts)


def shown(ctx: NodeContext, individual: OptSearchPoint, node: PipelineNode) -> str:
    rewrites = ctx.knobs(RewriteKnobs).rewrites
    if rewrites == "prompt":
        return individual.render(node)
    return individual.prompt(node.name).fields.get(rewrites, "")


def rewritten(ctx: NodeContext, node: PipelineNode, text: str) -> dict[Locus, str]:
    """Under ``prompt`` the reply lands in the node's slot and every other open field is emptied:
    it replaced the whole ``render()`` shown."""
    rewrites = ctx.knobs(RewriteKnobs).rewrites
    if rewrites != "prompt":
        return {Locus(node.name, rewrites): text}
    slot = node.prompt_info.rewrite_slot if node.prompt_info else None
    assert slot is not None, "the preflight refuses a whole-prompt rewrite of a node with no slot"
    return {
        **{Locus(node.name, f): "" for f in node.open_prompt_fields},
        Locus(node.name, slot): text,
    }


def marked(text: str) -> str | None:
    """The last ``<prompt>`` block: an answer comes after any echo of the template's example; an unclosed one reads to the reply's end."""
    found = [m.strip() for m in _PROMPT.findall(text) if m.strip()]
    return found[-1] if found else None


def unmarked(node: str, raw: str) -> ValidationFailure:
    """An empty reply is the provider's failure, not the prompt's: missing data, never a verdict."""
    reason = PARSE_FAILURE_MALFORMED if raw.strip() else PARSE_FAILURE_TOOLING
    return ValidationFailure(axis=f"{node}.output", value=raw[:300], allowed=[], reason=reason)


def reply_fields(
    ctx: NodeContext,
    node: PipelineNode,
    raw: str,
    parse: Callable[[str], str | None] = marked,
) -> tuple[dict[Locus, str], list[ValidationFailure]]:
    text = parse(raw)
    return ({}, [unmarked(ctx.node, raw)]) if text is None else (rewritten(ctx, node, text), [])


def child(
    ctx: NodeContext,
    node: PipelineNode,
    parents: Sequence[OptSearchPoint],
    raw: str,
    changes: str,
    *,
    parse: Callable[[str], str | None] = marked,
) -> Proposal:
    written, failures = reply_fields(ctx, node, raw, parse)
    return ctx.read(Children).child(
        parents, failures=failures, prompt_loci=written, changes_description=changes
    )


def rewrite(
    ctx: NodeContext,
    proposal: Proposal,
    raw: str,
    changes: str | None = None,
    *,
    parse: Callable[[str], str | None] = marked,
) -> Proposal:
    written, failures = reply_fields(ctx, ctx.read(Target).prompt_node, raw, parse)
    if failures:
        failures = [*proposal.validation_failures, *failures]
        return proposal.model_copy(update={"validation_failures": failures})
    return ctx.read(Children).edit(proposal, prompt_loci=written, changes_description=changes)
