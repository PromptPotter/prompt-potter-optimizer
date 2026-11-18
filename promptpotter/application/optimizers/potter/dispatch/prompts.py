from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from promptpotter.application.optimizers.potter.dispatch.layout import resolve_node_layout
from promptpotter.application.optimizers.potter.records import L1Layout, L2Memory


def node_layout(node: str, l2: L2Memory, levers: Mapping[str, Any]) -> L1Layout:
    """L2's steer this cycle, else what an outer declared for the inner cycle, else the floor."""
    steered = l2.steered_layout(node)
    return resolve_node_layout(node, levers) if steered is None else steered
