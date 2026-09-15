"""One addressable tree of everything an arm may mutate, and how each value reaches the model.

**The boundaries between a prompt, a skill, a tool and a folder of context are packaging, not
substance** — an Agent Skill already IS a directory of instructions, resources and programs, and an
MCP tool is a name, a description and a schema, which is text the model reads to decide whether to
call it. So they are leaves of ONE tree rather than four subsystems, and `PipelineSchema.value_tree`
is where that tree is built.

Two properties carry the facts the flat roster could not, and the second is the one that cost a
measurement programme:

**DELIVERY** — the channel a value reaches the model by. The same prose is a system prompt on one
backend and a file the agent must open on another, and nothing in a param NAME says which.

**VISIBILITY** — whether the model sees the value unconditionally, only when it asks, or never
because it is not text at all. Agent Skills are served by progressive disclosure on every harness
that implements them: the frontmatter is eager and the body is on demand. So a skill is not one
value with one visibility — its description decides WHETHER it is opened and its body decides what
happens once it is, which is why they are separate leaves.

`node_param_keys` projects off this tree, so a key's presence has one owner.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Annotated, Literal

from promptpotter.shared.hashing import shapes_optimizer_prompt

Delivery = Literal[
    # In the request that carries the task — a system or user message. Always seen.
    "request",
    # The eager half of an injected artifact: an Agent Skill's frontmatter. The harness lists it
    # unasked so the model can decide whether to open the body.
    "artifact_meta",
    # The lazy half: the artifact's body, reached only if the model opens it. `terminus-2` hands
    # over a path and expects a read; DeepSeek Harness exposes a `skill()` tool. Either way a
    # value delivered here may never arrive, and `connectors/harbor.py::_skill_opened` is how we
    # find out per cell.
    "artifact_body",
    # A tool's own name / description / input schema. Eager on every harness that advertises tools,
    # and prompt surface in every sense that matters: it is what the model reads to choose a call.
    "tool_def",
    # Shapes the generation without being text the model reads — temperature, turn budget, effort.
    "harness",
]

# ``config`` is a node config value whose concrete type its own declaration owns — an enum through
# `param_allowed_values`, a number, a flag. Not re-derived here: a second answer about a value's
# type is one that can disagree with the declaration it came from.
LeafKind = Literal["prose", "config", "schema", "program"]

Visibility = Literal[
    # The model sees it whether or not it asks.
    "eager",
    # The model sees it only if it opens the artifact. May never arrive.
    "on_demand",
    # Not text the model reads at all.
    "not_text",
]

# Hashed with the prompt, because a row edited here changes which leaves the tree emits, so which
# keys `node_param_keys` projects, so what the optimizer is told it may move.
_VISIBILITY_BY_DELIVERY: Annotated[dict[Delivery, Visibility], shapes_optimizer_prompt] = {
    "request": "eager",
    "artifact_meta": "eager",
    "artifact_body": "on_demand",
    "tool_def": "eager",
    "harness": "not_text",
}


@shapes_optimizer_prompt
def visibility_of(delivery: Delivery) -> Visibility:
    """Derived from the channel rather than declared per leaf — a value's visibility is a property
    of HOW it is delivered, and a second declaration could only disagree with the first."""
    return _VISIBILITY_BY_DELIVERY[delivery]


@dataclass(frozen=True)
class ValueLeaf:
    """One value an arm may hold, addressable by ``path``.

    ``key`` is what the node config or the prompt-field roster calls it, so a leaf still names the
    carrier it is written through; ``path`` is what a reader and a future selection address it by.
    """

    path: str
    node: str
    key: str
    kind: LeafKind
    delivery: Delivery
    # False ⇒ pinned: declared, delivered, and not a search axis. Kept in the tree rather than
    # filtered out, because "this is configured and held" is exactly what a reader of the harness
    # needs and what a roster of mutable keys alone can never say.
    mutable: bool

    @property
    def visibility(self) -> Visibility:
        return visibility_of(self.delivery)

    @property
    def may_not_arrive(self) -> bool:
        """Whether this value can be delivered and still never reach the model — so a lift measured
        across arms that differ only here is a lift no evidence supports."""
        return self.visibility == "on_demand"


__all__ = [
    "Delivery",
    "LeafKind",
    "ValueLeaf",
    "Visibility",
    "visibility_of",
]
