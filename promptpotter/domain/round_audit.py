"""One shape in both homes: live on ``dashboard.json::current_round.nodes``, at rest as :class:`RoundAudit` dumped."""

from __future__ import annotations

from typing import Any

from pydantic import ConfigDict, Field

from promptpotter.domain.spend import PrefixReading, TokenAccount
from promptpotter.domain.strict_model import StrictModel

__all__ = [
    "LoopWarning",
    "NodeBlock",
    "NodeInput",
    "NodeOutput",
    "RoundAudit",
]


class LoopWarning(StrictModel):
    """One optimizer-loop degradation the self-healing rails recovered from."""

    ts: str
    kind: str
    severity: str
    message: str
    round: int | None = None
    detail: dict[str, Any] = Field(default_factory=dict)


class NodeInput(StrictModel):
    """What a node's call was asked: the template it rendered, or its bare messages where it rendered none."""

    model_config = ConfigDict(frozen=True)

    template_name: str | None = None
    template_fields: dict[str, Any] = Field(default_factory=dict)
    variables: dict[str, Any] = Field(default_factory=dict)
    messages: list[dict[str, Any]] = Field(default_factory=list)


class NodeOutput(StrictModel):
    """What a node's call answered."""

    model_config = ConfigDict(frozen=True)

    response: Any
    reasoning: str | None = Field(
        description="The model's own thinking, where the provider returned one. For a human "
        "reader only: nothing derives, scores, sorts or gates on it."
    )


class NodeBlock(StrictModel):
    """One optimizer node's call in one round: what it was asked, what it answered and what that cost."""

    model_config = ConfigDict(frozen=True)

    input: NodeInput
    output: NodeOutput
    synthesized: bool = Field(
        description="No call was made: `output` is the node's banked answer, replayed on a "
        "resume, and every field below it is empty."
    )
    timestamp: str
    config: dict[str, Any] = Field(
        description="The config the call ASKED for — the only place a model's routing suffix "
        "survives."
    )
    model: str | None = Field(
        description="The provider's echo of the model that answered, which a gateway returns "
        "without its routing suffix."
    )
    usage: TokenAccount | None
    prefix: PrefixReading | None = Field(
        description="The provider's prefix-cache reading of `usage` — `replayed` where our own "
        "reuse cache answered and the counts are the banked call's."
    )
    duration_s: float | None
    finish_reason: str | None
    schema_repair_errors: list[str] = Field(
        description="The schema rules each retried attempt broke; non-empty means a second "
        "round-trip was paid."
    )


class RoundAudit(StrictModel):
    """One round's audit twin: each optimizer node's call in firing order, and the warnings the round raised."""

    round: int
    started_at: str | None = Field(
        description="Null where the calls landed before the round's entry."
    )
    finished_at: str | None = Field(description="Null until the round closes.")
    nodes: dict[str, NodeBlock]
    warnings: list[LoopWarning]
    interrupted: bool = Field(description="The run stopped with this round open.")
