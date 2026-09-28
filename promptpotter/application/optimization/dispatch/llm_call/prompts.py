from __future__ import annotations

import hashlib
import json
import logging
from collections.abc import Mapping
from typing import Any

from promptpotter.application.optimization.dispatch.injections.registry import validate_template
from promptpotter.application.optimizer_manifest import (
    SelectedOptimizer,
    bound_optimizer,
    checkin_manifest,
    llm_node_document,
    resolve_node_layout,
    resolve_node_override,
)
from promptpotter.config.settings import PROMPT_STRING_FIELDS
from promptpotter.domain.l1_layout import NODE_LAYOUTS, L1Layout
from promptpotter.domain.opt_search_point import OptimizerPromptTemplate
from promptpotter.domain.optimizer_state import L2L3Memory
from promptpotter.domain.pipeline_schema import PipelineSchema
from promptpotter.shared.hashing import shapes_optimizer_prompt

shapes_optimizer_prompt(__name__)

logger = logging.getLogger(__name__)

__all__ = [
    "base_optimizer_template",
    "compute_optimizer_prompt_hashes",
    "effective_optimizer_prompts",
    "load_optimizer_prompt",
    "node_layout",
]


def _prompt_body(
    document: Mapping[str, Any], config: Mapping[str, Any], node: str
) -> OptimizerPromptTemplate:
    family, version = config.get("prompt_family"), config.get("prompt_version")
    key = f"{family}/{version}" if version is not None else family
    body = (document.get("resolved_prompts") or {}).get(key) if family else None
    if not isinstance(body, dict):
        raise KeyError(
            f"Optimizer prompt for node {node!r} not found in resolved_prompts "
            f"(check nodes.{node}.config.prompt_family/version)."
        )
    return OptimizerPromptTemplate(**body)


def base_optimizer_template(name: str) -> OptimizerPromptTemplate:
    """Override-free and off the family the MANIFEST FILE names: the base an L4 prose mutation
    merges onto, and the declaration of the inline ``{{tokens}}`` that mutation must preserve."""
    node, _config, document = llm_node_document(name)
    file_config = (document["nodes"][node.name] or {}).get("config") or {}
    return _prompt_body(document, file_config, name)


def _running_template(
    name: str, config: Mapping[str, Any], document: Mapping[str, Any]
) -> OptimizerPromptTemplate:
    template = _prompt_body(document, config, name)
    if fields := resolve_node_override(name).prompt_fields:
        template = template.model_copy(update=fields)
    validate_template(name, template)
    return template


def effective_optimizer_prompts(
    schema: PipelineSchema | None,
    pipeline_params: dict[str, Any] | None,
) -> dict[str, dict[str, str]]:
    """``{}`` off the recursion — a node qualifies only if it names an optimizer prompt we hold the
    base for AND advertises ``PromptTemplate`` fields, which no normal campaign's nodes do."""
    if schema is None:
        return {}
    owned = {*bound_optimizer().llm_nodes, *checkin_manifest().schema.active_steps}
    keys_by_node = schema.node_param_keys()
    params = pipeline_params or {}
    out: dict[str, dict[str, str]] = {}
    for node_name in schema.active_steps:
        if node_name not in owned:
            continue
        fields = [f for f in PROMPT_STRING_FIELDS if f in keys_by_node.get(node_name, set())]
        if not fields:
            continue
        base = base_optimizer_template(node_name)
        node_params = params.get(node_name)
        override = node_params if isinstance(node_params, dict) else {}
        out[node_name] = {
            f: str(override[f] if f in override else getattr(base, f)) for f in fields
        }
    return out


def load_optimizer_prompt(name: str) -> OptimizerPromptTemplate:
    """Every load runs ``validate_template``, so a template naming a slot outside ``injection_table()``
    and the per-template extras raises at load time rather than silently rendering empty."""
    _node, config, document = llm_node_document(name)
    return _running_template(name, config, document)


def node_layout(node: str, memory: L2L3Memory) -> L1Layout:
    """**The layout ``node`` renders under, this cycle — the one question every fill asks.**

    Two storage channels, because the two edits have different lifetimes and neither can hold the
    other: L2's edit of `l1_generate` is per-cycle optimizer state that must survive a resume, so
    it lives on `PotterState.memory.l1_layout`; an L4 edit binds a whole inner cycle from OUTSIDE its
    state, so it rides the override ContextVar. `NodeLayoutSpec.editor` is what says which —
    asked HERE and nowhere else. Every call site that branched on it wrote the ternary again, and
    the split is what made "which panels does this node see" a three-file question."""
    if NODE_LAYOUTS[node].editor == "l2":
        return memory.l1_layout
    return resolve_node_layout(node)


def compute_optimizer_prompt_hashes(selected: SelectedOptimizer) -> dict[str, str]:
    """Per llm node of *selected*: three parts — the template, the resolved layout, the resolved
    config — because all three decide what the node produces; without config, repointing a node's
    MODEL left this hash unmoved."""
    out: dict[str, str] = {}
    for name in selected.llm_nodes:
        config = selected.node_config(name)
        blob = _running_template(name, config, selected.document).model_dump_json()
        if (spec := NODE_LAYOUTS.get(name)) is not None:
            # Only an `editor == "l4"` node can have its layout moved by the override channel
            # this hash exists to notice. `l1_generate` is edited by L2, in-campaign, through
            # `PotterState.memory.l1_layout` — per-cycle state that has no business in a manifest
            # hash — so it contributes its floor, which is exactly what an L4 edit leaves it at.
            layout = resolve_node_layout(name) if spec.editor == "l4" else spec.floor
            blob += layout.model_dump_json()
        blob += json.dumps(config, sort_keys=True, default=str)
        out[name] = hashlib.sha256(blob.encode("utf-8")).hexdigest()[:16]
    return out
