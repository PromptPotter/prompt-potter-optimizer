from __future__ import annotations

import logging
from collections.abc import Mapping
from typing import TYPE_CHECKING, Any

from promptpotter.application.optimizer_manifest import (
    SelectedOptimizer,
    checkin_manifest,
    llm_node_manifest,
    node_prompt,
    running_prompt,
)
from promptpotter.application.optimizers.potter.dispatch.injections.registry import (
    validate_template,
)
from promptpotter.application.optimizers.potter.dispatch.layout import resolve_node_layout
from promptpotter.application.optimizers.potter.records import L1Layout, L2Memory
from promptpotter.domain.opt_search_point import OptimizerPromptTemplate
from promptpotter.domain.pipeline_manifest import PipelineManifest
from promptpotter.domain.pipeline_schema import PipelineSchema
from promptpotter.shared.hashing import shapes_optimizer_prompt

if TYPE_CHECKING:
    from promptpotter.application.runner.inner.tasks import InnerCells

shapes_optimizer_prompt(__name__)

logger = logging.getLogger(__name__)


def base_optimizer_template(inner: SelectedOptimizer, name: str) -> OptimizerPromptTemplate:
    """L4-override-free: the bench's check-in where it declares *name*, else *inner*'s under its overlay's edits."""
    checkin = checkin_manifest()
    if (held := checkin.schema.get_node(name)) is not None:
        return node_prompt(name, held.current_config, checkin.manifest)
    return node_prompt(name, inner.node_config(name), inner.manifest)


def inner_template(cells: InnerCells, name: str) -> OptimizerPromptTemplate | None:
    """*name*'s base prompt in the manifest whose lever it is; ``None`` where no cell runs it."""
    owner = cells.owners.get(name)
    return None if owner is None else base_optimizer_template(owner, name)


def _running_template(
    name: str, config: Mapping[str, Any], manifest: PipelineManifest
) -> OptimizerPromptTemplate:
    template = running_prompt(name, config, manifest)
    validate_template(name, template)
    return template


def effective_optimizer_prompts(
    schema: PipelineSchema,
    pipeline_params: dict[str, Any] | None,
    cells: InnerCells | None,
) -> dict[str, dict[str, str]]:
    if cells is None:
        return {}
    keys_by_node = schema.node_param_keys()
    params = pipeline_params or {}
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
        node_params = params.get(node_name)
        override = node_params if isinstance(node_params, dict) else {}
        out[node_name] = {
            f: str(override[f] if f in override else getattr(base, f)) for f in fields
        }
    return out


def load_optimizer_prompt(name: str) -> OptimizerPromptTemplate:
    _node, config, manifest = llm_node_manifest(name)
    return _running_template(name, config, manifest)


def node_layout(node: str, l2: L2Memory) -> L1Layout:
    """L2's steer this cycle, else what an outer declared for the inner cycle, else the floor."""
    steered = l2.steered_layout(node)
    return resolve_node_layout(node) if steered is None else steered
