from __future__ import annotations

import contextvars
import hashlib
import json
import logging
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from promptpotter.application.campaign_config import DeterminismClamp
from promptpotter.application.optimization.dispatch.injections.registry import validate_template
from promptpotter.application.optimizer_manifest import (
    SelectedOptimizer,
    bound_optimizer,
    checkin_manifest,
    llm_node_document,
)
from promptpotter.config.settings import PROMPT_STRING_FIELDS
from promptpotter.domain.l1_layout import (
    NODE_LAYOUTS,
    L1Layout,
    coerce_l1_layout,
    validate_l1_layout,
)
from promptpotter.domain.opt_search_point import OptimizerPromptTemplate, PromptTemplate
from promptpotter.domain.optimizer_state import L2L3Memory
from promptpotter.domain.pipeline_schema import PipelineSchema
from promptpotter.domain.validators import ValidatorOutcome
from promptpotter.shared.hashing import shapes_optimizer_prompt

shapes_optimizer_prompt(__name__)

logger = logging.getLogger(__name__)

__all__ = [
    "base_optimizer_template",
    "combined_optimizer_prompt_hash",
    "compute_optimizer_prompt_hashes",
    "effective_optimizer_prompts",
    "get_optimizer_config_overrides",
    "load_optimizer_prompt",
    "node_layout",
    "resolve_layout_override",
    "resolve_node_layout",
    "resolve_node_override",
    "resolved_overrides",
    "set_determinism_clamp",
    "set_optimizer_prompt_overrides",
]

# The L4 inner-cycle runner binds the OUTER's per-node MUTATIONS here (inside the inner asyncio
# task), keyed by optimizer node → a partial `PromptTemplate`-field dict plus the structural
# `layout` / `output_schema_field_names` / `model` levers, resolved by `resolve_node_override`.
# A ContextVar — not a global — so every recursion level carries its own. `None` = no override.
_OPTIMIZER_PROMPT_OVERRIDES: contextvars.ContextVar[dict[str, dict[str, Any]] | None] = (
    contextvars.ContextVar("optimizer_prompt_overrides", default=None)
)


def set_optimizer_prompt_overrides(overrides: dict[str, dict[str, Any]] | None) -> None:
    _OPTIMIZER_PROMPT_OVERRIDES.set(overrides or None)


# This cycle's `OptimizationConfig.determinism`, bound at `runner/entry.py::run_optimization`. A
# ContextVar like its neighbour: each L4 level runs in its own task, so no pin crosses a level.
_DETERMINISM: contextvars.ContextVar[DeterminismClamp | None] = contextvars.ContextVar(
    "determinism_clamp", default=None
)


def set_determinism_clamp(clamp: DeterminismClamp | None) -> None:
    _DETERMINISM.set(clamp)


def get_optimizer_config_overrides() -> dict[str, Any] | None:
    """The campaign's decoding + route clamp, applied LAST so it beats both the node's file config
    and any per-call override. An unpinned field is ABSENT, never a ``None`` that would erase one."""
    clamp = _DETERMINISM.get()
    if clamp is None:
        return None
    return clamp.model_dump(exclude_none=True) or None


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


@dataclass(frozen=True)
class ResolvedNodeOverride:
    """``model`` / ``provider`` are the SINGLE inner-optimizer model the outer carrier node set,
    returned for EVERY node so one choice fans across the whole inner optimizer at apply time."""

    prompt_fields: dict[str, Any]
    schema_field_names: dict[str, str]
    model: str | None
    provider: str | None


def _node_override(node: str) -> dict[str, Any]:
    raw = (_OPTIMIZER_PROMPT_OVERRIDES.get() or {}).get(node)
    return raw if isinstance(raw, dict) else {}


def _single_model(overrides: dict[str, Any]) -> tuple[str | None, str | None]:
    """The one inner-optimizer ``(model, provider)`` the outer carrier node set — fanned onto
    every node. Empty on every normal cycle and for an outer optimizer prompt SET (prose only)."""
    for nd in overrides.values():
        if isinstance(nd, dict) and isinstance(nd.get("model"), str) and nd["model"]:
            prov = nd.get("provider")
            return nd["model"], prov if isinstance(prov, str) and prov else None
    return None, None


def _resolved_prompt_parts(raw: dict[str, Any]) -> tuple[dict[str, Any], dict[str, str]]:
    """One node's prompt fields and the rename map that SURVIVES its declaration. A rename target is
    dropped when it is a non-identifier, a self-rename, or a duplicate; a collision is rejected at
    the apply site. A bad L4 mutation must score poorly, never break the run."""
    prompt_fields = {k: v for k, v in raw.items() if k in PromptTemplate.model_fields}
    names: dict[str, str] = {}
    rename_raw = raw.get("output_schema_field_names")
    if isinstance(rename_raw, dict):
        for field, wire in rename_raw.items():
            if not isinstance(field, str) or not isinstance(wire, str):
                continue
            wire = wire.strip()
            if not wire.isidentifier() or wire == field:
                continue
            names[field] = wire
        targets = list(names.values())
        names = {f: w for f, w in names.items() if targets.count(w) == 1}
    return prompt_fields, names


def resolve_node_override(node: str) -> ResolvedNodeOverride:
    prompt_fields, names = _resolved_prompt_parts(_node_override(node))
    model, provider = _single_model(_OPTIMIZER_PROMPT_OVERRIDES.get() or {})
    return ResolvedNodeOverride(
        prompt_fields=prompt_fields, schema_field_names=names, model=model, provider=provider
    )


def resolve_layout_override(
    node: str, raw_layout: object
) -> tuple[L1Layout, list[ValidatorOutcome]]:
    """One node's floor with an L4 ``{panel: slot}`` edit applied, and the outcomes that edit
    breaks — empty on a clean apply, where the returned layout is what the inner cycle renders.

    ONE derivation asked at two boundaries. `validators/l1_strict.py` convicts the PROPOSAL, where
    the arm can be told and costs a synthetic 0; this module re-asks at render time, one recursion
    level down, where nothing can be told and the arm has already paid for a whole inner campaign.
    Two derivations would let the boundary that rejects and the boundary that applies disagree
    about which edits are legal."""
    spec = NODE_LAYOUTS[node]
    # The `editor` field is a contract, so it is asked rather than assumed. `l1_generate`'s
    # layout is L2's in-campaign surface (`Cycle.memory.l1_layout`) and nothing here applies
    # to it — reaching this with that node means a caller believes in an L4 lever that has no
    # code path, and silence would let the belief survive.
    if spec.editor != "l4":
        raise ValueError(
            f"resolve_layout_override({node!r}): this node's layout is edited by {spec.editor!r}, "
            "not L4. Only `editor='l4'` nodes resolve a layout through the per-node override "
            "channel; l1_generate's rides Cycle.memory.l1_layout instead."
        )
    merged = coerce_l1_layout(raw_layout, base=spec.floor)
    if merged is None:
        # Absent is "no layout edit"; a non-empty declaration that coerces to nothing asked for one
        # in a shape no slot can hold. Both land here, and treating them alike is the defect
        # `escalation/firing.py::_parse_l2` already carries the L2 twin of — `l1_layout_unparseable`
        # is that arm's id, shared so one shape cannot be a breach on one path and silence on the other.
        if not raw_layout:
            return spec.floor, []
        return spec.floor, [
            ValidatorOutcome(
                validator_id="l1_layout_unparseable",
                evidence={"keys": sorted(raw_layout) if isinstance(raw_layout, dict) else []},
            )
        ]
    result = validate_l1_layout(merged, spec=spec)
    if not result.is_valid:
        return spec.floor, list(result.outcomes)
    return merged, []


def resolve_node_layout(node: str) -> L1Layout:
    """The layout this node renders under. A declaration that does not apply RAISES: an L1 proposal
    is convicted upstream by `l1_inner_layout_applies`, so what reaches here is operator-authored,
    and rendering the floor for it would attribute the measurement to a layout nobody ran."""
    layout, breaches = resolve_layout_override(node, _node_override(node).get("layout"))
    if breaches:
        raise ValueError(
            f"resolve_node_layout({node!r}): the declared layout edit breaks "
            f"{sorted(o.validator_id for o in breaches)} and cannot be applied"
        )
    return layout


def node_layout(node: str, memory: L2L3Memory) -> L1Layout:
    """**The layout ``node`` renders under, this cycle — the one question every fill asks.**

    Two storage channels, because the two edits have different lifetimes and neither can hold the
    other: L2's edit of `l1_generate` is per-cycle optimizer state that must survive a resume, so
    it lives on `Cycle.memory.l1_layout`; an L4 edit binds a whole inner cycle from OUTSIDE its
    state, so it rides the override ContextVar. `NodeLayoutSpec.editor` is what says which —
    asked HERE and nowhere else. Every call site that branched on it wrote the ternary again, and
    the split is what made "which panels does this node see" a three-file question."""
    if NODE_LAYOUTS[node].editor == "l2":
        return memory.l1_layout
    return resolve_node_layout(node)


def resolved_overrides(overrides: dict[str, dict[str, Any]]) -> dict[str, Any]:
    """What a declaration RESOLVES to — the identity `inner_campaign_id` hashes. Everything the
    resolvers above drop (a key no template carries, a rename that could not be applied, a layout
    edit that lands back on the floor) is dropped here too, so two declarations that render ONE
    prompt hash alike. Hashing the declaration instead bought two inner campaigns for one
    configuration and left neither able to continue the rounds the other banked.

    The model rides OUTSIDE the per-node map because that is where it renders: `_single_model` fans
    one carrier node's choice onto every node, so WHICH node declared it is not a fact about the
    configuration, and keying it per-node made `{a: {model: X}}` and `{b: {model: X}}` two ids for
    one inner optimizer — the same defect one level down."""
    nodes: dict[str, dict[str, Any]] = {}
    for node, raw in overrides.items():
        if not isinstance(raw, dict):
            continue
        prompt_fields, names = _resolved_prompt_parts(raw)
        resolved: dict[str, Any] = dict(prompt_fields)
        if names:
            resolved["output_schema_field_names"] = names
        spec = NODE_LAYOUTS.get(node)
        if spec is not None and spec.editor == "l4":
            layout, _breaches = resolve_layout_override(node, raw.get("layout"))
            if layout != spec.floor:
                resolved["layout"] = layout.model_dump(mode="json")
        if resolved:
            nodes[node] = resolved
    model, provider = _single_model(overrides)
    return {"nodes": nodes, "model": model, "provider": provider}


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
            # `Cycle.memory.l1_layout` — per-cycle state that has no business in a manifest
            # hash — so it contributes its floor, which is exactly what an L4 edit leaves it at.
            layout = resolve_node_layout(name) if spec.editor == "l4" else spec.floor
            blob += layout.model_dump_json()
        blob += json.dumps(config, sort_keys=True, default=str)
        out[name] = hashlib.sha256(blob.encode("utf-8")).hexdigest()[:16]
    return out


def combined_optimizer_prompt_hash(selected: SelectedOptimizer) -> str:
    """An audit JOIN KEY, not the drift gate: drift is asked per ROUND, where the answer can name the
    round and fork at it. Not part of ``campaign_id``, which is random per ``new``."""
    per_prompt = compute_optimizer_prompt_hashes(selected)
    blob = json.dumps(per_prompt, sort_keys=True)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()[:12]
