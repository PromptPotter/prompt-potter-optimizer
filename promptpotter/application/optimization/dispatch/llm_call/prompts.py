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

logger = logging.getLogger(__name__)

__all__ = [
    "OPTIMIZER_PIPELINE_PATH",
    "combined_optimizer_prompt_hash",
    "compute_optimizer_prompt_hashes",
    "get_optimizer_config_overrides",
    "get_optimizer_schema",
    "list_optimizer_prompts",
    "load_optimizer_prompt",
    "load_optimizer_set_overrides",
    "resolve_node_layout",
    "resolve_node_schema_field_names",
    "set_optimizer_config_overrides",
    "set_optimizer_prompt_overrides",
]

OPTIMIZER_PIPELINE_PATH = REPO_ROOT / "datasets" / "_optimizer" / "pipeline.json"

# Per-cycle override of the optimizer meta-prompts, keyed by optimizer node
# (`l1_generate` / `l1_critique` / `l2_context` / `l3_plan`) → a partial
# `PromptTemplate`-field dict, merged onto the loaded prompt by
# `_apply_prompt_override`. ONE channel, two callers — both task-isolated:
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
    (`runner/inner_recursion.py`) binds the outer's per-node mutations."""
    _OPTIMIZER_PROMPT_OVERRIDES.set(overrides or None)


# Per-run override of optimizer NODE-CONFIG tunables (a flat ``{param: value}`` map,
# e.g. ``{"temperature": 0.0}``), applied to EVERY optimizer node call. Distinct from
# the prompt-field override above: that swaps meta-prompt TEXT; this clamps the sampling
# knobs. Sole caller: the L4 inner-cycle runner, which binds ``{"temperature": 0.0}``
# inside the inner asyncio task so an inner campaign is a (near-)deterministic fitness
# function of its meta-prompt — killing the run-to-run swing that swamps the outer proxy
# — WITHOUT touching the outer optimizer (its own task keeps the file default). Applied
# LAST in ``llm_call`` (a hard clamp), so it also neutralizes ``l1_generate``'s per-call
# creativity temperature, the dominant noise source. Task-isolated ContextVar; default
# ``None`` = no override (every normal, non-inner call).
_OPTIMIZER_CONFIG_OVERRIDES: contextvars.ContextVar[dict[str, Any] | None] = contextvars.ContextVar(
    "optimizer_config_overrides", default=None
)


def set_optimizer_config_overrides(overrides: dict[str, Any] | None) -> None:
    """Bind per-run optimizer node-config (tunable) overrides for this task's context.

    A flat ``{param: value}`` map (e.g. ``{"temperature": 0.0}``) clamped onto EVERY
    optimizer node call by :func:`..call.llm_call`, applied last so it beats both the
    node's file config and any per-call override (``l1_generate``'s creativity temp).
    Sole caller: the L4 inner-cycle runner (`runner/inner_recursion.py`), inside the
    inner task, so an inner campaign runs its optimizer deterministically without
    affecting the outer cycle's own task."""
    _OPTIMIZER_CONFIG_OVERRIDES.set(overrides or None)


def get_optimizer_config_overrides() -> dict[str, Any] | None:
    """The per-run optimizer node-config overrides bound for this task, or ``None``."""
    return _OPTIMIZER_CONFIG_OVERRIDES.get()


def load_optimizer_set_overrides(opt_set: str) -> dict[str, dict[str, Any]]:
    """Load a named optimizer prompt-set's per-node field overrides.

    The L4 outer cycle selects a specialized meta-prompt set via
    ``OptimizationConfig.optimizer_set`` (e.g. ``"meta"`` →
    ``datasets/_optimizer_meta/prompts.json``). The file is a flat
    ``{node: {field: text}}`` map of only the fields that set rewrites; it rides
    the SAME per-node override channel as the inner-cycle mutations
    (:func:`set_optimizer_prompt_overrides` → :func:`_apply_prompt_override`), so
    every injection slot the set does not name (the ``pipeline_param_catalogue``
    in ``problem_description``, the evidence panels, …) stays intact from
    ``datasets/_optimizer/``. Empty ``opt_set`` or a missing file → ``{}``.

    Non-dict top-level entries (e.g. a ``_doc`` note) are dropped — only real
    per-node field maps are returned."""
    if not opt_set:
        return {}
    path = REPO_ROOT / "datasets" / f"_optimizer_{opt_set}" / "prompts.json"
    if not path.exists():
        logger.warning(
            "optimizer_set %r: no prompts at %s — falling back to the default set", opt_set, path
        )
        return {}
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


_LANGFUSE_PREFIX = "optimizer_"
_LANGFUSE_CACHE_TTL = 300  # seconds


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
def _load_local(name: str) -> PromptTemplate:
    body = _resolved_prompt_for_node(name)
    if body is None:
        raise KeyError(
            f"Optimizer prompt '{name}' not found in resolved_prompts registry "
            f"(check nodes.{name}.config.prompt_family/version)."
        )
    return PromptTemplate(**body)


@functools.cache
def _prompt_langfuse() -> Any:
    """Process-wide LangfuseLogger for optimizer prompt fetch/push (separate from trace logger)."""
    from promptpotter.infrastructure.tracing import LangfuseLogger

    return LangfuseLogger()


def _try_langfuse(name: str) -> PromptTemplate | None:
    """Fetch prompt from Langfuse 'production' label (None on any failure)."""
    try:
        from promptpotter.config.settings import settings

        if not settings.LANGFUSE_PROMPTS_ENABLED:
            return None

        lf = _prompt_langfuse()
        if not lf.enabled or not lf.client:
            return None

        prompt_client = lf.client.get_prompt(
            name=f"{_LANGFUSE_PREFIX}{name}",
            label="production",
            cache_ttl_seconds=_LANGFUSE_CACHE_TTL,
        )
        config = getattr(prompt_client, "config", None)
        if not config or not isinstance(config, dict):
            return None

        return PromptTemplate(**config)
    except Exception:
        logger.debug("Langfuse prompt fetch failed for %s", name, exc_info=True)
        return None


def load_optimizer_prompt(name: str) -> PromptTemplate:
    """Load optimizer prompt: Langfuse production → manifest registry fallback.

    Every load runs through :func:`dispatch_hub.validate_template`, so a
    template that references a slot not in
    :data:`dispatch_hub.INJECTIONS` (and not in the per-template extras list)
    raises at load time rather than silently rendering empty.
    """
    from promptpotter.application.optimization.dispatch.hub import validate_template

    lf_prompt = _try_langfuse(name)
    template = lf_prompt or _load_local(name)
    template = _apply_prompt_override(name, template)
    validate_template(name, template)
    return template


def _apply_prompt_override(name: str, template: PromptTemplate) -> PromptTemplate:
    """Merge any per-run override fields (L4 inner cycle) onto *template*.

    The outer L1 mutates the six decomposition fields per inner node; only keys
    that are real ``PromptTemplate`` fields are merged (the model is ``extra``-
    strict). No override bound → the template passes through unchanged. The
    merged result still runs through ``validate_template``, so an override that
    drops a mandatory injection slot fails loud (and the inner cycle's round loop
    catches it — a bad mutation scores poorly, it doesn't break the run)."""
    overrides = _OPTIMIZER_PROMPT_OVERRIDES.get()
    if not overrides:
        return template
    node_override = overrides.get(name)
    if not isinstance(node_override, dict) or not node_override:
        return template
    fields = {k: v for k, v in node_override.items() if k in PromptTemplate.model_fields}
    return template.model_copy(update=fields) if fields else template


def _node_override_dict(node: str, key: str) -> dict[str, Any]:
    """The outer L4 cycle's ``overrides[node][key]`` object, or ``{}`` — the one read every
    structural (not-a-``PromptTemplate``-field) per-node lever shares."""
    node_override = (_OPTIMIZER_PROMPT_OVERRIDES.get() or {}).get(node)
    raw = node_override.get(key) if isinstance(node_override, dict) else None
    return raw if isinstance(raw, dict) else {}


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
    non-L4 cycle) → the floor unchanged (``_apply_prompt_override``'s peer for the
    structural, not-a-``PromptTemplate``-field, half of a per-node edit)."""
    spec = NODE_LAYOUTS[node]
    raw = _node_override_dict(node, "layout")
    if not raw:
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


def resolve_node_schema_field_names(node: str) -> dict[str, str]:
    """The outer L4 cycle's field-NAME edits for *node*'s output schema — ``{field: wire_name}``.

    The first and strongest lever (``docs/concepts/structured-output.md`` § 1): the model holds
    strong priors about what belongs under ``rationale`` vs ``evidence`` vs ``scratch``. Unlike a
    ``description``, a name IS the wire contract — so a rename is a **presentation transform
    only**: the emitted schema advertises ``wire_name``, the response validates through a
    ``validation_alias``, and every downstream reader still sees the model's own field name.
    Nothing but the LLM observes the change.

    **Locked by default.** ``build_l1_response_schema`` grafts the key only when the campaign sets
    ``optimization.schema_field_rename``, so the LLM cannot emit a key that does not exist —
    structural, not policed. Rides the same per-node override object as the prose, ``layout``,
    and ``output_schema_descriptions`` edits.

    Dropped rather than raised on: non-strings, blanks, non-identifiers, no-op self-renames, and
    duplicate targets (two fields claiming one wire name is an unresolvable response). Collisions
    with a field that is NOT being renamed are rejected at the apply site, which knows the field
    set. A bad L4 mutation must score poorly, never break the run.
    """
    raw = _node_override_dict(node, "output_schema_field_names")
    clean: dict[str, str] = {}
    for field, wire in raw.items():
        if not isinstance(field, str) or not isinstance(wire, str):
            continue
        wire = wire.strip()
        if not wire.isidentifier() or wire == field:
            continue
        clean[field] = wire
    targets = list(clean.values())
    return {f: w for f, w in clean.items() if targets.count(w) == 1}


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
