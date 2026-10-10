from __future__ import annotations

from dataclasses import dataclass
from typing import Annotated, Literal

from promptpotter.shared.hashing import shapes_optimizer_prompt

Delivery = Literal[
    # In the request that carries the task — a system or user message. Always seen.
    "request",
    # The eager half of an injected artifact: an Agent Skill's frontmatter, listed unasked.
    "artifact_meta",
    # The lazy half, reached only if the model opens it (`connectors/harbor.py::_skill_opened`).
    "artifact_body",
    # A tool's own name / description / input schema: what the model reads to choose a call.
    "tool_def",
    # Shapes the generation without being text the model reads — temperature, turn budget, effort.
    "harness",
]

LeafKind = Literal["prose", "config", "schema", "program"]

Visibility = Literal[
    # The model sees it whether or not it asks.
    "eager",
    # The model sees it only if it opens the artifact. May never arrive.
    "on_demand",
    # Not text the model reads at all.
    "not_text",
]

# Hashed with the prompt: a row edited here changes which keys `node_param_keys` projects.
_VISIBILITY_BY_DELIVERY: Annotated[dict[Delivery, Visibility], shapes_optimizer_prompt] = {
    "request": "eager",
    "artifact_meta": "eager",
    "artifact_body": "on_demand",
    "tool_def": "eager",
    "harness": "not_text",
}


@shapes_optimizer_prompt
def visibility_of(delivery: Delivery) -> Visibility:
    return _VISIBILITY_BY_DELIVERY[delivery]


@dataclass(frozen=True)
class ValueLeaf:
    path: str
    node: str
    key: str
    kind: LeafKind
    delivery: Delivery
    # False ⇒ pinned: declared and delivered, not a search axis.
    mutable: bool

    @property
    def visibility(self) -> Visibility:
        return visibility_of(self.delivery)

    @property
    def may_not_arrive(self) -> bool:
        """Delivered yet maybe never read: a lift across arms differing only here has no proof."""
        return self.visibility == "on_demand"


__all__ = [
    "Delivery",
    "LeafKind",
    "ValueLeaf",
    "Visibility",
    "visibility_of",
]
