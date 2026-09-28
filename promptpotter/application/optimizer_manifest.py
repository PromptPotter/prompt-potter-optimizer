"""Which optimizer a campaign runs: the manifest ``optimization.optimizer`` names, with the
campaign's ``optimization.nodes`` overlay laid on — plus the bench's own check-in node beside it.

Resolved off the config wherever one is in hand, and bound per task (:func:`bind_optimizer`) for
the calls deep inside a round that hold none — beside the two other per-task bindings on those
calls, the determinism clamp and the outer's prompt overrides an L4 inner cell runs under."""

from __future__ import annotations

import contextvars
import copy
import functools
import hashlib
import json
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

from pydantic import Field

from promptpotter.application import optimizers
from promptpotter.application.campaign_config import DeterminismClamp
from promptpotter.config.paths import (
    checkin_assets_root,
    checkin_manifest_path,
    optimizer_manifest_path,
    optimizers_root,
)
from promptpotter.domain.l1_layout import (
    NODE_LAYOUTS,
    L1Layout,
    coerce_l1_layout,
    validate_l1_layout,
)
from promptpotter.domain.opt_search_point import PromptTemplate
from promptpotter.domain.pipeline_parsing import parse_pipeline_response
from promptpotter.domain.pipeline_schema import (
    MEMBER_KINDS,
    ManifestNodeOverlay,
    NodeKind,
    PipelineNode,
    PipelineSchema,
)
from promptpotter.domain.strict_model import StrictModel
from promptpotter.domain.validators import ValidatorOutcome
from promptpotter.infrastructure.store.io import read_json, read_yaml
from promptpotter.shared.hashing import shapes_optimizer_prompt

if TYPE_CHECKING:
    from promptpotter.application.campaign_config import OptimizationConfig
    from promptpotter.application.optimizers.nodes import OptimizerRuntime

shapes_optimizer_prompt(__name__)

__all__ = [
    "KnobRow",
    "NodeKnobs",
    "OptimizerKnobsResponse",
    "SelectedOptimizer",
    "bind_optimizer",
    "bound_optimizer",
    "checkin_manifest",
    "get_optimizer_config_overrides",
    "llm_node_config",
    "llm_node_document",
    "optimizer_knobs",
    "resolve_layout_override",
    "resolve_node_layout",
    "resolve_node_override",
    "resolve_optimizer",
    "resolved_overrides",
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

    def node(self, name: str) -> PipelineNode:
        node = self.schema.get_node(name)
        if node is None:
            raise KeyError(f"optimizer {self.name!r} declares no node {name!r}")
        return node

    def node_config(self, name: str) -> dict[str, Any]:
        return dict(self.node(name).current_config)

    def file_config(self, name: str) -> dict[str, Any]:
        self.node(name)
        return dict(self.document["nodes"][name].get("config") or {})

    def knobs(self, name: str) -> StrictModel:
        return _knobs(name, self.node(name).wire_type, self.node_config(name))

    def declared_knobs(self, name: str) -> StrictModel:
        return _knobs(name, self.node(name).wire_type, self.file_config(name))

    def readout(self, node: str, knob: str) -> Any:
        """One knob's effective value for a SURFACE to display, ``None`` where this manifest
        declares no such node: an optimizer without a patience has none to show."""
        if self.schema.get_node(node) is None:
            return None
        return getattr(self.knobs(node), knob)

    @property
    def runtime(self) -> OptimizerRuntime:
        return optimizers.runtime(self.name)

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

    def prompt_body(self, node: str, *, base: bool) -> dict[str, Any] | None:
        """``base`` reads the family the FILE names, which is what an L4 mutation is laid onto;
        otherwise the family this campaign's overlay runs."""
        return _prompt_body(
            self.document, self.file_config(node) if base else self.node_config(node)
        )

    @functools.cached_property
    def node_digests(self) -> dict[str, str]:
        """Per llm node: the prompt it runs, its output schema and its resolved config — what
        decides the node's output, off the manifest alone."""
        out: dict[str, str] = {}
        for node in self.llm_nodes:
            cfg = self.node_config(node)
            schema_key = _resolved_key(cfg.get("schema_family"), cfg.get("schema_version"))
            out[node] = _digest(
                [
                    self.prompt_body(node, base=False),
                    self.resolved_schemas.get(schema_key) if schema_key else None,
                    cfg,
                ]
            )
        return out

    @functools.cached_property
    def digest(self) -> str:
        """The manifest's identity: its name, version and every node's resolved config and
        prompt. An audit join key — never part of a campaign id."""
        return _digest([self.name, self.version, self.node_digests, self.schema.pipelines])[:12]


def _resolved_key(family: object, version: object) -> str | None:
    if not family:
        return None
    return f"{family}/{version}" if version is not None else str(family)


def _prompt_body(document: Mapping[str, Any], config: Mapping[str, Any]) -> dict[str, Any] | None:
    key = _resolved_key(config.get("prompt_family"), config.get("prompt_version"))
    body = (document.get("resolved_prompts") or {}).get(key) if key else None
    return dict(body) if isinstance(body, dict) else None


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
    # layout is L2's in-campaign surface (`PotterState.memory.l1_layout`) and nothing here applies
    # to it — reaching this with that node means a caller believes in an L4 lever that has no
    # code path, and silence would let the belief survive.
    if spec.editor != "l4":
        raise ValueError(
            f"resolve_layout_override({node!r}): this node's layout is edited by {spec.editor!r}, "
            "not L4. Only `editor='l4'` nodes resolve a layout through the per-node override "
            "channel; l1_generate's rides PotterState.memory.l1_layout instead."
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
