from __future__ import annotations

import contextvars
import functools
import hashlib
import json
import logging
from dataclasses import dataclass
from typing import Any

from promptpotter.config.paths import optimizer_assets_root, optimizer_pipeline_path
from promptpotter.config.settings import PROMPT_STRING_FIELDS
from promptpotter.domain.l1_layout import (
    L1_LAYOUT_SLOTS,
    NODE_LAYOUTS,
    L1Layout,
    validate_l1_layout,
)
from promptpotter.domain.opt_search_point import PromptTemplate
from promptpotter.domain.pipeline_schema import PipelineSchema
from promptpotter.infrastructure.store.io import read_json, read_yaml
from promptpotter.shared.instrument import instrument_mode

logger = logging.getLogger(__name__)

__all__ = [
    "OPTIMIZER_PIPELINE_PATH",
    "base_optimizer_template",
    "combined_optimizer_prompt_hash",
    "compute_optimizer_prompt_hashes",
    "effective_optimizer_prompts",
    "get_optimizer_config_overrides",
    "get_optimizer_schema",
    "load_optimizer_prompt",
    "load_optimizer_set_overrides",
    "optimizer_manifest",
    "optimizer_resolved_schemas",
    "resolve_node_layout",
    "resolve_node_override",
    "set_optimizer_prompt_overrides",
]

# The optimizer's own pipeline, split by authorship: the manifest is operator-authored
# (nodes, prompts, the graph view) and the schema registry is generated from the Pydantic
# models by ``scripts/build_optimizer_schemas.py``. One file could not be both — the
# generator's rewrite would reformat the operator's prose on every CI run.
#
# INSTALL CONTENT, not a dataset. These are install-global by contract (one file
# configures the optimizer for every campaign), so they live under the package and ship
# in the wheel — never among the benchmark datasets, where a parent walk resolves to
# ``site-packages/datasets/``, the HuggingFace library's directory.
#
# The manifest resolves through ``optimizer_pipeline_path()`` and the registry does not:
# the operator may shadow the file they author, never the file we generate.
OPTIMIZER_PIPELINE_PATH = optimizer_pipeline_path()
OPTIMIZER_SCHEMAS_PATH = optimizer_assets_root() / "resolved_schemas.json"

# Per-cycle override of the optimizer prompts, keyed by optimizer node
# (`l1_generate` / `l1_critique` / `l2_context` / `l3_plan`) → a partial
# `PromptTemplate`-field dict (plus the structural `layout` / `output_schema_field_names` /
# `model` levers), resolved by `resolve_node_override`. ONE channel, two callers — both
# task-isolated:
#   1. the OUTER L4 cycle binds its specialized optimizer prompt SET here
#      (`load_optimizer_set_overrides`, from `OptimizationConfig.optimizer_set`,
#      set at the runner seam) so it reasons about editing an inner optimizer; and
#   2. the L4 inner-cycle runner binds the OUTER's per-node MUTATIONS here (inside
#      the inner asyncio task) so those mutations shape the inner cycle's prompts.
# Because each inner cycle runs in its own task, an outer binding and the
# inner (mutation) binding never collide — the inner task overwrites its copy. A
# ContextVar — not a global — so every level at any recursion depth carries its
# own. Default `None` = no override (every normal, non-L4 cycle).
_OPTIMIZER_PROMPT_OVERRIDES: contextvars.ContextVar[dict[str, dict[str, Any]] | None] = (
    contextvars.ContextVar("optimizer_prompt_overrides", default=None)
)


def set_optimizer_prompt_overrides(overrides: dict[str, dict[str, Any]] | None) -> None:
    _OPTIMIZER_PROMPT_OVERRIDES.set(overrides or None)


def get_optimizer_config_overrides() -> dict[str, Any] | None:
    """The optimizer decoding clamp, applied LAST so it beats both the node's file config and any
    per-call override. Only instrument mode sets it — that is what makes an inner cycle near-deterministic."""
    mode = instrument_mode()
    return mode.optimizer_clamp if mode is not None else None


def load_optimizer_set_overrides(opt_set: str) -> dict[str, dict[str, Any]]:
    """A named set whose file is MISSING raises: ``optimizer_set`` is an ``Estimand.SEARCH`` axis, so
    falling back to the default would attribute a measurement to a prompt set it never ran."""
    if not opt_set:
        return {}
    path = optimizer_assets_root() / "sets" / f"{opt_set}.yaml"
    if not path.exists():
        raise FileNotFoundError(f"optimizer_set {opt_set!r}: no prompt set at {path}")
    data = read_yaml(path)
    return {k: v for k, v in data.items() if isinstance(v, dict)}


@functools.lru_cache(maxsize=1)
def optimizer_manifest() -> dict[str, Any]:
    """Public because this is what callers hash and render — the raw bytes stopped being a meaningful
    identity once the file carried comments and block scalars. Warns once when it is not the shipped one."""
    if optimizer_assets_root() / "pipeline.yaml" != OPTIMIZER_PIPELINE_PATH:
        logger.warning(
            "optimizer manifest OVERRIDDEN: reading %s instead of the manifest shipped with "
            "the package. Provider, model and temperature for every optimizer node come from "
            "that file.",
            OPTIMIZER_PIPELINE_PATH,
        )
    manifest: dict[str, Any] = read_yaml(OPTIMIZER_PIPELINE_PATH)
    return manifest


@functools.lru_cache(maxsize=1)
def optimizer_resolved_schemas() -> dict[str, Any]:
    """The generated schema registry keyed ``{family}/{version}``."""
    schemas: dict[str, Any] = read_json(OPTIMIZER_SCHEMAS_PATH)
    return schemas


def _resolved_key(family: str, version: Any) -> str:
    return f"{family}/{version}" if version is not None else family


@functools.lru_cache(maxsize=1)
def get_optimizer_schema() -> PipelineSchema:
    """The schema registry is a sibling file because it is generated, not authored; this is the only
    place the two halves meet."""
    from promptpotter.domain.pipeline_parsing import parse_resolved_schema
    from promptpotter.domain.pipeline_schema import PipelineNode

    data = optimizer_manifest()
    resolved_schemas = optimizer_resolved_schemas()

    nodes: list[PipelineNode] = []
    for name, node_data in data.get("nodes", {}).items():
        nc = node_data.get("config", {})
        kwargs: dict[str, Any] = {
            "name": name,
            "current_config": nc,
            "param_keys": set(node_data.get("optimizer", {}).get("param_keys", [])),
        }
        if sf := nc.get("schema_family"):
            key = _resolved_key(sf, nc.get("schema_version"))
            if key in resolved_schemas:
                kwargs["output_schema"] = parse_resolved_schema(resolved_schemas[key])
        nodes.append(PipelineNode(**kwargs))

    return PipelineSchema(
        name=data.get("name", ""),
        version=data.get("version", ""),
        nodes=nodes,
    )


def optimizer_node_config(node: str) -> dict[str, Any]:
    """The single read accessor for optimizer-node tunables, which live only in the optimizer
    pipeline file — never in a per-campaign config copy."""
    schema_node = get_optimizer_schema().get_node(node)
    if schema_node is None:
        raise KeyError(f"Unknown optimizer node: {node!r}")
    return schema_node.current_config


def optimizer_model(node: str = "l1_generate") -> str:
    return str(optimizer_node_config(node)["model"])


def _resolved_prompt_for_node(name: str) -> dict[str, Any] | None:
    data = optimizer_manifest()
    node_cfg = data.get("nodes", {}).get(name, {}).get("config", {})
    family = node_cfg.get("prompt_family")
    if not family:
        return None
    key = _resolved_key(family, node_cfg.get("prompt_version"))
    body = data.get("resolved_prompts", {}).get(key)
    return body if isinstance(body, dict) else None


@functools.lru_cache(maxsize=32)
def base_optimizer_template(name: str) -> PromptTemplate:
    """Override-free: the base an L4 prose mutation merges onto, and the declaration of the inline
    ``{{tokens}}`` (``{{n_variants}}``, ``{{citable_fields}}``) that mutation must preserve."""
    body = _resolved_prompt_for_node(name)
    if body is None:
        raise KeyError(
            f"Optimizer prompt '{name}' not found in resolved_prompts registry "
            f"(check nodes.{name}.config.prompt_family/version)."
        )
    return PromptTemplate(**body)


def effective_optimizer_prompts(
    schema: PipelineSchema | None,
    pipeline_params: dict[str, Any] | None,
) -> dict[str, dict[str, str]]:
    """``{}`` off the recursion — a node qualifies only if it names an optimizer prompt we hold the
    base for AND advertises ``PromptTemplate`` fields, which no normal campaign's nodes do."""
    if schema is None:
        return {}
    owned = set(list_optimizer_prompts())
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


def load_optimizer_prompt(name: str) -> PromptTemplate:
    """Every load runs ``validate_template``, so a template naming a slot outside ``INJECTIONS`` and
    the per-template extras raises at load time rather than silently rendering empty."""
    from promptpotter.application.optimization.dispatch.facade import validate_template

    template = base_optimizer_template(name)
    if fields := resolve_node_override(name).prompt_fields:
        template = template.model_copy(update=fields)
    validate_template(name, template)
    return template


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


def _single_model_override() -> tuple[str | None, str | None]:
    """The one inner-optimizer ``(model, provider)`` the outer carrier node set — fanned onto
    every node. Empty on every normal cycle and for an outer optimizer prompt SET (prose only)."""
    for nd in (_OPTIMIZER_PROMPT_OVERRIDES.get() or {}).values():
        if isinstance(nd, dict) and isinstance(nd.get("model"), str) and nd["model"]:
            prov = nd.get("provider")
            return nd["model"], prov if isinstance(prov, str) and prov else None
    return None, None


def resolve_node_override(node: str) -> ResolvedNodeOverride:
    """A rename target is dropped when it is a non-identifier, a self-rename, or a duplicate; a
    collision is rejected at the apply site. A bad L4 mutation must score poorly, never break the run."""
    raw = _node_override(node)
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
    model, provider = _single_model_override()
    return ResolvedNodeOverride(
        prompt_fields=prompt_fields, schema_field_names=names, model=model, provider=provider
    )


def resolve_node_layout(node: str) -> L1Layout:
    """PARTIAL per-slot replacement: a named slot replaces the floor's list, an omitted one keeps it.
    The GUARD RAIL rolls a bad edit back to the floor, so it scores no-improvement, not starvation."""
    spec = NODE_LAYOUTS[node]
    # The `editor` field is a contract, so it is asked rather than assumed. `l1_generate`'s
    # layout is L2's in-campaign surface (`opt_sp.memory.l1_layout`) and nothing here applies
    # to it — reaching this with that node means a caller believes in an L4 lever that has no
    # code path, and silence would let the belief survive.
    if spec.editor != "l4":
        raise ValueError(
            f"resolve_node_layout({node!r}): this node's layout is edited by {spec.editor!r}, "
            "not L4. Only `editor='l4'` nodes resolve a layout through the per-node override "
            "channel; l1_generate's rides opt_sp.memory.l1_layout instead."
        )
    raw = _node_override(node).get("layout")
    if not isinstance(raw, dict) or not raw:
        return spec.floor
    update: dict[str, list[str]] = {}
    for slot in L1_LAYOUT_SLOTS:
        vals = raw.get(slot)
        if isinstance(vals, list) and all(isinstance(v, str) for v in vals):
            update[slot] = list(vals)
    if not update:
        return spec.floor
    merged = spec.floor.model_copy(update=update)
    result = validate_l1_layout(merged, spec=spec)
    if not result.is_valid:
        logger.warning(
            "L4 layout edit for %r rolled back to floor (guard rail): %s",
            node,
            [o.validator_id for o in result.outcomes],
        )
        return spec.floor
    return merged


def list_optimizer_prompts() -> list[str]:
    data = optimizer_manifest()
    return sorted(
        name
        for name, node in data.get("nodes", {}).items()
        if node.get("config", {}).get("prompt_family")
    )


def compute_optimizer_prompt_hashes() -> dict[str, str]:
    """Three parts — the template, the resolved layout, the resolved config — because all three decide
    what the node produces; without config, repointing a node's MODEL left this hash unmoved."""
    out: dict[str, str] = {}
    for name in list_optimizer_prompts():
        tpl = load_optimizer_prompt(name)
        blob = tpl.model_dump_json()
        if (spec := NODE_LAYOUTS.get(name)) is not None:
            # Only an `editor == "l4"` node can have its layout moved by the override channel
            # this hash exists to notice. `l1_generate` is edited by L2, in-campaign, through
            # `opt_sp.memory.l1_layout` — per-cycle state that has no business in a manifest
            # hash — so it contributes its floor, which is exactly what an L4 edit leaves it at.
            layout = resolve_node_layout(name) if spec.editor == "l4" else spec.floor
            blob += layout.model_dump_json()
        blob += json.dumps(optimizer_node_config(name), sort_keys=True, default=str)
        out[name] = hashlib.sha256(blob.encode("utf-8")).hexdigest()[:16]
    return out


def combined_optimizer_prompt_hash() -> str:
    """An audit JOIN KEY, not the drift gate: drift is asked per ROUND, where the answer can name the
    round and fork at it. Not part of ``campaign_id``, which is random per ``new``."""
    per_prompt = compute_optimizer_prompt_hashes()
    blob = json.dumps(per_prompt, sort_keys=True)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()[:12]
