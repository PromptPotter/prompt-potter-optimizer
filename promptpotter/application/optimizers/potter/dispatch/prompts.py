from __future__ import annotations

import hashlib
import json
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
from promptpotter.application.optimizers.potter.dispatch.layout import (
    NODE_LAYOUTS,
    resolve_node_layout,
)
from promptpotter.config.settings import PROMPT_STRING_FIELDS
from promptpotter.domain.opt_search_point import OptimizerPromptTemplate
from promptpotter.domain.optimizer_state import L1Layout, L2L3Memory
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


def base_optimizer_template(inner: SelectedOptimizer, name: str) -> OptimizerPromptTemplate:
    """Override-free and off the family the MANIFEST FILE names — *inner*'s, the manifest the L4
    inner campaign selects, unless the bench's check-in declares *name*: the base an L4 prose
    mutation merges onto, and the declaration of the inline ``{{tokens}}`` it must preserve."""
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
    """``{}`` off the recursion (*inner* is ``None``) — a node qualifies if it advertises
    ``PromptTemplate`` fields, and the L4 identity refuses an outer one *inner* does not declare."""
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
