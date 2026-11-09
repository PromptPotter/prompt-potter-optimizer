from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from promptpotter.application.bench.node_context import Calls
from promptpotter.application.optimizer_manifest import (
    SelectedOptimizer,
    checkin_manifest,
    running_prompt,
)
from promptpotter.application.optimizers.potter.dispatch.injections.registry import (
    validate_template,
)
from promptpotter.application.optimizers.potter.dispatch.layout import resolve_node_layout
from promptpotter.application.optimizers.potter.records import L1Layout, L2Memory
from promptpotter.domain.opt_search_point import OptimizerPromptTemplate
from promptpotter.domain.pipeline_schema import PipelineSchema

if TYPE_CHECKING:
    from promptpotter.application.bench.node_context import NodeContext
    from promptpotter.application.runner.inner.tasks import InnerCells
    from promptpotter.domain.pipeline_params import PipelineParams


logger = logging.getLogger(__name__)


def base_optimizer_template(inner: SelectedOptimizer, name: str) -> OptimizerPromptTemplate:
    """L4-override-free: the bench's check-in where it declares *name*, else *inner*'s under its overlay's edits."""
    checkin = checkin_manifest()
    if checkin.schema.get_node(name) is not None:
        return running_prompt(name, checkin.config, checkin.manifest)
    return running_prompt(name, inner.node_config(name), inner.manifest)


def inner_template(cells: InnerCells, name: str) -> OptimizerPromptTemplate | None:
    """*name*'s base prompt in the manifest whose lever it is; ``None`` where no cell runs it."""
    owner = cells.owners.get(name)
    return None if owner is None else base_optimizer_template(owner, name)


def effective_optimizer_prompts(
    schema: PipelineSchema,
    pipeline_params: PipelineParams,
    cells: InnerCells | None,
) -> dict[str, dict[str, str]]:
    if cells is None:
        return {}
    keys_by_node = schema.node_param_keys()
    out: dict[str, dict[str, str]] = {}
    for node_name in schema.active_steps:
        fields = [
            f
            for f in OptimizerPromptTemplate.RENDER_ORDER
            if f in keys_by_node.get(node_name, set())
        ]
        base = inner_template(cells, node_name) if fields else None
        if base is None:
            continue
        override = pipeline_params.node(node_name)
        out[node_name] = {
            f: str(override[f] if f in override else getattr(base, f)) for f in fields
        }
    return out


def load_optimizer_prompt(ctx: NodeContext, name: str) -> OptimizerPromptTemplate:
    """*name*'s running prompt under the cycle's selection, through the asking member's `Calls`."""
    template = ctx.read(Calls).prompt(name)
    validate_template(name, template)
    return template


def node_layout(node: str, l2: L2Memory) -> L1Layout:
    """L2's steer this cycle, else what an outer declared for the inner cycle, else the floor."""
    steered = l2.steered_layout(node)
    return resolve_node_layout(node) if steered is None else steered
