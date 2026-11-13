from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from promptpotter.application.optimizers.potter.dispatch.layout import resolve_node_layout
from promptpotter.application.optimizers.potter.records import L1Layout, L2Memory
from promptpotter.domain.optimizer_template import OptimizerPromptTemplate
from promptpotter.domain.pipeline_schema import PipelineSchema

if TYPE_CHECKING:
    from promptpotter.domain.l4.cells import InnerCells
    from promptpotter.domain.pipeline_params import PipelineParams


logger = logging.getLogger(__name__)


def effective_optimizer_prompts(
    schema: PipelineSchema,
    pipeline_params: PipelineParams,
    cells: InnerCells | None,
) -> dict[str, dict[str, str]]:
    if cells is None:
        return {}
    keys_by_node = schema.node_param_keys()
    out: dict[str, dict[str, str]] = {}
    for node_name in schema.running:
        fields = [
            f
            for f in OptimizerPromptTemplate.RENDER_ORDER
            if f in keys_by_node.get(node_name, set())
        ]
        base = cells.template(node_name) if fields else None
        if base is None:
            continue
        override = pipeline_params.node(node_name).laid_keys()
        out[node_name] = {
            f: str(override[f] if f in override else getattr(base, f)) for f in fields
        }
    return out


def node_layout(node: str, l2: L2Memory) -> L1Layout:
    """L2's steer this cycle, else what an outer declared for the inner cycle, else the floor."""
    steered = l2.steered_layout(node)
    return resolve_node_layout(node) if steered is None else steered
