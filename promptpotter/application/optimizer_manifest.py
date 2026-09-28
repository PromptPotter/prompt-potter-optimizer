"""Which optimizer a campaign runs: the manifest ``optimization.optimizer`` names, with the
campaign's ``optimization.nodes`` overlay laid on — plus the bench's own check-in node beside it.

Resolved off the config wherever one is in hand, and bound per task (:func:`bind_optimizer`) for
the calls deep inside a round that hold none — beside the other per-task bindings on those calls:
the determinism clamp, the outer's prompt overrides an L4 inner cell runs under, and the manifest
an L4 outer's inner cells select (:func:`bind_inner_optimizer`)."""

from __future__ import annotations

import contextvars
import copy
import functools
import hashlib
import json
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any, cast

from pydantic import Field

from promptpotter.application import optimizers
from promptpotter.application.campaign_config import DeterminismClamp, OptimizationConfig
from promptpotter.config.paths import (
    checkin_assets_root,
    checkin_manifest_path,
    optimizer_manifest_path,
    optimizers_root,
)
from promptpotter.domain.opt_search_point import OptimizerPromptTemplate, PromptTemplate
from promptpotter.domain.pipeline_parsing import parse_pipeline_response
from promptpotter.domain.pipeline_schema import (
    MEMBER_KINDS,
    ManifestNodeOverlay,
    NodeKind,
    PipelineNode,
    PipelineSchema,
)
from promptpotter.domain.strict_model import StrictModel
from promptpotter.infrastructure.store.io import read_json, read_yaml
from promptpotter.shared.hashing import shapes_optimizer_prompt

if TYPE_CHECKING:
    from promptpotter.application.optimizers.nodes import (
        OptimizerPacing,
        OptimizerRuntime,
        Selector,
    )

shapes_optimizer_prompt(__name__)

__all__ = [
    "KnobRow",
    "NodeKnobs",
    "OptimizerEntry",
    "OptimizerKnobsResponse",
    "OptimizerRoster",
    "SelectedOptimizer",
    "bind_inner_optimizer",
    "bind_optimizer",
    "bound_inner_optimizer",
    "bound_optimizer",
    "checkin_manifest",
    "declared_node_override",
    "get_optimizer_config_overrides",
    "llm_node_config",
    "llm_node_document",
    "optimizer_knobs",
    "optimizer_prompt",
    "optimizer_roster",
    "resolve_node_override",
    "resolve_optimizer",
    "resolved_overrides",
    "running_prompt",
    "select_optimizer",
    "set_determinism_clamp",
    "set_optimizer_prompt_overrides",
]


def _stamp(path: Path) -> int:
    try:
        return path.stat().st_mtime_ns
    except OSError:
        # A vanished file is its reader's error to raise; the stamp only keys the cache.
        return -1


@functools.lru_cache(maxsize=8)
def _read_manifest(path: Path, _mtime_ns: int) -> dict[str, Any]:
    # Keyed on path AND mtime: an operator's edit or tenant shadow takes effect without a restart.
    if not path.is_file():
        raise FileNotFoundError(f"no optimizer manifest at {path}")
    manifest: dict[str, Any] = read_yaml(path)
    return manifest


@functools.lru_cache(maxsize=8)
def _read_schemas(path: Path) -> dict[str, Any]:
    schemas: dict[str, Any] = read_json(path)
    return schemas


def _parse(document: Mapping[str, Any], schemas: Mapping[str, Any]) -> PipelineSchema:
    return parse_pipeline_response({**document, "resolved_schemas": dict(schemas)})


def _digest(value: object) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, default=str).encode()).hexdigest()[:16]


@dataclass(frozen=True)
class SelectedOptimizer:
    """One campaign's optimizer. ``document`` is the manifest as authored; ``schema`` is it parsed
    with the overlay applied, which is the configuration the run executes."""

    name: str
    document: Mapping[str, Any]
    overlay: Mapping[str, Mapping[str, Any]]
    schema: PipelineSchema
    resolved_schemas: Mapping[str, Any]

    @property
    def version(self) -> str:
        return str(self.document.get("version") or "")

    @property
    def paper(self) -> str | None:
        """The citation a paper preset reproduces: its declared knob values are that paper's."""
        cited = self.document.get("paper")
        return str(cited) if cited else None

    def node(self, name: str) -> PipelineNode:
        node = self.schema.get_node(name)
        if node is None:
            raise KeyError(f"optimizer {self.name!r} declares no node {name!r}")
        return node

    @property
    def stamps_theta(self) -> bool:
        """This optimizer's own declaration (``Selector.stamps_theta``) — read before a
        ``RoundPlan`` exists, for round 0's origin document, which stamps this fact for the same
        reason the scoreboard reads it: a selector that never fits θ makes round 0's arm no
        exception."""
        walk = self.schema.pipelines["default"]
        name = next(n for n in walk if self.node(n).wire_type is NodeKind.SELECTOR)
        return cast("Selector", optimizers.member(name)).stamps_theta

    def node_config(self, name: str) -> dict[str, Any]:
        return dict(self.node(name).current_config)

    def file_config(self, name: str) -> dict[str, Any]:
        self.node(name)
        return dict(self.document["nodes"][name].get("config") or {})

    def knobs(self, name: str) -> StrictModel:
        return _knobs(name, self.node(name).wire_type, self.node_config(name))

    def declared_knobs(self, name: str) -> StrictModel:
        return _knobs(name, self.node(name).wire_type, self.file_config(name))

    @property
    def runtime(self) -> OptimizerRuntime:
        return optimizers.runtime(self.name)

    @property
    def pacing(self) -> OptimizerPacing:
        return self.runtime.pacing(self)

    def prompt_hashes(self) -> dict[str, str]:
        return self.runtime.prompt_hashes(self)

    @property
    def member_nodes(self) -> tuple[str, ...]:
        """Every declared node an implementation answers, in declaration order."""
        table = optimizers.registered()
        return tuple(n.name for n in self.schema.config_nodes if n.name in table)

    @property
    def llm_nodes(self) -> tuple[str, ...]:
        return tuple(n.name for n in self.schema.config_nodes if n.wire_type is NodeKind.LLM)

    @property
    def proposer(self) -> str:
        """The first llm node `default` walks — the node whose model is "the optimizer's model"."""
        return next(n.name for n in self.schema.nodes if n.wire_type is NodeKind.LLM)

    def model(self, node: str | None = None) -> str:
        return str(self.node_config(node or self.proposer)["model"])

    def _node_digest(self, node: str, prompt_fields: Mapping[str, Any]) -> str:
        cfg = self.node_config(node)
        body = _prompt_body(self.document, cfg)
        schema_key = _resolved_key(cfg.get("schema_family"), cfg.get("schema_version"))
        return _digest(
            [
                {**body, **prompt_fields} if body is not None and prompt_fields else body,
                self.resolved_schemas.get(schema_key) if schema_key else None,
                cfg,
            ]
        )

    @functools.cached_property
    def node_digests(self) -> dict[str, str]:
        """Per llm node: the prompt it runs, its output schema and its resolved config — what
        decides the node's output, off the manifest alone."""
        return {node: self._node_digest(node, {}) for node in self.llm_nodes}

    def running_digests(self) -> dict[str, str]:
        """:attr:`node_digests` under the prompt-field edit an L4 inner cell runs its nodes with."""
        return {
            node: self._node_digest(node, resolve_node_override(node).prompt_fields)
            for node in self.llm_nodes
        }


def _resolved_key(family: object, version: object) -> str | None:
    if not family:
        return None
    return f"{family}/{version}" if version is not None else str(family)


def _prompt_body(document: Mapping[str, Any], config: Mapping[str, Any]) -> dict[str, Any] | None:
    key = _resolved_key(config.get("prompt_family"), config.get("prompt_version"))
    body = (document.get("resolved_prompts") or {}).get(key) if key else None
    return dict(body) if isinstance(body, dict) else None


def optimizer_prompt(
    node: str, config: Mapping[str, Any], document: Mapping[str, Any]
) -> OptimizerPromptTemplate:
    body = _prompt_body(document, config)
    if body is None:
        raise KeyError(
            f"Optimizer prompt for node {node!r} not found in resolved_prompts "
            f"(check nodes.{node}.config.prompt_family/version)."
        )
    return OptimizerPromptTemplate(**body)


def running_prompt(
    node: str, config: Mapping[str, Any], document: Mapping[str, Any]
) -> OptimizerPromptTemplate:
    """The prompt *node* runs under *config*, with an L4 inner cell's prompt-field edit laid on."""
    template = optimizer_prompt(node, config, document)
    if fields := resolve_node_override(node).prompt_fields:
        template = template.model_copy(update=fields)
    return template


def _knobs(name: str, kind: NodeKind | None, config: Mapping[str, Any]) -> StrictModel:
    model = optimizers.member(name).knobs
    if kind in MEMBER_KINDS:
        return model.model_validate(dict(config))
    # An llm node's knobs sit beside its call config.
    return model.model_validate({k: v for k, v in config.items() if k in model.model_fields})


@functools.lru_cache(maxsize=32)
def _select(name: str, path: Path, stamp: int, overlay_json: str) -> SelectedOptimizer:
    document = _read_manifest(path, stamp)
    overlay: dict[str, dict[str, Any]] = json.loads(overlay_json)
    declared = document.get("nodes") or {}
    if unknown := sorted(set(overlay) - set(declared)):
        raise ValueError(
            f"optimization.nodes names {unknown}, which optimizer {name!r} does not declare "
            f"(it has {sorted(declared)})."
        )
    if taken := sorted(set(declared) & {n.name for n in checkin_manifest().schema.config_nodes}):
        raise ValueError(f"optimizer {name!r} declares {taken}, which the bench's check-in owns.")
    overlaid = copy.deepcopy(dict(document))
    for node, config in overlay.items():
        block = overlaid["nodes"][node]
        block["config"] = {**(block.get("config") or {}), **config}
    schemas = _read_schemas(optimizers_root() / name / "resolved_schemas.json")
    selected = SelectedOptimizer(
        name=name,
        document=document,
        overlay=overlay,
        schema=_parse(overlaid, schemas),
        resolved_schemas=schemas,
    )
    # Every member's knobs validate NOW, so a bad overlay stops the run before it spends.
    for declared_node in selected.schema.config_nodes:
        if declared_node.wire_type in MEMBER_KINDS or declared_node.name in optimizers.registered():
            selected.knobs(declared_node.name)
    return selected


def resolve_optimizer(name: str, nodes: Mapping[str, ManifestNodeOverlay]) -> SelectedOptimizer:
    """The one resolution every surface shares — a run, a draft edit, a served menu — so an
    overlay refused in one place is refused in all."""
    path = optimizer_manifest_path(name)
    overlay = {node: dict(o.config) for node, o in sorted(nodes.items())}
    return _select(name, path, _stamp(path), json.dumps(overlay, sort_keys=True))


def select_optimizer(opt: OptimizationConfig) -> SelectedOptimizer:
    return resolve_optimizer(opt.optimizer, opt.nodes)


class KnobRow(StrictModel):
    """One knob of one optimizer node, as a settings surface offers it."""

    key: str = Field(description="The knob's name under `nodes.{node}.config`")
    description: str = Field(description="What the knob does — its field description")
    type: str = Field(
        description="JSON Schema type the value takes: boolean | integer | number | string | object"
    )
    options: list[str] | None = Field(
        description="The closed set a string knob takes, in declared order; null when open"
    )
    nullable: bool = Field(description="Whether null is a legal value (an opt-in knob, off)")
    value: Any = Field(description="The value the manifest declares — a campaign's floor")


class NodeKnobs(StrictModel):
    node: str = Field(description="The manifest node that owns these knobs")
    kind: str = Field(description="The node's type: sampler | eliminator | selector | …")
    knobs: list[KnobRow] = Field(description="Its knobs, in declared order")


class OptimizerKnobsResponse(StrictModel):
    """Every knob an optimizer manifest's nodes take, served so a settings surface draws a
    control per knob and writes `optimization.nodes.{node}.config.{key}`."""

    optimizer: str = Field(description="The manifest name, as `optimization.optimizer` names it")
    version: str = Field(description="The manifest's own version")
    nodes: list[NodeKnobs] = Field(description="Every node taking a knob, in declared order")


def _knob_row(key: str, prop: Mapping[str, Any], defs: Mapping[str, Any], value: Any) -> KnobRow:
    options = [
        defs[ref["$ref"].rsplit("/", 1)[-1]] if "$ref" in ref else ref
        for ref in prop.get("anyOf") or [prop]
    ]
    nullable = any(o.get("type") == "null" for o in options)
    kind = next(o for o in options if o.get("type") != "null")
    enum = kind.get("enum") or ([kind["const"]] if "const" in kind else None)
    return KnobRow(
        key=key,
        description=str(prop.get("description") or ""),
        type=str(kind.get("type") or ("object" if "properties" in kind else "string")),
        options=[str(v) for v in enum] if enum else None,
        nullable=nullable,
        value=value,
    )


def optimizer_knobs(name: str) -> OptimizerKnobsResponse:
    selected = resolve_optimizer(name, {})
    nodes: list[NodeKnobs] = []
    for node in selected.member_nodes:
        schema = optimizers.member(node).knobs.model_json_schema()
        declared = selected.declared_knobs(node).model_dump(mode="json")
        rows = [
            _knob_row(key, prop, schema.get("$defs") or {}, declared[key])
            for key, prop in (schema.get("properties") or {}).items()
        ]
        if rows:
            wire_type = selected.node(node).wire_type
            nodes.append(NodeKnobs(node=node, kind=str(wire_type), knobs=rows))
    return OptimizerKnobsResponse(optimizer=selected.name, version=selected.version, nodes=nodes)


class OptimizerEntry(StrictModel):
    """One optimizer this install can run, as a picker offers it."""

    name: str = Field(description="The manifest name, as `optimization.optimizer` names it")
    version: str = Field(description="The manifest's own version")
    paper: str | None = Field(
        description="The citation a paper preset reproduces; its declared knob values are that "
        "paper's configuration. Null for an optimizer reproducing none"
    )


class OptimizerRoster(StrictModel):
    """The optimizers this install can run, the default first.

    One per runtime the registry holds: the menu `optimization.optimizer` accepts, never a list."""

    default: str = Field(description="What a campaign naming no `optimization.optimizer` runs")
    optimizers: list[OptimizerEntry] = Field(description="The roster, the default first")


def optimizer_roster() -> OptimizerRoster:
    default = OptimizationConfig.model_fields["optimizer"].default
    names = sorted(optimizers.runtimes(), key=lambda n: (n != default, n))
    entries = []
    for name in names:
        selected = resolve_optimizer(name, {})
        entries.append(OptimizerEntry(name=name, version=selected.version, paper=selected.paper))
    return OptimizerRoster(default=default, optimizers=entries)


@dataclass(frozen=True)
class CheckinManifest:
    document: Mapping[str, Any]
    schema: PipelineSchema


@functools.lru_cache(maxsize=2)
def _checkin_at(path: Path, stamp: int) -> CheckinManifest:
    document = _read_manifest(path, stamp)
    schemas = _read_schemas(checkin_assets_root() / "resolved_schemas.json")
    return CheckinManifest(document=document, schema=_parse(document, schemas))


def checkin_manifest() -> CheckinManifest:
    path = checkin_manifest_path()
    return _checkin_at(path, _stamp(path))


_BOUND: contextvars.ContextVar[SelectedOptimizer | None] = contextvars.ContextVar(
    "selected_optimizer", default=None
)


def bind_optimizer(selected: SelectedOptimizer) -> None:
    _BOUND.set(selected)


def bound_optimizer() -> SelectedOptimizer:
    selected = _BOUND.get()
    if selected is None:
        raise RuntimeError(
            "no optimizer is bound to this task — the run seam binds "
            "`select_optimizer(config.optimization)` before anything reads a node"
        )
    return selected


# The manifest an L4 outer's inner cells select, bound at `publish_inner_spawn_context`: what the
# outer's arms mutate, never the outer's own. `None` off the recursion.
_INNER: contextvars.ContextVar[SelectedOptimizer | None] = contextvars.ContextVar(
    "inner_optimizer", default=None
)


def bind_inner_optimizer(selected: SelectedOptimizer | None) -> None:
    _INNER.set(selected)


def bound_inner_optimizer() -> SelectedOptimizer | None:
    return _INNER.get()


def llm_node_document(node: str) -> tuple[PipelineNode, Mapping[str, Any], Mapping[str, Any]]:
    """The node, the config it runs and the document declaring it: the check-in's own when the
    check-in declares it, else the bound optimizer's. A manifest may not declare a check-in node."""
    checkin = checkin_manifest()
    if (held := checkin.schema.get_node(node)) is not None:
        return held, held.current_config, checkin.document
    selected = bound_optimizer()
    return selected.node(node), selected.node_config(node), selected.document


def llm_node_config(node: str) -> dict[str, Any]:
    return dict(llm_node_document(node)[1])


# The L4 inner-cycle runner binds the OUTER's per-node MUTATIONS here (inside the inner asyncio
# task), keyed by optimizer node → a partial `PromptTemplate`-field dict plus the
# `output_schema_field_names` / `model` levers `resolve_node_override` resolves, and any lever the
# node's own optimizer resolves (`OptimizerRuntime.override_levers`). A ContextVar — not a
# global — so every recursion level carries its own. `None` = no override.
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


@dataclass(frozen=True)
class ResolvedNodeOverride:
    """``model`` / ``provider`` are the SINGLE inner-optimizer model the outer carrier node set,
    returned for EVERY node so one choice fans across the whole inner optimizer at apply time."""

    prompt_fields: dict[str, Any]
    schema_field_names: dict[str, str]
    model: str | None
    provider: str | None


def declared_node_override(node: str) -> dict[str, Any]:
    """One node's override as the outer DECLARED it — what an optimizer reads its own levers off."""
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
    prompt_fields, names = _resolved_prompt_parts(declared_node_override(node))
    model, provider = _single_model(_OPTIMIZER_PROMPT_OVERRIDES.get() or {})
    return ResolvedNodeOverride(
        prompt_fields=prompt_fields, schema_field_names=names, model=model, provider=provider
    )


def resolved_overrides(overrides: dict[str, dict[str, Any]]) -> dict[str, Any]:
    """What a declaration RESOLVES to — the identity `inner_campaign_id` hashes. Everything the
    resolvers drop (a key no template carries, a rename that could not be applied, an optimizer's
    own lever landing back where it started) is dropped here too, so two declarations that render
    ONE prompt hash alike. Hashing the declaration instead bought two inner campaigns for one
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
        # Asked of every runtime: the outer cannot know which manifest the inner cell selects,
        # and each answers only for the nodes its own manifest declares.
        for runtime in optimizers.runtimes().values():
            resolved.update(runtime.override_levers(node, raw))
        if resolved:
            nodes[node] = resolved
    model, provider = _single_model(overrides)
    return {"nodes": nodes, "model": model, "provider": provider}
