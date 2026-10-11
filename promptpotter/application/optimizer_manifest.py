from __future__ import annotations

import contextvars
import functools
import json
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal, cast, get_args

from pydantic import Field

from promptpotter.application import optimizers
from promptpotter.application.campaign_config import DeterminismClamp, OptimizationConfig
from promptpotter.config.paths import (
    checkin_assets_root,
    checkin_manifest_path,
    optimizer_manifest_path,
)
from promptpotter.domain.campaign import Treatment
from promptpotter.domain.l4 import proxies
from promptpotter.domain.opt_search_point import OptimizerPromptTemplate, PromptTemplate
from promptpotter.domain.pipeline_parsing import parse_pipeline_response
from promptpotter.domain.pipeline_schema import (
    MEMBER_KINDS,
    ManifestNodeOverlay,
    NodeKind,
    PipelineNode,
    PipelineSchema,
)
from promptpotter.domain.search_point import PARAM_SCOPE_KEYS, WHO_ANSWERS_KEYS
from promptpotter.domain.strict_model import StrictModel
from promptpotter.infrastructure.store.io import read_json, read_yaml
from promptpotter.shared.errors import NotFoundError, PayloadInvalidError
from promptpotter.shared.hashing import shapes_optimizer_prompt, stable_hash
from promptpotter.shared.plugin_registry import BUILT_IN

if TYPE_CHECKING:
    from promptpotter.application.optimizers.nodes import (
        OptimizerRuntime,
        Sampler,
        Selector,
    )
    from promptpotter.domain.dashboard_rows import OptimizerLimit
    from promptpotter.domain.results import DisplayMetric

shapes_optimizer_prompt(__name__)

__all__ = [
    "KnobRow",
    "NodeKnobs",
    "OptimizerEntry",
    "OptimizerKnobsResponse",
    "OptimizerRoster",
    "SelectedOptimizer",
    "StartPrompt",
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


class StartPrompt(StrictModel):
    fields: dict[str, Any] = Field(description="The prompt body, by decomposition field")
    version: str = Field(description="The `prompt_version` the node's config names")
    versions_declared: int = Field(
        description="How many versions the manifest declares for the node's prompt family — a "
        "surface showing this one says so where there are more"
    )


@dataclass(frozen=True)
class SelectedOptimizer:
    """``document`` is the manifest as authored; ``schema`` is it parsed with the overlay applied."""

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
        cited = self.document.get("paper")
        return str(cited) if cited else None

    def node(self, name: str) -> PipelineNode:
        node = self.schema.get_node(name)
        if node is None:
            raise KeyError(f"optimizer {self.name!r} declares no node {name!r}")
        return node

    @property
    def elects_on(self) -> DisplayMetric:
        walk = self.schema.pipelines["default"]
        name = next(n for n in walk if self.node(n).kind is NodeKind.SELECTOR)
        return cast("Selector", optimizers.member(name)).elects_on

    @property
    def sampler(self) -> Sampler:
        walk = self.schema.pipelines["default"]
        name = next(n for n in walk if self.node(n).kind is NodeKind.SAMPLER)
        return cast("Sampler", optimizers.member(name))

    def round_cells(self, pool: int) -> int:
        return self.sampler.draws(self, pool)

    def round_cells_ceiling(self, pool: int) -> int:
        return self.runtime.round_cells_ceiling(self, pool)

    def node_config(self, name: str) -> dict[str, Any]:
        return dict(self.node(name).current_config)

    def call_config(self, name: str) -> dict[str, Any]:
        """Less the member's knobs: a knob's edit is the diff classifier's to scope, never a prompt hash's."""
        table = optimizers.registered()
        knobs = table[name].knobs.model_fields if name in table else {}
        return {k: v for k, v in self.node_config(name).items() if k not in knobs}

    def file_config(self, name: str) -> dict[str, Any]:
        self.node(name)
        return dict(self.document["nodes"][name].get("config") or {})

    def knobs(self, name: str) -> StrictModel:
        return _knobs(name, self.node(name).kind, self.node_config(name))

    def knobs_of[T: StrictModel](self, name: str, kind: type[T]) -> T:
        knobs = self.knobs(name)
        if not isinstance(knobs, kind):
            raise TypeError(f"{name}'s knobs are {type(knobs).__name__}, not {kind.__name__}")
        return knobs

    def declared_knobs(self, name: str) -> StrictModel:
        return _knobs(name, self.node(name).kind, self.file_config(name))

    @property
    def runtime(self) -> OptimizerRuntime:
        return optimizers.runtime(self.name)

    @property
    def arms_per_round(self) -> int:
        return self.runtime.arms(self)

    @property
    def limits(self) -> tuple[OptimizerLimit, ...]:
        return self.runtime.limits(self)

    @property
    def phases(self) -> frozenset[str]:
        stated = optimizers.llm_nodes()
        return frozenset(
            member.phase.phase
            for node in self.llm_nodes
            if (member := stated.get(node)) is not None and member.phase is not None
        )

    @property
    def steered_axes(self) -> dict[str, set[str]]:
        stated = optimizers.llm_nodes()
        out: dict[str, set[str]] = {}
        for node in self.llm_nodes:
            for target, keys in (stated[node].steers if node in stated else {}).items():
                out.setdefault(target, set()).update(keys)
        return out

    def outer_levers(self, node: str) -> dict[str, str]:
        member = optimizers.llm_nodes().get(node)
        return {} if member is None else dict(member.outer_levers)

    def start_prompts(self) -> dict[str, StartPrompt]:
        out: dict[str, StartPrompt] = {}
        declared = [str(key) for key in self.document.get("resolved_prompts") or {}]
        for node in self.schema.declared_nodes:
            config = node.current_config
            body = _prompt_body(self.document, config)
            if body is not None:
                family = str(config.get("prompt_family"))
                out[node.name] = StartPrompt(
                    fields=body,
                    version=str(config.get("prompt_version") or ""),
                    versions_declared=sum(k.partition("/")[0] == family for k in declared),
                )
        return out

    def prompt_hashes(self) -> dict[str, str]:
        """Under the bound L4 edit; a resume diverges at the first round whose banked stamp differs."""
        return {
            node: self._node_digest(node, declared_node_override(node)) for node in self.llm_nodes
        }

    @property
    def member_nodes(self) -> tuple[str, ...]:
        table = optimizers.registered()
        return tuple(n.name for n in self.schema.declared_nodes if n.name in table)

    @property
    def llm_nodes(self) -> tuple[str, ...]:
        return tuple(n.name for n in self.schema.declared_nodes if n.kind is NodeKind.LLM)

    @property
    def proposer(self) -> str:
        """The first llm node `default` walks — the node whose model is "the optimizer's model"."""
        return next(n.name for n in self.schema.nodes if n.kind is NodeKind.LLM)

    def model(self, node: str | None = None) -> str:
        return str(self.node_config(node or self.proposer)["model"])

    def _node_digest(self, node: str, declared: Mapping[str, Any]) -> str:
        cfg = self.call_config(node)
        body = _prompt_body(self.document, cfg)
        fields, renames = _resolved_prompt_parts(dict(declared))
        schema_key = _resolved_key(cfg.get("schema_family"), cfg.get("schema_version"))
        return stable_hash(
            [
                {**body, **fields} if body is not None and fields else body,
                renames,
                _resolved_levers(node, declared),
                self.resolved_schemas.get(schema_key) if schema_key else None,
                cfg,
            ]
        )

    def treatment(self) -> Treatment:
        """Never the L4 edit an inner cell runs under, which that cell's identity hashes beside it."""
        return Treatment(
            optimizer=self.name,
            version=self.version,
            prompt_hashes={node: self._node_digest(node, {}) for node in self.llm_nodes},
            knobs={node: self.knobs(node).model_dump(mode="json") for node in self.member_nodes},
            source=self.runtime.source_digest(proxies),
        )


def _resolved_levers(node: str, declared: Mapping[str, Any]) -> dict[str, Any]:
    member = optimizers.llm_nodes().get(node)
    return {} if member is None else member.resolved_levers(declared)


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
    template = optimizer_prompt(node, config, document)
    if fields := resolve_node_override(node).prompt_fields:
        template = template.model_copy(update=fields)
    return template


_CALL_CONFIG_KEYS: frozenset[str] = (
    WHO_ANSWERS_KEYS
    | PARAM_SCOPE_KEYS
    | {"seed", "prompt_family", "prompt_version", "schema_family", "schema_version"}
)


def _refuse_unknown_llm_keys(selected: SelectedOptimizer) -> None:
    table = optimizers.registered()
    for node in selected.llm_nodes:
        knobs = table[node].knobs.model_fields if node in table else {}
        if unknown := sorted(set(selected.node_config(node)) - _CALL_CONFIG_KEYS - set(knobs)):
            raise ValueError(
                f"optimizer {selected.name!r} node {node!r} names {unknown} in its config, which "
                f"is neither a call setting ({sorted(_CALL_CONFIG_KEYS)}) nor one of its knobs "
                f"({sorted(knobs)})."
            )


def _knobs(name: str, kind: NodeKind | None, config: Mapping[str, Any]) -> StrictModel:
    model = optimizers.member(name).knobs
    if kind in MEMBER_KINDS:
        return model.model_validate(dict(config))
    return model.model_validate({k: v for k, v in config.items() if k in model.model_fields})


@functools.lru_cache(maxsize=32)
def _select(
    name: str, shipped: Path, path: Path, stamp: int, overlay_json: str
) -> SelectedOptimizer:
    document = _read_manifest(path, stamp)
    overlay: dict[str, dict[str, Any]] = json.loads(overlay_json)
    declared = document.get("nodes") or {}
    if unknown := sorted(set(overlay) - set(declared)):
        raise ValueError(
            f"optimization.nodes names {unknown}, which optimizer {name!r} does not declare "
            f"(it has {sorted(declared)})."
        )
    if taken := sorted(set(declared) & {n.name for n in checkin_manifest().schema.declared_nodes}):
        raise ValueError(f"optimizer {name!r} declares {taken}, which the bench's check-in owns.")
    overlaid = {
        **document,
        "nodes": {
            node: {**block, "config": {**(block.get("config") or {}), **overlay[node]}}
            if node in overlay
            else block
            for node, block in declared.items()
        },
    }
    schemas = _read_schemas(shipped / "resolved_schemas.json")
    selected = SelectedOptimizer(
        name=name,
        document=document,
        overlay=overlay,
        schema=_parse(overlaid, schemas),
        resolved_schemas=schemas,
    )
    # Every member's knobs validate NOW, so a bad overlay stops the run before it spends.
    for declared_node in selected.schema.declared_nodes:
        if declared_node.kind in MEMBER_KINDS or declared_node.name in optimizers.registered():
            selected.knobs(declared_node.name)
    _refuse_unknown_llm_keys(selected)
    return selected


def resolve_optimizer(name: str, nodes: Mapping[str, ManifestNodeOverlay]) -> SelectedOptimizer:
    try:
        shipped = optimizers.runtime(name).manifest_dir
    except KeyError as exc:
        raise NotFoundError(f"No optimizer named {name!r}", code="optimizer_unknown") from exc
    path = optimizer_manifest_path(name, shipped)
    overlay = {node: dict(o.config) for node, o in sorted(nodes.items())}
    try:
        return _select(name, shipped, path, _stamp(path), json.dumps(overlay, sort_keys=True))
    except ValueError as exc:
        raise PayloadInvalidError(
            f"optimization.nodes refused by optimizer {name!r}: {exc}", code="optimizer_overlay"
        ) from exc


def select_optimizer(opt: OptimizationConfig) -> SelectedOptimizer:
    return resolve_optimizer(opt.optimizer, opt.nodes)


KnobType = Literal["boolean", "integer", "number", "string", "array", "object"]

_KNOB_TYPES: tuple[KnobType, ...] = get_args(KnobType)


class KnobRow(StrictModel):
    """One knob of one optimizer node, as a settings surface offers it."""

    key: str = Field(description="The knob's name under `nodes.{node}.config`")
    description: str = Field(description="What the knob does — its field description")
    type: KnobType = Field(description="JSON Schema type the value takes")
    options: list[str] | None = Field(
        description="The closed set a string knob takes, in declared order; null when open"
    )
    nullable: bool = Field(description="Whether null is a legal value (an opt-in knob, off)")
    minimum: float | None = Field(description="The least legal value, itself legal; null when none")
    exclusive_minimum: float | None = Field(
        description="A bound every legal value lies strictly above; null when none"
    )
    maximum: float | None = Field(
        description="The greatest legal value, itself legal; null when none"
    )
    exclusive_maximum: float | None = Field(
        description="A bound every legal value lies strictly below; null when none"
    )
    value: Any = Field(description="The value the manifest declares — a campaign's floor")


class NodeKnobs(StrictModel):
    node: str = Field(description="The manifest node that owns these knobs")
    kind: str = Field(description="The node's type: sampler | eliminator | selector | …")
    knobs: list[KnobRow] = Field(description="Its knobs, in declared order")


class OptimizerKnobsResponse(StrictModel):
    """Every knob an optimizer manifest's nodes take, each written to `optimization.nodes.{node}.config.{key}`."""

    optimizer: str = Field(description="The manifest name, as `optimization.optimizer` names it")
    version: str = Field(description="The manifest's own version")
    nodes: list[NodeKnobs] = Field(description="Every node taking a knob, in declared order")


def _knob_type(kind: Mapping[str, Any]) -> KnobType:
    declared = kind.get("type") or ("object" if "properties" in kind else "string")
    for known in _KNOB_TYPES:
        if known == declared:
            return known
    raise ValueError(f"knob type {declared!r} is none of {', '.join(_KNOB_TYPES)}")


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
        type=_knob_type(kind),
        options=[str(v) for v in enum] if enum else None,
        nullable=nullable,
        minimum=kind.get("minimum"),
        exclusive_minimum=kind.get("exclusiveMinimum"),
        maximum=kind.get("maximum"),
        exclusive_maximum=kind.get("exclusiveMaximum"),
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
            nodes.append(NodeKnobs(node=node, kind=str(selected.node(node).kind), knobs=rows))
    return OptimizerKnobsResponse(optimizer=selected.name, version=selected.version, nodes=nodes)


class OptimizerEntry(StrictModel):
    """One optimizer this install can run, as a picker offers it."""

    name: str = Field(description="The manifest name, as `optimization.optimizer` names it")
    version: str = Field(description="The manifest's own version")
    paper: str | None = Field(
        description="The citation a paper preset reproduces; its declared knob values are that "
        "paper's configuration. Null for an optimizer reproducing none"
    )
    origin: str | None = Field(
        description="The installed package that registered it, as `<distribution>: <entry point>`; "
        "null for one shipped with PromptPotter"
    )


class OptimizerRoster(StrictModel):
    """The optimizers this install can run, the default first: one per runtime the registry holds."""

    default: str = Field(description="What a campaign naming no `optimization.optimizer` runs")
    optimizers: list[OptimizerEntry] = Field(description="The roster, the default first")


def optimizer_roster() -> OptimizerRoster:
    default = OptimizationConfig.model_fields["optimizer"].default
    names = sorted(optimizers.runtimes(), key=lambda n: (n != default, n))
    origins = optimizers.runtime_origins()
    entries = []
    for name in names:
        selected = resolve_optimizer(name, {})
        origin = None if origins[name] == BUILT_IN else origins[name]
        entries.append(
            OptimizerEntry(name=name, version=selected.version, paper=selected.paper, origin=origin)
        )
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


# The manifest an L4 outer's inner cells select, never the outer's own; `None` off the recursion.
_INNER: contextvars.ContextVar[SelectedOptimizer | None] = contextvars.ContextVar(
    "inner_optimizer", default=None
)


def bind_inner_optimizer(selected: SelectedOptimizer | None) -> None:
    _INNER.set(selected)


def bound_inner_optimizer() -> SelectedOptimizer | None:
    return _INNER.get()


def llm_node_document(node: str) -> tuple[PipelineNode, Mapping[str, Any], Mapping[str, Any]]:
    checkin = checkin_manifest()
    if (held := checkin.schema.get_node(node)) is not None:
        return held, held.current_config, checkin.document
    selected = bound_optimizer()
    return selected.node(node), selected.node_config(node), selected.document


def llm_node_config(node: str) -> dict[str, Any]:
    return dict(llm_node_document(node)[1])


# The OUTER's per-node mutations, bound inside the inner task so each recursion level carries its own.
_OPTIMIZER_PROMPT_OVERRIDES: contextvars.ContextVar[dict[str, dict[str, Any]] | None] = (
    contextvars.ContextVar("optimizer_prompt_overrides", default=None)
)


def set_optimizer_prompt_overrides(overrides: dict[str, dict[str, Any]] | None) -> None:
    _OPTIMIZER_PROMPT_OVERRIDES.set(overrides or None)


_DETERMINISM: contextvars.ContextVar[DeterminismClamp | None] = contextvars.ContextVar(
    "determinism_clamp", default=None
)


def set_determinism_clamp(clamp: DeterminismClamp | None) -> None:
    _DETERMINISM.set(clamp)


def get_optimizer_config_overrides() -> dict[str, Any] | None:
    """An unpinned field is ABSENT, never a ``None`` that would erase the value it is laid over."""
    clamp = _DETERMINISM.get()
    if clamp is None:
        return None
    return clamp.model_dump(exclude_none=True) or None


@dataclass(frozen=True)
class ResolvedNodeOverride:
    """``model`` / ``provider`` are the ONE choice the outer's carrier node set, returned for every node."""

    prompt_fields: dict[str, Any]
    schema_field_names: dict[str, str]
    model: str | None
    provider: str | None


def declared_node_override(node: str) -> dict[str, Any]:
    raw = (_OPTIMIZER_PROMPT_OVERRIDES.get() or {}).get(node)
    return raw if isinstance(raw, dict) else {}


def _single_model(overrides: dict[str, Any]) -> tuple[str | None, str | None]:
    for nd in overrides.values():
        if isinstance(nd, dict) and isinstance(nd.get("model"), str) and nd["model"]:
            prov = nd.get("provider")
            return nd["model"], prov if isinstance(prov, str) and prov else None
    return None, None


def _resolved_prompt_parts(raw: dict[str, Any]) -> tuple[dict[str, Any], dict[str, str]]:
    """An unusable rename is DROPPED, never raised: a bad L4 mutation must score poorly, not break the run."""
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
    """The identity `inner_campaign_id` hashes: what the resolvers drop is dropped here, so declarations rendering ONE prompt hash alike."""
    nodes: dict[str, dict[str, Any]] = {}
    for node, raw in overrides.items():
        if not isinstance(raw, dict):
            continue
        prompt_fields, names = _resolved_prompt_parts(raw)
        resolved: dict[str, Any] = dict(prompt_fields)
        if names:
            resolved["output_schema_field_names"] = names
        resolved.update(_resolved_levers(node, raw))
        if resolved:
            nodes[node] = resolved
    model, provider = _single_model(overrides)
    return {"nodes": nodes, "model": model, "provider": provider}
