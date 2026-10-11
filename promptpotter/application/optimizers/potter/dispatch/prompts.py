from __future__ import annotations

import logging
from collections.abc import Mapping
from typing import Any

from promptpotter.application.optimizer_manifest import (
    SelectedOptimizer,
    checkin_manifest,
    llm_node_document,
    optimizer_prompt,
    running_prompt,
)
from promptpotter.application.optimizers.potter.dispatch.injections.registry import (
    validate_template,
)
from promptpotter.application.optimizers.potter.dispatch.layout import resolve_node_layout
from promptpotter.application.optimizers.potter.records import L1Layout, L2L3Memory
from promptpotter.domain.opt_search_point import OptimizerPromptTemplate
from promptpotter.domain.pipeline_schema import PipelineSchema
from promptpotter.domain.search_point import PROMPT_STRING_FIELDS
from promptpotter.shared.hashing import shapes_optimizer_prompt

shapes_optimizer_prompt(__name__)

logger = logging.getLogger(__name__)

__all__ = [
    "base_optimizer_template",
    "effective_optimizer_prompts",
    "load_optimizer_prompt",
    "node_layout",
]


def base_optimizer_template(inner: SelectedOptimizer, name: str) -> OptimizerPromptTemplate:
    """Override-free, off *inner*'s manifest file unless the bench's check-in declares *name*."""
    checkin = checkin_manifest()
    document = checkin.document if checkin.schema.get_node(name) is not None else inner.document
    return optimizer_prompt(name, (document["nodes"][name] or {}).get("config") or {}, document)


def _running_template(
    name: str, config: Mapping[str, Any], document: Mapping[str, Any]
) -> OptimizerPromptTemplate:
    template = running_prompt(name, config, document)
    validate_template(name, template)
    return template


def effective_optimizer_prompts(
    schema: PipelineSchema | None,
    pipeline_params: dict[str, Any] | None,
    inner: SelectedOptimizer | None,
) -> dict[str, dict[str, str]]:
    if schema is None or inner is None:
        return {}
    keys_by_node = schema.node_param_keys()
    params = pipeline_params or {}
    out: dict[str, dict[str, str]] = {}
    for node_name in schema.active_steps:
        fields = [f for f in PROMPT_STRING_FIELDS if f in keys_by_node.get(node_name, set())]
        if not fields:
            continue
        base = base_optimizer_template(inner, node_name)
        node_params = params.get(node_name)
        override = node_params if isinstance(node_params, dict) else {}
        out[node_name] = {
            f: str(override[f] if f in override else getattr(base, f)) for f in fields
        }
    return out


def load_optimizer_prompt(name: str) -> OptimizerPromptTemplate:
    _node, config, document = llm_node_document(name)
    return _running_template(name, config, document)


def node_layout(node: str, memory: L2L3Memory) -> L1Layout:
    """A layer's steer this cycle, else what an outer declared for the inner cycle, else the floor."""
    steered = memory.steered_layout(node)
    return resolve_node_layout(node) if steered is None else steered
