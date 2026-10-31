from __future__ import annotations

from typing import Any

from pydantic import TypeAdapter

from promptpotter.domain.strict_model import FrozenModel

# Per model, each gated parameter's admitted values, lowest first.
type Bounds = dict[str, tuple[str, ...]]


class Ask(FrozenModel):
    linkedin: str


class FreeTier(FrozenModel):
    task_nodes: tuple[str, ...]
    task_models: dict[str, Bounds]
    optimizer_models: dict[str, Bounds]
    # The check-in's last config layer, in a dataset file's `campaign:` shape.
    campaign: dict[str, Any]
    ask: Ask


FREE_TIER_ADAPTER = TypeAdapter(FreeTier)
