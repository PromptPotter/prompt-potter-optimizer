from __future__ import annotations

import contextvars
import functools
import hashlib
import json
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from promptpotter.config.paths import optimizer_assets_root, optimizer_pipeline_path
from promptpotter.config.settings import PROMPT_STRING_FIELDS
from promptpotter.domain.l1_layout import (
    NODE_LAYOUTS,
    L1Layout,
    coerce_l1_layout,
    validate_l1_layout,
)
from promptpotter.domain.opt_search_point import OptSearchPoint, PromptTemplate
from promptpotter.domain.pipeline_schema import PipelineSchema
from promptpotter.domain.validators import ValidatorOutcome
from promptpotter.infrastructure.store.io import read_json, read_yaml
from promptpotter.shared.instrument import instrument_mode

logger = logging.getLogger(__name__)

__all__ = [
    "base_optimizer_template",
    "combined_optimizer_prompt_hash",
    "compute_optimizer_prompt_hashes",
    "effective_optimizer_prompts",
    "get_optimizer_config_overrides",
    "get_optimizer_schema",
    "load_optimizer_prompt",
    "load_optimizer_set_overrides",
    "node_layout",
    "optimizer_manifest",
    "optimizer_resolved_schemas",
    "resolve_layout_override",
    "resolve_node_layout",
    "resolve_node_override",
    "resolved_overrides",
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
# the operator may shadow the file they author, never the file we generate. The manifest's
# path is resolved per read rather than bound here, because binding it at import let a
# long-running server keep serving the model it saw at startup after the operator had
# edited or shadowed the file — and label it "current" on the node inspector.
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


def optimizer_manifest() -> dict[str, Any]:
    """Public because this is what callers hash and render — the raw bytes stopped being a meaningful
    identity once the file carried comments and block scalars. Resolved and stat-ed on every call so a
    hand-edit and a tenant shadow both take effect without a restart: this is the ONE file an operator
    edits to change the optimizer's model, and a process that cached it at import reported the old one
    as live."""
    path = optimizer_pipeline_path()
    try:
        stamp = path.stat().st_mtime_ns
    except OSError:
        # A vanished manifest is the reader's error to raise, not this line's — fall through on a
        # stamp no real file can hold so the cache cannot answer for a file that is gone.
        stamp = -1
    return _manifest_at(path, stamp)


@functools.lru_cache(maxsize=2)
def _manifest_at(path: Path, _mtime_ns: int) -> dict[str, Any]:
    """Keyed on the resolved path AND its mtime, so an edit invalidates its own entry. Two slots is
    the whole population: the shipped manifest and one tenant shadow are all that ever alternate."""
    if optimizer_assets_root() / "pipeline.yaml" != path:
        logger.warning(
            "optimizer manifest OVERRIDDEN: reading %s instead of the manifest shipped with "
            "the package. Provider, model and temperature for every optimizer node come from "
            "that file.",
            path,
        )
    manifest: dict[str, Any] = read_yaml(path)
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
    # layout is L2's in-campaign surface (`opt_sp.memory.l1_layout`) and nothing here applies
    # to it — reaching this with that node means a caller believes in an L4 lever that has no
    # code path, and silence would let the belief survive.
    if spec.editor != "l4":
        raise ValueError(
            f"resolve_layout_override({node!r}): this node's layout is edited by {spec.editor!r}, "
            "not L4. Only `editor='l4'` nodes resolve a layout through the per-node override "
            "channel; l1_generate's rides opt_sp.memory.l1_layout instead."
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


def node_layout(node: str, opt_sp: OptSearchPoint) -> L1Layout:
    """**The layout ``node`` renders under, this cycle — the one question every fill asks.**

    Two storage channels, because the two edits have different lifetimes and neither can hold the
    other: L2's edit of `l1_generate` is per-cycle searchpoint state that must survive a resume, so
    it lives on `opt_sp.memory.l1_layout`; an L4 edit binds a whole inner cycle from OUTSIDE its
    searchpoint, so it rides the override ContextVar. `NodeLayoutSpec.editor` is what says which —
    asked HERE and nowhere else. Every call site that branched on it wrote the ternary again, and
    the split is what made "which panels does this node see" a three-file question."""
    if NODE_LAYOUTS[node].editor == "l2":
        return opt_sp.memory.l1_layout
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
