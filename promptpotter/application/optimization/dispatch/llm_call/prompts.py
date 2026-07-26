"""Optimizer-pipeline manifest loading — schemas + meta-prompt templates.

Single source of truth for optimizer nodes, schemas, and prompts is
``datasets/_optimizer/pipeline.json``. It follows the same shape as a
backend's ``GET /pipeline`` response: ``nodes`` reference
``schema_family``/``schema_version`` + ``prompt_family``/``prompt_version``,
and the bodies live in the top-level ``resolved_schemas`` /
``resolved_prompts`` registries. The optimizer is itself a pipeline.
Prompt loading prefers a Langfuse ``production`` label and falls back to
the local manifest registry.
"""

from __future__ import annotations

import contextvars
import functools
import hashlib
import json
import logging
from dataclasses import dataclass
from typing import Any

from promptpotter.domain.l1_layout import (
    L1_LAYOUT_SLOTS,
    NODE_LAYOUTS,
    L1Layout,
    validate_l1_layout,
)
from promptpotter.domain.opt_search_point import PromptTemplate
from promptpotter.domain.pipeline_schema import PipelineSchema
from promptpotter.infrastructure.store.layout import REPO_ROOT
from promptpotter.shared.instrument import instrument_mode

logger = logging.getLogger(__name__)

__all__ = [
    "OPTIMIZER_PIPELINE_PATH",
    "ResolvedNodeOverride",
    "base_optimizer_template",
    "combined_optimizer_prompt_hash",
    "compute_optimizer_prompt_hashes",
    "get_optimizer_config_overrides",
    "get_optimizer_schema",
    "list_optimizer_prompts",
    "load_optimizer_prompt",
    "load_optimizer_set_overrides",
    "resolve_node_layout",
    "resolve_node_override",
    "set_optimizer_prompt_overrides",
]

OPTIMIZER_PIPELINE_PATH = REPO_ROOT / "datasets" / "_optimizer" / "pipeline.json"

# Per-cycle override of the optimizer meta-prompts, keyed by optimizer node
# (`l1_generate` / `l1_critique` / `l2_context` / `l3_plan`) → a partial
# `PromptTemplate`-field dict (plus the structural `layout` / `output_schema_field_names` /
# `model` levers), resolved by `resolve_node_override`. ONE channel, two callers — both
# task-isolated:
#   1. the OUTER L4 cycle binds its specialized meta-prompt SET here
#      (`load_optimizer_set_overrides`, from `OptimizationConfig.optimizer_set`,
#      set at the runner seam) so it reasons about editing an inner optimizer; and
#   2. the L4 inner-cycle runner binds the OUTER's per-node MUTATIONS here (inside
#      the inner asyncio task) so those mutations shape the inner cycle's prompts.
# Because each inner cycle runs in its own task, an outer (meta) binding and the
# inner (mutation) binding never collide — the inner task overwrites its copy. A
# ContextVar — not a global — so every level at any recursion depth carries its
# own. Default `None` = no override (every normal, non-L4 cycle).
_OPTIMIZER_PROMPT_OVERRIDES: contextvars.ContextVar[dict[str, dict[str, Any]] | None] = (
    contextvars.ContextVar("optimizer_prompt_overrides", default=None)
)


def set_optimizer_prompt_overrides(overrides: dict[str, dict[str, Any]] | None) -> None:
    """Bind per-cycle optimizer-prompt-field overrides for this task's context.

    Keyed by optimizer node; each value is a partial `PromptTemplate`-field map
    merged onto the loaded prompt by :func:`load_optimizer_prompt`. Two callers,
    both task-isolated: the runner seam binds the outer L4 cycle's meta-prompt set
    (:func:`load_optimizer_set_overrides`); the inner runner
    (`runner/inner/cycle.py`) binds the outer's per-node mutations."""
    _OPTIMIZER_PROMPT_OVERRIDES.set(overrides or None)


def get_optimizer_config_overrides() -> dict[str, Any] | None:
    """The optimizer decoding clamp for this task, or ``None`` on every normal call.

    A flat ``{param: value}`` map (``temperature`` + ``seed``) clamped onto EVERY optimizer
    node call by :func:`..call.llm_call`, applied LAST so it beats both the node's file
    config and any per-call override (``l1_generate``'s creativity temp, the dominant noise
    source). Distinct from the prompt-field override above: that swaps meta-prompt TEXT,
    this pins the sampling knobs. Set only by declaring a cycle a measurement instrument
    (:func:`~promptpotter.shared.instrument.enter_instrument_mode`), which is what makes an
    inner campaign a near-deterministic function of its meta-prompt — the outer optimizer's
    own task binds no mode and keeps the file default."""
    mode = instrument_mode()
    return mode.optimizer_clamp if mode is not None else None


def load_optimizer_set_overrides(opt_set: str) -> dict[str, dict[str, Any]]:
    """Load a named optimizer prompt-set's per-node field overrides.

    The L4 outer cycle selects a specialized meta-prompt set via
    ``OptimizationConfig.optimizer_set`` (e.g. ``"meta"`` →
    ``datasets/_optimizer_meta/prompts.json``). The file is a flat
    ``{node: {field: text}}`` map of only the fields that set rewrites; it rides
    the SAME per-node override channel as the inner-cycle mutations
    (:func:`set_optimizer_prompt_overrides` → :func:`resolve_node_override`), so
    every injection slot the set does not name (the ``pipeline_param_catalogue``
    in ``problem_description``, the evidence panels, …) stays intact from
    ``datasets/_optimizer/``. Empty ``opt_set`` or a missing file → ``{}``.

    Non-dict top-level entries (e.g. a ``_doc`` note) are dropped — only real
    per-node field maps are returned.

    A named set whose file is missing RAISES. ``optimizer_set`` is an
    ``Estimand.SEARCH`` axis, so falling back to the default set would run the inner
    prompts while the campaign records that it ran the named one — a measurement
    attributed to a prompt set it never used."""
    if not opt_set:
        return {}
    path = REPO_ROOT / "datasets" / f"_optimizer_{opt_set}" / "prompts.json"
    if not path.exists():
        raise FileNotFoundError(f"optimizer_set {opt_set!r}: no prompt set at {path}")
    data = json.loads(path.read_text(encoding="utf-8"))
    return {k: v for k, v in data.items() if isinstance(v, dict)}


@functools.lru_cache(maxsize=1)
def _load_optimizer_manifest() -> dict[str, Any]:
    """Read the on-disk optimizer-pipeline manifest (cached).

    Single source of truth for nodes, schemas, and prompts. Same shape as
    a backend's ``GET /pipeline`` response: ``nodes`` reference
    ``schema_family``/``schema_version`` + ``prompt_family``/``prompt_version``,
    and the bodies live in the top-level ``resolved_schemas`` /
    ``resolved_prompts`` registries.
    """
    manifest: dict[str, Any] = json.loads(OPTIMIZER_PIPELINE_PATH.read_text(encoding="utf-8"))
    return manifest


def _resolved_key(family: str, version: Any) -> str:
    return f"{family}/{version}" if version is not None else family


@functools.lru_cache(maxsize=1)
def get_optimizer_schema() -> PipelineSchema:
    """Load datasets/_optimizer/pipeline.json as PipelineSchema (cached).

    Follows the same pipeline-schema convention as a backend: each node's
    structured output schema is referenced via ``config.schema_family`` /
    ``config.schema_version`` and resolved against the top-level
    ``resolved_schemas`` registry — same shape ``parse_pipeline_response``
    uses for backend pipelines, so the optimizer is itself a pipeline that
    can later be optimized.
    """
    from promptpotter.domain.pipeline_parsing import parse_resolved_schema
    from promptpotter.domain.pipeline_schema import PipelineNode

    data = _load_optimizer_manifest()
    resolved_schemas = data.get("resolved_schemas", {})

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
    """The resolved config dict for an optimizer node (``datasets/_optimizer/pipeline.json``).

    The single read accessor for optimizer-node tunables — provider, model,
    temperature, reasoning_effort — now that they live only in the optimizer
    pipeline file. Display/default sites (the RoundEnd model line, the preflight
    optimizer-vs-target check, the l1 creativity default) read through here
    instead of a per-campaign config copy."""
    schema_node = get_optimizer_schema().get_node(node)
    if schema_node is None:
        raise KeyError(f"Unknown optimizer node: {node!r}")
    return schema_node.current_config


def optimizer_model(node: str = "l1_generate") -> str:
    """The concrete optimizer model configured for ``node`` (default: the L1 generator)."""
    return str(optimizer_node_config(node)["model"])


def _resolved_prompt_for_node(name: str) -> dict[str, Any] | None:
    """Look up a node's prompt body in ``resolved_prompts``.

    Joins the node's ``config.prompt_family``/``prompt_version`` against
    the manifest's ``resolved_prompts`` registry — same family/version
    indirection backend pipelines use.
    """
    data = _load_optimizer_manifest()
    node_cfg = data.get("nodes", {}).get(name, {}).get("config", {})
    family = node_cfg.get("prompt_family")
    if not family:
        return None
    key = _resolved_key(family, node_cfg.get("prompt_version"))
    body = data.get("resolved_prompts", {}).get(key)
    return body if isinstance(body, dict) else None


@functools.lru_cache(maxsize=32)
def base_optimizer_template(name: str) -> PromptTemplate:
    """The manifest template for *name*, override-free — the base an L4 prose mutation
    merges onto, and the declaration of the inline ``{{tokens}}`` (caller extras such as
    ``{{n_variants}}`` / ``{{citable_fields}}``) such a mutation must preserve."""
    body = _resolved_prompt_for_node(name)
    if body is None:
        raise KeyError(
            f"Optimizer prompt '{name}' not found in resolved_prompts registry "
            f"(check nodes.{name}.config.prompt_family/version)."
        )
    return PromptTemplate(**body)


def load_optimizer_prompt(name: str) -> PromptTemplate:
    """Load an optimizer prompt from the manifest registry.

    Every load runs through :func:`dispatch_hub.validate_template`, so a
    template that references a slot not in
    :data:`dispatch_hub.INJECTIONS` (and not in the per-template extras list)
    raises at load time rather than silently rendering empty.
    """
    from promptpotter.application.optimization.dispatch.facade import validate_template

    template = base_optimizer_template(name)
    if fields := resolve_node_override(name).prompt_fields:
        template = template.model_copy(update=fields)
    validate_template(name, template)
    return template


@dataclass(frozen=True)
class ResolvedNodeOverride:
    """The outer L4 cycle's per-node mutation, resolved from the specimen channel into one surface.

    Every field is empty / ``None`` on a normal (non-L4) cycle, so reading it there is a total
    no-op. ``prompt_fields`` are the ``PromptTemplate``-field edits (the model is ``extra``-strict,
    so it is never one of them); ``schema_field_names`` is the cleaned ``{field: wire_name}`` rename
    map. ``model`` / ``provider`` are the SINGLE inner-optimizer model the outer carrier node set
    (``node_param_keys`` constrains the outer search to one carrier), returned for EVERY node so one
    choice fans across the whole inner optimizer at apply time. ``resolve_node_layout`` is the peer
    for the layout half, which has a non-optional floor and so keeps its own accessor."""

    prompt_fields: dict[str, Any]
    schema_field_names: dict[str, str]
    model: str | None
    provider: str | None


def _node_override(node: str) -> dict[str, Any]:
    """The raw ``overrides[node]`` object bound for this task, or ``{}``."""
    raw = (_OPTIMIZER_PROMPT_OVERRIDES.get() or {}).get(node)
    return raw if isinstance(raw, dict) else {}


def _single_model_override() -> tuple[str | None, str | None]:
    """The one inner-optimizer ``(model, provider)`` the outer carrier node set — fanned onto
    every node. Empty on every normal cycle and for an outer meta-prompt SET (prose only)."""
    for nd in (_OPTIMIZER_PROMPT_OVERRIDES.get() or {}).values():
        if isinstance(nd, dict) and isinstance(nd.get("model"), str) and nd["model"]:
            prov = nd.get("provider")
            return nd["model"], prov if isinstance(prov, str) and prov else None
    return None, None


def resolve_node_override(node: str) -> ResolvedNodeOverride:
    """Resolve the outer L4 cycle's mutation of *node* into one typed surface — the single reader
    of the specimen channel's per-node dict for its prose, schema-rename, and model levers.

    Prose fields merge onto the template in :func:`load_optimizer_prompt`; renames feed
    ``build_l1_response_schema`` via ``effective_l1_field_names``; the single fanned model is
    consumed in :func:`..call.llm_call`'s config merge. A rename target is dropped when it is a
    non-identifier, a no-op self-rename, or a duplicate (two fields claiming one wire name is an
    unresolvable response); a collision with a field that is NOT being renamed is rejected at the
    apply site, which knows the field set. A bad L4 mutation must score poorly, never break the run.
    """
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
    """The effective per-node injection layout for *node* — its floor ± the outer
    L4 cycle's per-node ``layout`` edit.

    The layout edit rides the SAME per-node override channel as the prose edits
    (:func:`set_optimizer_prompt_overrides` → ``overrides[node]["layout"] = {slot:
    [name, ...]}``) — the outer L1 emits it alongside a prose edit; the inner runner
    binds it inside the inner task. It is a PARTIAL per-slot replacement: a slot the
    edit names replaces the floor's list, a slot it omits keeps the floor. The merged
    layout is validated against the node's :data:`NODE_LAYOUTS` spec — a dropped-
    mandatory / unknown-placeholder / dup edit is the GUARD RAIL: it rolls back to the
    floor, so the inner cycle runs on the good default and the bad edit scores as
    no-improvement rather than starving the node. No override bound (every normal,
    non-L4 cycle) → the floor unchanged (:class:`ResolvedNodeOverride`'s peer for the
    structural, not-a-``PromptTemplate``-field, half of a per-node edit)."""
    spec = NODE_LAYOUTS[node]
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
    """Names of nodes that declare a ``prompt_family`` in the manifest."""
    data = _load_optimizer_manifest()
    return sorted(
        name
        for name, node in data.get("nodes", {}).items()
        if node.get("config", {}).get("prompt_family")
    )


def compute_optimizer_prompt_hashes() -> dict[str, str]:
    """SHA-256 (16-char prefix) of each optimizer prompt's effective content.

    Hashes the deterministic ``model_dump_json()`` of the loaded
    ``PromptTemplate`` (so the hash reflects what was actually used,
    Langfuse-overridden or local) plus the node's resolved injection layout —
    a layout-only L4 edit changes which evidence a node sees, so it must move
    the node's hash (nodes without a ``NODE_LAYOUTS`` entry, e.g. ``checkin``,
    contribute the template alone). Persisted to
    ``index.json::final.prompt_hashes`` so cross-cycle audits can join cycles
    by ``l1_generate_hash`` etc.
    """
    out: dict[str, str] = {}
    for name in list_optimizer_prompts():
        tpl = load_optimizer_prompt(name)
        blob = tpl.model_dump_json()
        if name in NODE_LAYOUTS:
            blob += resolve_node_layout(name).model_dump_json()
        out[name] = hashlib.sha256(blob.encode("utf-8")).hexdigest()[:16]
    return out


def combined_optimizer_prompt_hash() -> str:
    """One 12-hex hash over the whole optimizer meta-prompt set.

    Folds the per-prompt hashes from :func:`compute_optimizer_prompt_hashes`
    into a single deterministic digest. Recorded on ``campaign.json`` as
    ``optimizer_prompt_hash``; resume compares the stored value against a
    fresh recomputation and warns on drift. Not part of ``campaign_id``
    (campaign ids are random per ``new`` call) — it's a drift-detection
    property only.
    """
    per_prompt = compute_optimizer_prompt_hashes()
    blob = json.dumps(per_prompt, sort_keys=True)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()[:12]
