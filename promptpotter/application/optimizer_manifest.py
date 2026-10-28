from __future__ import annotations

import contextlib
import contextvars
import functools
import json
from collections.abc import Iterator, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal, assert_never, cast

from pydantic import Field

from promptpotter.application import optimizers
from promptpotter.application.optimizers.nodes import (
    INITIAL_POPULATION,
    OPERATOR_SEAT,
    RUN_INIT,
    CountKnob,
    Initializer,
    Reviewer,
    RoundWalk,
    source_digest,
)
from promptpotter.config.paths import (
    checkin_assets_root,
    checkin_manifest_path,
    optimizer_manifest_path,
)
from promptpotter.domain.campaign import DeterminismClamp
from promptpotter.domain.campaign_config import OptimizationConfig
from promptpotter.domain.l4 import proxies
from promptpotter.domain.opt_search_point import OptimizerPromptTemplate
from promptpotter.domain.optimizer_state import RoundOutput, StatePart, StateSlice
from promptpotter.domain.pipeline_parsing import parse_pipeline_response
from promptpotter.domain.pipeline_schema import (
    MEMBER_KINDS,
    ManifestNodeOverlay,
    NodeKind,
    PipelineNode,
    PipelineSchema,
)
from promptpotter.domain.pool import DemoPool, Pool
from promptpotter.domain.search_point import PARAM_SCOPE_KEYS, WHO_ANSWERS_KEYS
from promptpotter.domain.strict_model import FrozenModel, StrictModel
from promptpotter.domain.treatment import Treatment
from promptpotter.infrastructure.store.io import read_json, read_yaml
from promptpotter.shared.errors import NotFoundError, PayloadInvalidError
from promptpotter.shared.hashing import shapes_optimizer_prompt, stable_hash

if TYPE_CHECKING:
    from collections.abc import Sequence

    from promptpotter.application.bench.cycle import Cycle
    from promptpotter.application.optimizers.nodes import (
        Adapter,
        Controller,
        Eliminator,
        MintFacts,
        NodeMember,
        Proposer,
        ReviewReading,
        Sampler,
        Selector,
    )
    from promptpotter.domain.arm_reading import DisplayMetric
    from promptpotter.domain.campaign_config import ArmGateMode
    from promptpotter.domain.cycle_paths import CycleHop
    from promptpotter.domain.dashboard_rows import OptimizerLimit
    from promptpotter.domain.opt_search_point import OptSearchPoint
    from promptpotter.domain.pool import Access
    from promptpotter.domain.results import OptimizerFact, RoundResult
    from promptpotter.domain.round_audit import RoundAudit
    from promptpotter.domain.scoring import CellSheet
    from promptpotter.infrastructure.store.campaign_store.store import CampaignStore

shapes_optimizer_prompt(__name__)


def _stamp(path: Path) -> int:
    try:
        return path.stat().st_mtime_ns
    except OSError:
        # A vanished file is its reader's error to raise; the stamp only keys the cache.
        return -1


class ManifestNode(FrozenModel):
    type: NodeKind
    description: str | None = None
    # The backend-shaped menu of the node's searchable params (`pipeline_parsing.py`).
    optimizer: dict[str, Any] | None = None
    config: dict[str, Any] = Field(default_factory=dict)


class PipelineManifest(FrozenModel):
    """A `pipeline.yaml` typed at load — an optimizer's, or the bench's check-in."""

    name: str
    version: str
    paper: str | None = None
    available_models: list[str]
    nodes: dict[str, ManifestNode]
    pipelines: dict[str, list[str]]
    # Whether `initial_population` is a round of its own or heads round 1 (`RoundPlan.walk`).
    initial_population_walks: Literal["alone", "before_default"] | None = None
    resolved_prompts: dict[str, OptimizerPromptTemplate] = Field(default_factory=dict)

    def laid_over(self, overlay: Mapping[str, Mapping[str, Any]]) -> PipelineManifest:
        nodes = {
            name: node.model_copy(update={"config": {**node.config, **overlay[name]}})
            if name in overlay
            else node
            for name, node in self.nodes.items()
        }
        return self.model_copy(update={"nodes": nodes})


class ManifestSwap(FrozenModel):
    # Takes the swapped node's place in every walk, as manifest `of` declares and configures it.
    node: str
    of: str


class ManifestSwitch(FrozenModel):
    """A switch: the whole manifest ``extends`` names, each node ``swap`` names replaced by another manifest's node of its type."""

    name: str
    version: str
    paper: str | None = None
    extends: str
    swap: dict[str, ManifestSwap]


@functools.lru_cache(maxsize=16)
def _read_manifest(path: Path, _mtime_ns: int) -> PipelineManifest | ManifestSwitch:
    # Keyed on path AND mtime: an operator's edit or tenant shadow takes effect without a restart.
    if not path.is_file():
        raise FileNotFoundError(f"no optimizer manifest at {path}")
    raw = read_yaml(path)
    return (
        ManifestSwitch.model_validate(raw)
        if "extends" in raw
        else PipelineManifest.model_validate(raw)
    )


def authored_manifest(path: Path) -> PipelineManifest | ManifestSwitch:
    return _read_manifest(path, _stamp(path))


@functools.lru_cache(maxsize=8)
def _read_schemas(path: Path) -> dict[str, Any]:
    schemas: dict[str, Any] = read_json(path)
    return schemas


def _parse(manifest: PipelineManifest, schemas: Mapping[str, Any]) -> PipelineSchema:
    authored = manifest.model_dump(mode="json", exclude_unset=True)
    # Every prompt a manifest resolves is an `OptimizerPromptTemplate`, so its loci are that class's.
    fields = list(OptimizerPromptTemplate.RENDER_ORDER)
    prompts = {key: {"fields": fields} for key in manifest.resolved_prompts}
    return parse_pipeline_response(
        {**authored, "resolved_prompts": prompts, "resolved_schemas": dict(schemas)}
    )


@dataclass(frozen=True)
class RoundPlan:
    """What the bench walks, by role: built once, at load, so a walk the bench cannot run is refused before a mint."""

    initializers: tuple[Initializer, ...]
    measurement: str
    sampler: Sampler
    initial_population: tuple[Proposer, ...]
    # Its own round, read against C0 (LEVI's calibration); else the seeds race round 1's offspring (CAPO).
    seeds_alone: bool
    proposers: tuple[Proposer, ...]
    eliminator: Eliminator | None
    selector: Selector
    adapters: tuple[Adapter, ...]
    controller: Controller | None

    @property
    def round_walks(self) -> tuple[tuple[Proposer, ...], ...]:
        """The proposers of each walk that is a round of its own: seeds heading round 1 are its."""
        return (self.proposers, *((self.initial_population,) if self.seeds_alone else ()))

    def walk(self, population: Sequence[OptSearchPoint]) -> RoundWalk:
        """A round seeds only where no population stands: a resumed one restores its own."""
        if not self.initial_population or population:
            return RoundWalk("default", (), self.proposers)
        if self.seeds_alone:
            return RoundWalk(INITIAL_POPULATION, (), self.initial_population)
        return RoundWalk("default", self.initial_population, self.proposers)


def _walk_refused(name: str, why: str) -> PayloadInvalidError:
    return PayloadInvalidError(f"optimizer {name!r}: {why}", code="optimizer_roster_open")


def _round_plan(
    name: str, schema: PipelineSchema, seeds_walk: Literal["alone", "before_default"] | None
) -> RoundPlan:
    walk = schema.pipelines["default"]
    declared = {n.name: n.kind for n in schema.declared_nodes}
    if undeclared := [n for n in walk if n not in declared]:
        raise _walk_refused(name, f"`default` walks {undeclared}, which it does not declare.")
    kinds = [declared[n] for n in walk]
    if NodeKind.GATEWAY not in kinds:
        raise _walk_refused(name, f"`default` walks no {NodeKind.GATEWAY} node — it walks {walk}.")
    measured_at = kinds.index(NodeKind.GATEWAY)
    selected_at = next((i for i, kind in enumerate(kinds) if kind is NodeKind.SELECTOR), len(kinds))
    samplers: list[Sampler] = []
    proposers: list[Proposer] = []
    eliminators: list[Eliminator] = []
    selectors: list[Selector] = []
    adapters: list[Adapter] = []
    controllers: list[Controller] = []
    for at, (node, kind) in enumerate(zip(walk, kinds, strict=True)):
        if kind is NodeKind.GATEWAY:
            continue
        member = optimizers.NODE_MEMBERS.get(node)
        if kind is NodeKind.SAMPLER and at < measured_at:
            samplers.append(cast("Sampler", member))
        elif kind in (NodeKind.LLM, NodeKind.ALGORITHM) and at < measured_at:
            proposers.append(cast("Proposer", member))
        elif kind is NodeKind.ELIMINATOR and at < measured_at:
            eliminators.append(cast("Eliminator", member))
        elif kind is NodeKind.SELECTOR and at > measured_at:
            selectors.append(cast("Selector", member))
        elif kind is NodeKind.LLM and at > selected_at:
            adapters.append(cast("Adapter", member))
        elif kind is NodeKind.CONTROLLER and at > measured_at:
            controllers.append(cast("Controller", member))
        else:
            raise _walk_refused(
                name,
                f"`default` walks {kind} node {node!r} where the bench has no role for it "
                f"(the {NodeKind.GATEWAY} node is at {measured_at}).",
            )
    if len(samplers) != 1 or len(selectors) != 1 or not proposers or len(eliminators) > 1:
        raise _walk_refused(
            name,
            "`default` must walk one sampler, at least one proposer and at most one eliminator "
            f"before its measurement, and one selector after it — it walks {walk}.",
        )
    initial = [
        cast("Proposer", optimizers.NODE_MEMBERS.get(n))
        for n in schema.pipelines.get(INITIAL_POPULATION, ())
    ]
    for pipeline, walked in (("default", proposers), (INITIAL_POPULATION, initial)):
        if misplaced := [p.name for at, p in enumerate(walked) if p.opens is not (at == 0)]:
            raise _walk_refused(
                name,
                f"`{pipeline}` must walk a proposer that makes the round's arms first and only "
                f"ones editing them after — {misplaced} of {[p.name for p in walked]} are out of "
                "place.",
            )
    if bool(initial) != (seeds_walk is not None):
        raise _walk_refused(
            name,
            f"a manifest walking `{INITIAL_POPULATION}` declares `initial_population_walks`, and "
            f"only such a manifest does — it walks {[p.name for p in initial]} and declares "
            f"{seeds_walk!r}.",
        )
    initializers = [optimizers.NODE_MEMBERS.get(n) for n in schema.pipelines.get(RUN_INIT, ())]
    if strays := [m.name for m in initializers if not isinstance(m, Initializer)]:
        raise _walk_refused(
            name,
            f"`{RUN_INIT}` walks {strays}, which are no pool initializer: it seeds pools alone.",
        )
    return RoundPlan(
        initializers=tuple(cast("Initializer", m) for m in initializers),
        measurement=walk[measured_at],
        sampler=samplers[0],
        initial_population=tuple(initial),
        seeds_alone=seeds_walk == "alone",
        proposers=tuple(proposers),
        eliminator=eliminators[0] if eliminators else None,
        selector=selectors[0],
        adapters=tuple(adapters),
        controller=controllers[0] if controllers else None,
    )


@dataclass(frozen=True)
class SelectedOptimizer:
    """``manifest`` is as resolved, a switch laid onto what it extends; ``schema`` is it parsed with the overlay applied."""

    name: str
    manifest: PipelineManifest
    overlay: Mapping[str, Mapping[str, Any]]
    schema: PipelineSchema
    resolved_schemas: Mapping[str, Any]
    plan: RoundPlan
    # The members `plan` walks, closed under what each consults or fires, in declared order.
    member_nodes: tuple[str, ...]

    @property
    def version(self) -> str:
        return self.manifest.version

    @property
    def paper(self) -> str | None:
        return self.manifest.paper

    def _carried(self) -> frozenset[type[StatePart]]:
        return frozenset(
            kind
            for node in self.member_nodes
            for kind in optimizers.NODE_MEMBERS.get(node).reads
            | optimizers.NODE_MEMBERS.get(node).writes
        )

    @property
    def slices(self) -> tuple[type[StateSlice], ...]:
        """Every kind a member it names reads or writes, which its rounds bank."""
        return tuple(
            sorted((k for k in self._carried() if issubclass(k, StateSlice)), key=lambda k: k.kind)
        )

    @property
    def outputs(self) -> tuple[type[RoundOutput], ...]:
        return tuple(
            sorted((k for k in self._carried() if issubclass(k, RoundOutput)), key=lambda k: k.kind)
        )

    def node(self, name: str) -> PipelineNode:
        node = self.schema.get_node(name)
        if node is None:
            raise KeyError(f"optimizer {self.name!r} declares no node {name!r}")
        return node

    @property
    def elects_on(self) -> DisplayMetric:
        return self.plan.selector.elects_on

    @property
    def sampler(self) -> Sampler:
        return self.plan.sampler

    def round_cells(self, search: int) -> int:
        return self.sampler.round_cells(self.knobs(self.sampler.name), search)

    def round_cells_ceiling(self, search: int) -> int:
        return self.sampler.round_cells_ceiling(self, search)

    def node_config(self, name: str) -> dict[str, Any]:
        return dict(self.node(name).current_config)

    def call_config(self, name: str) -> dict[str, Any]:
        """Less the member's knobs: a knob's edit is the diff classifier's to scope, never a prompt hash's."""
        table = optimizers.NODE_MEMBERS.table
        knobs = table[name].knobs.model_fields if name in table else {}
        return {k: v for k, v in self.node_config(name).items() if k not in knobs}

    def file_config(self, name: str) -> dict[str, Any]:
        self.node(name)
        return dict(self.manifest.nodes[name].config)

    def knobs(self, name: str) -> StrictModel:
        return _knobs(name, self.node(name).kind, self.node_config(name))

    def knobs_of[T: StrictModel](self, name: str, kind: type[T]) -> T:
        knobs = self.knobs(name)
        if not isinstance(knobs, kind):
            raise TypeError(f"{name}'s knobs are {type(knobs).__name__}, not {kind.__name__}")
        return knobs

    def roster_one(self, member: type) -> str:
        """The load held every consulted edge to one roster member."""
        (name,) = (
            n for n in self.member_nodes if isinstance(optimizers.NODE_MEMBERS.get(n), member)
        )
        return name

    def knob_owner(self, member: NodeMember, kind: type[StrictModel]) -> str:
        """It where its own knobs are a *kind*, else the ONE consulted member whose knobs are."""
        if issubclass(member.knobs, kind):
            return member.name
        held = [
            n
            for n in (self.roster_one(c) for c in member.consults)
            if issubclass(optimizers.NODE_MEMBERS.get(n).knobs, kind)
        ]
        if len(held) != 1:
            raise TypeError(
                f"{member.name!r} consults {len(held)} {kind.__name__} knob sets, not one"
            )
        return held[0]

    def reached_knobs[T: StrictModel](self, member: NodeMember, kind: type[T]) -> T:
        return self.knobs_of(self.knob_owner(member, kind), kind)

    def access(self, member: NodeMember, pool: type[Pool]) -> Access:
        if (draw := member.draws.get(pool)) is None:
            raise KeyError(
                f"{member.name!r} draws from the {pool.kind!r} pool and does not declare it"
            )
        return draw.access(self, member)

    def draws(self, member: NodeMember) -> frozenset[type[Pool]]:
        """The pools *member* reads entries of: an ``off`` draw reads none."""
        return frozenset(pool for pool in member.draws if self.access(member, pool) != "off")

    @property
    def shot_cap(self) -> int:
        """The most shots an operator's arm may carry: the most any proposer of a round may draw, 0 where none draws the demo pool."""
        return max(
            (
                draw.count(self, proposer)
                for proposer in (*self.plan.initial_population, *self.plan.proposers)
                if isinstance(draw := proposer.draws.get(DemoPool), CountKnob)
            ),
            default=0,
        )

    def pool_refusals(self, *, mint: MintFacts | None) -> list[str]:
        """Why a pool seeds nothing its readers may draw: at load with no *mint*, before a mint with its facts."""
        refused: list[str] = []
        for initializer in self.plan.initializers:
            closed = any(
                self.access(member, pool) == "closed"
                for member in map(optimizers.NODE_MEMBERS.get, self.member_nodes)
                for pool in member.draws
                if pool in initializer.writes
            )
            if why := initializer.refusal(self, closed, mint):
                refused.append(why)
        return refused

    def declared_knobs(self, name: str) -> StrictModel:
        return _knobs(name, self.node(name).kind, self.file_config(name))

    @property
    def members(self) -> tuple[NodeMember, ...]:
        return tuple(map(optimizers.NODE_MEMBERS.get, self.member_nodes))

    def walk_arms(self, walk: Sequence[Proposer]) -> int:
        return sum(proposer.arms(self) for proposer in walk)

    @property
    def arms_per_round(self) -> int:
        return max(map(self.walk_arms, self.plan.round_walks))

    @property
    def limits(self) -> tuple[OptimizerLimit, ...]:
        return tuple(limit for member in self.members for limit in member.limits(self))

    def round_facts(self, round_result: RoundResult) -> list[OptimizerFact]:
        return [fact for member in self.members for fact in member.round_facts(self, round_result)]

    def refuse_origin(self, origin_results: CellSheet) -> None:
        for member in self.members:
            member.refuse_origin(origin_results)

    def round_packages(self, cycle: Cycle, rounds: list[RoundResult]) -> dict[int, dict[str, str]]:
        out: dict[int, dict[str, str]] = {}
        for member in self.members:
            for round_num, packages in member.round_packages(cycle, rounds).items():
                out.setdefault(round_num, {}).update(packages)
        return out

    async def rederive(
        self,
        campaign_store: CampaignStore,
        hop: CycleHop,
        cycle: Cycle,
        rounds: list[RoundResult],
        drifted: list[int],
    ) -> list[RoundResult]:
        for member in self.members:
            rounds = await member.rederive(campaign_store, hop, cycle, rounds, drifted)
        return rounds

    def review(
        self,
        rounds: list[RoundResult],
        audits: list[RoundAudit | None],
        *,
        context_object: list[str],
    ) -> ReviewReading | None:
        """``None`` where no member keeps a reading of its own; ``review.md`` then says N/A."""
        reviewer = next((m for m in self.members if isinstance(m, Reviewer)), None)
        if reviewer is None:
            return None
        return reviewer.review(rounds, audits, context_object=context_object)

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

    def prompt_hashes(self) -> dict[str, str]:
        """Under the bound L4 edit; a resume diverges at the first round whose banked stamp differs."""
        return {node: self._node_digest(node) for node in self.llm_nodes}

    @property
    def llm_nodes(self) -> tuple[str, ...]:
        return tuple(n.name for n in self.schema.declared_nodes if n.kind is NodeKind.LLM)

    @property
    def proposer(self) -> str:
        """The first llm node `default` walks — the node whose model is "the optimizer's model"."""
        return next(n.name for n in self.schema.nodes if n.kind is NodeKind.LLM)

    def model(self, node: str | None = None) -> str:
        return str(self.node_config(node or self.proposer)["model"])

    def _node_digest(self, node: str) -> str:
        cfg = self.call_config(node)
        override = resolve_node_override(node)
        if override.model:
            # Laid over as `bench/llm_call.py::_merged_config` sends it.
            cfg = {**cfg, "model": override.model}
            if override.provider:
                cfg["provider"] = override.provider
        body = prompt_body(self.manifest, cfg)
        fields = override.prompt_fields
        schema_key = _resolved_key(cfg.get("schema_family"), cfg.get("schema_version"))
        return stable_hash(
            [
                {**body, **fields} if body is not None and fields else body,
                override.schema_field_names,
                _resolved_levers(node, declared_node_override(node)),
                self.resolved_schemas.get(schema_key) if schema_key else None,
                cfg,
            ]
        )

    def treatment(self) -> Treatment:
        """Under the bound L4 edit too: two inner cells under two arms are two treatments."""
        return Treatment(
            optimizer=self.name,
            version=self.version,
            prompt_hashes=self.prompt_hashes(),
            # Less its prose and `version`, which `Treatment` carries on its own.
            manifest=stable_hash(
                [
                    self.manifest.laid_over(self.overlay).model_dump(
                        mode="json",
                        exclude={
                            "resolved_prompts": True,
                            "version": True,
                            "paper": True,
                            "nodes": {"__all__": {"description"}},
                        },
                    ),
                    {n: self.knobs(n).model_dump(mode="json") for n in self.member_nodes},
                ]
            ),
            # What an initializer seeds its pool from is prompt MATERIAL stored as data, which no module digest reads.
            source=stable_hash(
                [
                    source_digest(self.members, proxies),
                    {i.name: i.starting_content(self) for i in self.plan.initializers},
                ]
            ),
        )


def _resolved_levers(node: str, declared: Mapping[str, Any]) -> dict[str, Any]:
    member = optimizers.llm_nodes().get(node)
    return {} if member is None else member.resolved_levers(declared)


def _resolved_key(family: object, version: object) -> str | None:
    if not family:
        return None
    return f"{family}/{version}" if version is not None else str(family)


def _resolved_prompt(
    manifest: PipelineManifest, config: Mapping[str, Any]
) -> OptimizerPromptTemplate | None:
    key = _resolved_key(config.get("prompt_family"), config.get("prompt_version"))
    return manifest.resolved_prompts.get(key) if key else None


def prompt_body(manifest: PipelineManifest, config: Mapping[str, Any]) -> dict[str, Any] | None:
    """The fields as authored, never their defaults: a prompt hash reads only what the file says."""
    held = _resolved_prompt(manifest, config)
    return None if held is None else held.model_dump(exclude_unset=True)


def optimizer_prompt(
    node: str, config: Mapping[str, Any], manifest: PipelineManifest
) -> OptimizerPromptTemplate:
    template = _resolved_prompt(manifest, config)
    if template is None:
        raise KeyError(
            f"Optimizer prompt for node {node!r} not found in resolved_prompts "
            f"(check nodes.{node}.config.prompt_family/version)."
        )
    return template


def running_prompt(
    node: str, config: Mapping[str, Any], manifest: PipelineManifest
) -> OptimizerPromptTemplate:
    template = optimizer_prompt(node, config, manifest)
    if fields := resolve_node_override(node).prompt_fields:
        template = template.model_copy(update=fields)
    return template


_CALL_CONFIG_KEYS: frozenset[str] = (
    WHO_ANSWERS_KEYS
    | PARAM_SCOPE_KEYS
    | {"seed", "prompt_family", "prompt_version", "schema_family", "schema_version"}
)


def _refuse_unknown_llm_keys(selected: SelectedOptimizer) -> None:
    table = optimizers.NODE_MEMBERS.table
    for node in selected.llm_nodes:
        knobs = table[node].knobs.model_fields if node in table else {}
        if unknown := sorted(set(selected.node_config(node)) - _CALL_CONFIG_KEYS - set(knobs)):
            raise ValueError(
                f"optimizer {selected.name!r} node {node!r} names {unknown} in its config, which "
                f"is neither a call setting ({sorted(_CALL_CONFIG_KEYS)}) nor one of its knobs "
                f"({sorted(knobs)})."
            )
    for target, keys in selected.steered_axes.items():
        if unheld := sorted(keys - set(selected.node_config(target))):
            raise ValueError(
                f"optimizer {selected.name!r} steers {unheld} on node {target!r}, whose config "
                "holds no such key."
            )


def _knobs(name: str, kind: NodeKind | None, config: Mapping[str, Any]) -> StrictModel:
    model = optimizers.NODE_MEMBERS.get(name).knobs
    if kind in MEMBER_KINDS:
        return model.model_validate(dict(config))
    return model.model_validate({k: v for k, v in config.items() if k in model.model_fields})


def _located(name: str) -> tuple[Path, PipelineManifest | ManifestSwitch]:
    try:
        shipped = optimizers.MANIFESTS.get(name).directory
    except KeyError as exc:
        raise NotFoundError(f"No optimizer named {name!r}", code="optimizer_unknown") from exc
    return shipped, authored_manifest(optimizer_manifest_path(shipped))


def _stamps(name: str) -> tuple[int, ...]:
    """Of every file *name* resolves from, so an edit to what a switch extends re-resolves the switch."""
    _, authored = _located(name)
    read = [name]
    if isinstance(authored, ManifestSwitch):
        read += [authored.extends, *(swap.of for swap in authored.swap.values())]
    return tuple(_stamp(optimizer_manifest_path(_located(n)[0])) for n in read)


def _whole(name: str) -> tuple[PipelineManifest, dict[str, Any]]:
    shipped, authored = _located(name)
    if isinstance(authored, ManifestSwitch):
        raise _walk_refused(
            name, "a switch extends and swaps from whole manifests, never a switch."
        )
    return authored, _read_schemas(shipped / "resolved_schemas.json")


def _resolved(name: str) -> tuple[PipelineManifest, dict[str, Any]]:
    """*name*'s manifest and output schemas; a switch's swapped node brings its prompt and schema."""
    _, authored = _located(name)
    if not isinstance(authored, ManifestSwitch):
        return _whole(name)
    base, base_schemas = _whole(authored.extends)
    schemas, prompts = dict(base_schemas), dict(base.resolved_prompts)
    swapped: dict[str, tuple[str, ManifestNode]] = {}
    for old, swap in authored.swap.items():
        source, source_schemas = _whole(swap.of)
        node = source.nodes.get(swap.node)
        if old not in base.nodes or node is None or swap.node in base.nodes:
            raise _walk_refused(
                name,
                f"swaps {old!r} of {authored.extends!r} for {swap.node!r} of {swap.of!r}: the "
                "first must be a node of the one, the second of the other alone.",
            )
        if node.type is not base.nodes[old].type:
            raise _walk_refused(name, f"swaps {node.type} {swap.node!r} into {old!r}'s role.")
        cfg = node.config
        if key := _resolved_key(cfg.get("prompt_family"), cfg.get("prompt_version")):
            prompts[key] = source.resolved_prompts[key]
        if key := _resolved_key(cfg.get("schema_family"), cfg.get("schema_version")):
            schemas[key] = source_schemas[key]
        swapped[old] = (swap.node, node)
    nodes = dict(swapped.get(n, (n, node)) for n, node in base.nodes.items())
    pipelines = {
        walk: [swapped[n][0] if n in swapped else n for n in walked]
        for walk, walked in base.pipelines.items()
    }
    manifest = base.model_copy(
        update={
            "name": authored.name,
            "version": authored.version,
            "paper": authored.paper,
            "nodes": nodes,
            "pipelines": pipelines,
            "resolved_prompts": prompts,
        }
    )
    return manifest, schemas


@functools.lru_cache(maxsize=32)
def _select(
    name: str, _stamped: tuple[int, ...], overlay_json: str, gate: ArmGateMode
) -> SelectedOptimizer:
    manifest, schemas = _resolved(name)
    overlay: dict[str, dict[str, Any]] = json.loads(overlay_json)
    if unknown := sorted(set(overlay) - set(manifest.nodes)):
        raise ValueError(
            f"optimization.nodes names {unknown}, which optimizer {name!r} does not declare "
            f"(it has {sorted(manifest.nodes)})."
        )
    checkin = {n.name for n in checkin_manifest().schema.declared_nodes}
    if taken := sorted(set(manifest.nodes) & checkin):
        raise ValueError(f"optimizer {name!r} declares {taken}, which the bench's check-in owns.")
    schema = _parse(manifest.laid_over(overlay), schemas)
    selected = SelectedOptimizer(
        name=name,
        manifest=manifest,
        overlay=overlay,
        schema=schema,
        resolved_schemas=schemas,
        plan=_round_plan(name, schema, manifest.initial_population_walks),
        member_nodes=_roster(schema),
    )
    # At load, never mid-round: an edge no roster member closes would raise inside round 1.
    if open_ := _open_edges(selected, gate):
        raise PayloadInvalidError(
            f"optimizer {name!r} names a roster whose edges do not close: " + "; ".join(open_),
            code="optimizer_roster_open",
        )
    # Every member's knobs validate NOW, so a bad overlay stops the run before it spends.
    for declared_node in selected.schema.declared_nodes:
        if (
            declared_node.kind in MEMBER_KINDS
            or declared_node.name in optimizers.NODE_MEMBERS.table
        ):
            selected.knobs(declared_node.name)
    _refuse_unknown_llm_keys(selected)
    if refused := selected.pool_refusals(mint=None):
        raise ValueError("; ".join(refused))
    return selected


_WALKS = (RUN_INIT, INITIAL_POPULATION, "default")


def _edges_of(member: NodeMember) -> frozenset[type]:
    """The members it reaches: those it consults, and a controller's those it fires."""
    fires = cast("Controller", member).fires if member.kind is NodeKind.CONTROLLER else frozenset()
    return member.consults | fires


def _roster(schema: PipelineSchema) -> tuple[str, ...]:
    table = optimizers.NODE_MEMBERS.table
    declared = [n.name for n in schema.declared_nodes if n.name in table]
    reached = {n for walk in _WALKS for n in schema.pipelines.get(walk, ()) if n in table}
    frontier = list(reached)
    while frontier:
        for edge in _edges_of(table[frontier.pop()]):
            for n in declared:
                if n not in reached and isinstance(table[n], edge):
                    reached.add(n)
                    frontier.append(n)
    return tuple(n for n in declared if n in reached)


def _walk(selected: SelectedOptimizer, gate: ArmGateMode) -> tuple[NodeMember, ...]:
    """Every member in first-round order under *gate*, the bench's seat among them, then the members only a walked one reaches."""
    plan = selected.plan
    pipelines = selected.schema.pipelines
    default = [
        optimizers.NODE_MEMBERS.get(n) for n in pipelines["default"] if n != plan.measurement
    ]
    seeds: list[NodeMember] = list(plan.initial_population)
    match gate:
        case "off":
            pass
        case "review":
            default.insert(default.index(plan.proposers[-1]) + 1, OPERATOR_SEAT)
        case "author":
            opens = default.index(plan.proposers[0])
            default = [m for m in default if m not in plan.proposers]
            default.insert(opens, OPERATOR_SEAT)
            seeds = [OPERATOR_SEAT] if seeds else []
        case unreachable:
            assert_never(unreachable)
    named = {n for walk in _WALKS for n in pipelines.get(walk, ())}
    reached = [optimizers.NODE_MEMBERS.get(n) for n in selected.member_nodes if n not in named]
    return (*plan.initializers, *seeds, *default, *reached)


def _open_edges(selected: SelectedOptimizer, gate: ArmGateMode) -> list[str]:
    members = {node: optimizers.NODE_MEMBERS.get(node) for node in selected.member_nodes}
    table = optimizers.NODE_MEMBERS.table
    open_ = [
        f"{n!r} is declared and neither walked nor reached by a walked member"
        for n in (d.name for d in selected.schema.declared_nodes)
        if n in table and n not in members
    ]
    for kind in (*selected.slices, *selected.outputs):
        writers = sorted(n for n, m in members.items() if kind in m.writes)
        if not writers or (len(writers) > 1 and kind.writers == "one"):
            readers = sorted(n for n, m in members.items() if kind in m.reads)
            open_.append(
                f"{kind.kind!r} is written by {writers or 'no member'} (read by {readers})"
            )
    for node, member in members.items():
        for edge in _edges_of(member):
            held = sorted(n for n, m in members.items() if isinstance(m, edge))
            if len(held) != 1:
                open_.append(f"{node!r} reaches {edge.__name__}, which the roster holds as {held}")
    if open_:
        return open_
    # One closure: a part that holds no value until written is written earlier in the walk than
    # any member reading or drawing it, unless that member writes it itself.
    written: set[type[StatePart]] = set()
    for member in _walk(selected, gate):
        early = sorted(
            kind.kind
            for kind in {*member.reads, *selected.draws(member)}
            if kind.written_first() and kind not in written and kind not in member.writes
        )
        if early:
            open_.append(
                f"{member.name!r} reads {early} before any member writes it"
                + (f" under `arm_gate: {gate}`" if gate == "author" else "")
            )
        written |= member.writes
    for walk in selected.plan.round_walks:
        if not selected.walk_arms(walk):
            open_.append(f"{[p.name for p in walk]} add no arm: no member answers a round's arms")
    if len(reviewers := sorted(n for n, m in members.items() if isinstance(m, Reviewer))) > 1:
        open_.append(f"{reviewers} each answer review.md's reading, which one member answers")
    return open_


def resolve_optimizer(
    name: str, nodes: Mapping[str, ManifestNodeOverlay], *, gate: ArmGateMode
) -> SelectedOptimizer:
    """The roster must close as *gate* walks it: under ``author``, the seat in its proposers' place."""
    overlay = {node: dict(o.config) for node, o in sorted(nodes.items())}
    try:
        return _select(name, _stamps(name), json.dumps(overlay, sort_keys=True), gate)
    except ValueError as exc:
        raise PayloadInvalidError(
            f"optimization.nodes refused by optimizer {name!r}: {exc}", code="optimizer_overlay"
        ) from exc


def select_optimizer(opt: OptimizationConfig) -> SelectedOptimizer:
    return resolve_optimizer(opt.optimizer, opt.nodes, gate=opt.arm_gate)


@dataclass(frozen=True)
class CheckinManifest:
    manifest: PipelineManifest
    schema: PipelineSchema


@functools.lru_cache(maxsize=2)
def _checkin_at(path: Path, _mtime_ns: int) -> CheckinManifest:
    manifest = PipelineManifest.model_validate(read_yaml(path))
    schemas = _read_schemas(checkin_assets_root() / "resolved_schemas.json")
    return CheckinManifest(manifest=manifest, schema=_parse(manifest, schemas))


def checkin_manifest() -> CheckinManifest:
    path = checkin_manifest_path()
    return _checkin_at(path, _stamp(path))


_BOUND: contextvars.ContextVar[SelectedOptimizer | None] = contextvars.ContextVar(
    "selected_optimizer", default=None
)
_DETERMINISM: contextvars.ContextVar[DeterminismClamp | None] = contextvars.ContextVar(
    "determinism_clamp", default=None
)


@contextlib.contextmanager
def binding_optimizer(optimization: OptimizationConfig) -> Iterator[None]:
    token = _BOUND.set(select_optimizer(optimization))
    clamp = _DETERMINISM.set(optimization.determinism)
    try:
        yield
    finally:
        _DETERMINISM.reset(clamp)
        _BOUND.reset(token)


def bound_optimizer() -> SelectedOptimizer:
    selected = _BOUND.get()
    if selected is None:
        raise RuntimeError(
            "no optimizer is bound to this task — the run seam binds "
            "`select_optimizer(config.optimization)` before anything reads a node"
        )
    return selected


def llm_node_manifest(node: str) -> tuple[PipelineNode, Mapping[str, Any], PipelineManifest]:
    checkin = checkin_manifest()
    if (held := checkin.schema.get_node(node)) is not None:
        return held, held.current_config, checkin.manifest
    selected = bound_optimizer()
    return selected.node(node), selected.node_config(node), selected.manifest


# The OUTER's per-node mutations, bound inside the inner task so each recursion level carries its own.
_OPTIMIZER_PROMPT_OVERRIDES: contextvars.ContextVar[dict[str, dict[str, Any]] | None] = (
    contextvars.ContextVar("optimizer_prompt_overrides", default=None)
)


@contextlib.contextmanager
def binding_prompt_overrides(overrides: dict[str, dict[str, Any]] | None) -> Iterator[None]:
    token = _OPTIMIZER_PROMPT_OVERRIDES.set(overrides or None)
    try:
        yield
    finally:
        _OPTIMIZER_PROMPT_OVERRIDES.reset(token)


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
    prompt_fields = {k: v for k, v in raw.items() if k in OptimizerPromptTemplate.model_fields}
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
        prompt_fields, names = _resolved_prompt_parts(raw)
        resolved: dict[str, Any] = dict(prompt_fields)
        if names:
            resolved["output_schema_field_names"] = names
        resolved.update(_resolved_levers(node, raw))
        if resolved:
            nodes[node] = resolved
    model, provider = _single_model(overrides)
    return {"nodes": nodes, "model": model, "provider": provider}
