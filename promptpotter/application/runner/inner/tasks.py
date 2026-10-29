"""An outer dataset's ``panel``: its typed cells."""

from __future__ import annotations

import itertools
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from functools import cached_property
from typing import TYPE_CHECKING, Any

from pydantic import Field, ValidationError, model_validator

from promptpotter import connectors
from promptpotter.application.datasets.authored import dataset_campaign_block
from promptpotter.application.optimizer_manifest import SelectedOptimizer, resolve_optimizer
from promptpotter.domain.bench import DatasetSplit
from promptpotter.domain.campaign import DeterminismClamp
from promptpotter.domain.campaign_config import (
    DEFAULT_ORIGIN_BUDGET,
    ArmGateMode,
    CampaignConfig,
    LivesConfig,
    OptimizationConfig,
    merge_node_overlays,
)
from promptpotter.domain.cells import DEPTH_FIELDS, PinnedCell, Seat
from promptpotter.domain.l4.proxies import INNER_RESULT_KEY, OUTER_PROXY_KEYS
from promptpotter.domain.opt_search_point import OptimizerPromptTemplate
from promptpotter.domain.pipeline_schema import (
    ManifestNodeOverlay,
    NodeKind,
    NodeRole,
    NodeSearchNarrowing,
    PipelineNode,
)
from promptpotter.domain.prompt_block import Presumption
from promptpotter.domain.spend import Budget
from promptpotter.domain.strict_model import FrozenModel
from promptpotter.infrastructure.store.dataset_access import (
    read_dataset_pipeline,
    resolve_dataset,
)
from promptpotter.shared.errors import CellUnscoreableError

if TYPE_CHECKING:
    from promptpotter.connectors.protocol import Connector
    from promptpotter.domain.sample import Sample
    from promptpotter.infrastructure.store.stores import Stores


class InnerBenchmarkConfig(FrozenModel):
    """A SPEND bound only: a target score or default ladder rescales every fitness against an undeclared benchmark."""

    n_samples_per_inner_round: int = Field(ge=1)
    # Explicit `null` ⇒ the per-round count; the inner-origin θ is the term BOTH arms of every paired delta subtract.
    n_samples_origin: int | None = Field(default=DEFAULT_ORIGIN_BUDGET, ge=1)
    # Held OUT of the search: drawn beside the origin bank under the cell's seed, read by every
    # bench pass and reached by no inner node. The scored lift is read on these rows alone.
    n_samples_bench: int = Field(ge=1)
    max_inner_rounds: int = Field(ge=1)
    # Part of what a cell IS, so it enters the cell's identity.
    inner_nodes: dict[str, ManifestNodeOverlay] = Field(default_factory=dict)
    # Held OUT of the identity: how much evidence a cell buys, never what it is.
    inner_depth_nodes: dict[str, ManifestNodeOverlay] = Field(default_factory=dict)
    inner_lives: LivesConfig | None = None
    # At the file default, identical optimizer prompts generate different candidates and the swing swamps the outer proxy.
    inner_optimizer_temperature: float | None = Field(default=None, ge=0.0, le=2.0)


def panel_cell(sample: Sample) -> Seat:
    try:
        return PinnedCell.from_pin(sample.source_pin or {}).cell
    except ValidationError as exc:
        raise CellUnscoreableError(
            f"{sample.query!r} is no cell of an inner panel: {exc}", spent={}
        ) from exc


def _level_slug(value: object) -> str:
    """The generated cell id is the OUTER QUERY, so it stays byte-stable across runs."""
    return re.sub(r"[^A-Za-z0-9]+", "-", str(value)).strip("-") or "none"


class InnerTasks(FrozenModel):
    """A panel of seats, each a fresh inner campaign run as an inner hop of the arm's cell."""

    inner_benchmark: str = Field(min_length=1)
    inner_benchmark_config: InnerBenchmarkConfig
    tasks: list[Seat] = Field(min_length=1)

    @model_validator(mode="before")
    @classmethod
    def _expand_axes(cls, data: object) -> object:
        """Only a BALANCED factorial makes a marginal readable: two hand-listed axes can cut one partition."""
        if not isinstance(data, dict) or "axes" not in data:
            return data
        raw = dict(data)
        axes = raw.pop("axes")
        if raw.get("tasks"):
            raise ValueError(
                "a panel declares `axes:` or `tasks:`, never both — the product would have to be "
                "reconciled with the list, and which one wins has no right answer."
            )
        if not isinstance(axes, dict) or not axes:
            raise ValueError("`axes:` must be a non-empty mapping of cell field → list of levels")
        # A route order belongs to ONE model, so it is declared on the cell and never crossed; a
        # held-out seat is a role in the panel, which a factorial has no level for.
        griddable = set(Seat.model_fields) - {
            "kind",
            "id",
            "n_rounds",
            "inner_route_order",
            "held_out",
        }
        for key, levels in axes.items():
            if key == "n_rounds":
                raise ValueError(
                    "`axes.n_rounds` is not a grid axis — depth is CONTINUED on a cell, "
                    "never forked into a second one. Run the grid at one depth, then raise "
                    "`max_inner_rounds` and re-run: every cell deepens in place."
                )
            if key not in griddable:
                raise ValueError(
                    f"`axes.{key}` is not a griddable cell field; an axis is one of "
                    f"{', '.join(sorted(griddable))}. `id` is derived from the coordinate."
                )
            if not isinstance(levels, list) or not levels:
                raise ValueError(f"`axes.{key}` must be a non-empty list of levels")
            if len({_level_slug(v) for v in levels}) != len(levels):
                raise ValueError(
                    f"`axes.{key}` repeats a level — each level is one column of the grid, and "
                    "two spellings of one value would generate two cells that measure the same "
                    "thing."
                )
        keys = list(axes)
        raw["tasks"] = [
            {
                "kind": "seat",
                "id": "__".join(_level_slug(v) for v in combo),
                **dict(zip(keys, combo, strict=True)),
            }
            for combo in itertools.product(*(axes[k] for k in keys))
        ]
        return raw

    def dataset_for(self, cell: Seat) -> str:
        return cell.inner_dataset or self.inner_benchmark

    @property
    def datasets(self) -> frozenset[str]:
        return frozenset(self.dataset_for(cell) for cell in self.tasks)

    def resolve_cells(self, stores: Stores) -> InnerCells:
        """Each cell runs its own optimizer: the treatment is the cell's."""
        cfg = self.inner_benchmark_config
        by_dataset: dict[str, InnerCell] = {}
        for name in sorted(self.datasets):
            dataset_dir = resolve_dataset(stores, name).directory
            campaign = dataset_campaign_block(dataset_dir)
            opt = CampaignConfig.model_validate(campaign).optimization
            identity = _select_inner_optimizer(opt, cfg.inner_nodes, {}, n_samples=None)
            by_dataset[name] = InnerCell(
                campaign_config=campaign,
                pipeline=read_dataset_pipeline(dataset_dir),
                optimizer=_select_inner_optimizer(
                    opt,
                    cfg.inner_nodes,
                    cfg.inner_depth_nodes,
                    n_samples=cfg.n_samples_per_inner_round,
                ),
                # Keyed before the cell's chain resolves: every admission, so no drawable edit hides.
                treatment=identity.treatment(frozenset(Presumption)).digest,
            )
        return InnerCells(
            cells=tuple(self.tasks),
            by_dataset=by_dataset,
            specs={
                seat.id: self._seat_spec(by_dataset[self.dataset_for(seat)].treatment, seat)
                for seat in self.tasks
            },
        )

    def _seat_spec(self, treatment: str, cell: Seat) -> InnerTaskSpec:
        """Depth is the panel's, never the pin's: a cell's key holds what it is, not how deep it ran."""
        cfg = self.inner_benchmark_config
        return InnerTaskSpec(
            inner_dataset=self.dataset_for(cell),
            optimizer_treatment=treatment,
            run_seed=cell.run_seed,
            n_samples=cfg.n_samples_per_inner_round,
            n_samples_origin=cfg.n_samples_origin,
            n_samples_bench=cfg.n_samples_bench,
            n_rounds=cell.n_rounds or cfg.max_inner_rounds,
            nodes=cfg.inner_nodes,
            depth_nodes=cfg.inner_depth_nodes,
            lives=cfg.inner_lives,
            inner_model=cell.inner_model,
            inner_provider=cell.inner_provider,
            inner_route_order=cell.inner_route_order,
            inner_optimizer_temperature=cfg.inner_optimizer_temperature,
        )

    @model_validator(mode="after")
    def _cells_are_distinct(self) -> InnerTasks:
        """`inner_campaign_id` is content-addressed: a twin CONTINUES the first or trips the one-producer guard."""
        seen_ids: set[str] = set()
        for cell in self.tasks:
            if cell.id in seen_ids:
                raise ValueError(f"duplicate task id {cell.id!r}")
            seen_ids.add(cell.id)
        seen_specs: dict[tuple[object, ...], str] = {}
        for task in self.tasks:
            # `n_rounds` is absent deliberately: two cells differing only in depth ARE one cell.
            key = (
                self.dataset_for(task),
                task.run_seed,
                task.inner_model,
                task.inner_provider,
                tuple(task.inner_route_order or ()),
            )
            if (twin := seen_specs.get(key)) is not None:
                raise ValueError(
                    f"tasks {twin!r} and {task.id!r} resolve to the same inner campaign "
                    f"(dataset={key[0]!r}, run_seed={key[1]}) — give one a different seed, or "
                    "drop it; two names for one cell is not two cells. A different "
                    "`n_rounds` does not separate them: depth is CONTINUED on one cell, "
                    "never forked into a second."
                )
            seen_specs[key] = task.id
        return self


def parse_panel(raw: object) -> InnerTasks:
    return InnerTasks.model_validate(raw)


class ResolvedPanel(FrozenModel):
    """The panel with each cell's instrument (`connector.py::_resolve_panel`): the ONE document run
    init, the samples and the fingerprint read."""

    panel: InnerTasks
    # By cell id.
    instruments: dict[str, str]
    # By cell id: the dataset each cell's inner campaign measures, its δ scale's.
    datasets: dict[str, str]

    @model_validator(mode="after")
    def _every_cell_measured(self) -> ResolvedPanel:
        ids = {cell.id for cell in self.panel.tasks}
        if set(self.instruments) != ids or set(self.datasets) != ids:
            raise ValueError(
                "a resolved panel carries one instrument and one dataset per cell, and no other"
            )
        return self

    def samples(self) -> list[dict[str, Any]]:
        """``held_out`` seats the cell on the outer bench and stays out of the pin: the seat is where
        the cell sits this campaign, not what it measures, so its cells replay once it is searched."""
        return [
            {
                "query": t.id,
                "ground_truth": None,
                "source_pin": PinnedCell(cell=t, instrument=self.instruments[t.id]).pin(),
                "bench_only": t.held_out,
                "measured_dataset": self.datasets[t.id],
            }
            for t in self.panel.tasks
        ]


def parse_resolved(raw: object) -> ResolvedPanel:
    return ResolvedPanel.model_validate(raw)


class InnerTaskSpec(FrozenModel):
    inner_dataset: str
    optimizer_treatment: str
    run_seed: int
    n_samples: int
    n_samples_origin: int | None = None
    n_samples_bench: int
    n_rounds: int
    nodes: dict[str, ManifestNodeOverlay] = Field(default_factory=dict)
    depth_nodes: dict[str, ManifestNodeOverlay] = Field(default_factory=dict)
    lives: LivesConfig | None = None
    inner_model: str | None = None
    inner_provider: str | None = None
    inner_route_order: list[str] | None = None
    inner_optimizer_temperature: float | None = None

    def treatment(self) -> dict[str, Any]:
        """SUBTRACTED, so a new spec field defaults to identity: a needless fork wastes a run, a
        wrong continue corrupts one."""
        return {k: v for k, v in self.model_dump(mode="json").items() if k not in DEPTH_FIELDS}


assert set(InnerTaskSpec.model_fields) >= DEPTH_FIELDS


def _recursion_connector() -> Connector:
    return connectors.CONNECTORS.get("promptpotter")


def is_self_optimization(backend_type: str) -> bool:
    return connectors.CONNECTORS.table.get(backend_type) is _recursion_connector()


INNER_ARM_GATE: ArmGateMode = "off"


def _inner_manifest_nodes(
    optimizer: str,
    own: Mapping[str, ManifestNodeOverlay],
    nodes: Mapping[str, ManifestNodeOverlay],
    depth_nodes: Mapping[str, ManifestNodeOverlay],
    *,
    n_samples: int | None,
) -> dict[str, ManifestNodeOverlay]:
    """``n_samples=None`` leaves the sampler's size out, which is what the identity hashes."""
    merged = merge_node_overlays(merge_node_overlays(own, nodes), depth_nodes)
    sampler = resolve_optimizer(optimizer, merged, gate=INNER_ARM_GATE).sampler
    if n_samples is None or sampler.size_knob is None:
        return merged
    size = {sampler.name: ManifestNodeOverlay(config={sampler.size_knob: n_samples})}
    return merge_node_overlays(merged, size)


def _select_inner_optimizer(
    opt: OptimizationConfig,
    nodes: Mapping[str, ManifestNodeOverlay],
    depth_nodes: Mapping[str, ManifestNodeOverlay],
    *,
    n_samples: int | None,
) -> SelectedOptimizer:
    return resolve_optimizer(
        opt.optimizer,
        _inner_manifest_nodes(opt.optimizer, opt.nodes, nodes, depth_nodes, n_samples=n_samples),
        gate=INNER_ARM_GATE,
    )


# `problem_description` and `answer_format` are absent: they carry the injection slots and the output contract.
OUTER_PROMPT_FIELDS: tuple[str, ...] = ("persona", "task_intent", "instruction", "thinking_style")
assert set(OUTER_PROMPT_FIELDS) <= set(OptimizerPromptTemplate.RENDER_ORDER)

_OUTER_KINDS = frozenset({NodeKind.LLM, NodeKind.GATEWAY})


@dataclass(frozen=True)
class InnerCell:
    campaign_config: Mapping[str, Any]
    pipeline: Mapping[str, Any] | None
    optimizer: SelectedOptimizer
    # The digest without the panel's depth, so a deepened cell continues its campaign.
    treatment: str


def _chain(inner: SelectedOptimizer) -> list[str]:
    return [n for n in inner.schema.pipelines["default"] if inner.node(n).kind in _OUTER_KINDS]


def _lever(inner: SelectedOptimizer, node: str) -> dict[str, Any]:
    """What an arm's edit to *node* lands on; the knobs are left out, being how much a cell buys."""
    declared = inner.manifest.nodes[node].model_dump(
        mode="json", exclude_unset=True, exclude={"config"}
    )
    return {**declared, "config": inner.call_config(node)}


def _terminal(inner: SelectedOptimizer) -> str:
    """The chain's last llm node; only config-less measurement nodes follow, so only a FULL match replays a row."""
    return [n for n in _chain(inner) if inner.node(n).kind is NodeKind.LLM][-1]


@dataclass(frozen=True)
class InnerCells:
    """Each cell runs its own optimizer; the outer graph is the union of their nodes, in panel order,
    so a cell added to the panel appends nodes and moves no banked cell's chain."""

    # As declared, in panel order.
    cells: tuple[Seat, ...]
    by_dataset: Mapping[str, InnerCell]
    # Each seat's spec, by id: a pinned seat carries no depth, so it is read here, never off the pin.
    specs: Mapping[str, InnerTaskSpec]

    def held(self, cell: Seat) -> InnerCell:
        return self.by_dataset[self.spec(cell).inner_dataset]

    def spec(self, cell: Seat) -> InnerTaskSpec:
        return self.specs[cell.id]

    @property
    def optimizers(self) -> list[SelectedOptimizer]:
        """One per manifest, in panel order: a manifest's graph is its name's."""
        seen: dict[str, SelectedOptimizer] = {}
        for cell in self.cells:
            inner = self.held(cell).optimizer
            seen.setdefault(inner.name, inner)
        return list(seen.values())

    @cached_property
    def owners(self) -> Mapping[str, SelectedOptimizer]:
        """The manifest each outer node is a lever of, over EVERY cell. RAISES where two cells run one
        node differently, overlay included: an arm's edit to it would mean a different prompt in each."""
        held: dict[str, SelectedOptimizer] = {}
        for cell in self.cells:
            inner = self.held(cell).optimizer
            for node in inner.schema.declared_nodes:
                first = held.setdefault(node.name, inner)
                if _lever(inner, node.name) != _lever(first, node.name):
                    raise ValueError(
                        f"cell {cell.id!r} runs node {node.name!r} of {inner.name!r} unlike an "
                        f"earlier cell's {first.name!r}: one outer node is one lever, so align "
                        "their campaign overlays or split the panel"
                    )
        return held

    @property
    def chain(self) -> list[str]:
        return list(dict.fromkeys(n for inner in self.optimizers for n in _chain(inner)))

    def terminal(self, cell: Seat) -> str:
        return _terminal(self.held(cell).optimizer)

    def own(self, cell: Seat, arm: Mapping[str, Any]) -> dict[str, Any]:
        """The arm's edit on the nodes this cell's optimizer runs: another manifest's node is not its lever."""
        nodes = {node.name for node in self.held(cell).optimizer.schema.declared_nodes}
        return {node: config for node, config in arm.items() if node in nodes}

    def pipeline(self) -> dict[str, Any]:
        """RAISES where two manifests declare one node differently (``owners``)."""
        observed = [{"pipeline_key": key} for key in (INNER_RESULT_KEY, *OUTER_PROXY_KEYS)]
        terminals = {_terminal(inner) for inner in self.optimizers}
        owners = self.owners
        nodes: dict[str, dict[str, Any]] = {}
        walks: dict[str, list[str]] = {}
        for inner in self.optimizers:
            for node in inner.schema.declared_nodes:
                if owners[node.name] is not inner:
                    continue
                if node.kind is NodeKind.GATEWAY:
                    declared: dict[str, Any] = {"type": node.kind.value, "config": {}}
                elif node.kind is NodeKind.LLM:
                    levers = inner.outer_levers(node.name)
                    terminal = node.name in terminals
                    declared = {
                        "type": node.kind.value,
                        "node_role": NodeRole.RANKER.value if terminal else "",
                        "config": {},
                        "optimizer": {
                            "param_keys": [*OUTER_PROMPT_FIELDS, *levers],
                            "param_types": levers,
                            "observation_name": node.name,
                            "observation_mappings": observed if terminal else [],
                        },
                    }
                else:
                    continue
                nodes[node.name] = declared
            for name, steps in inner.schema.pipelines.items():
                walk = walks.setdefault(name, [])
                walk += [n for n in steps if n in nodes and n not in walk]
        return {
            "name": "+".join(inner.name for inner in self.optimizers),
            "nodes": nodes,
            "pipelines": {k: v for k, v in walks.items() if v},
        }


def inner_instrument_config(
    spec: InnerTaskSpec,
    base: CampaignConfig,
    *,
    solver: Sequence[PipelineNode],
    n_search: int,
) -> CampaignConfig:
    """Caps are LIFTED: one tripping on measured tokens truncates nondeterministically; the outer book admits every send."""
    opt_update: dict[str, Any] = {
        "budget": Budget(usd=None, tokens=None, rounds=spec.n_rounds, wall_clock_s=None),
        "lives": spec.lives,
        # Each gate's await is unbounded, and an instrument cannot ask a human anything.
        "origin_gate": "off",
        "arm_gate": INNER_ARM_GATE,
        # Under 2PL θ is in units of 1/a and each cycle graduates on its OWN CV, so the panel would average a mixture of scales.
        "enable_2pl_graduation": False,
        # The nodes and gate `_select_inner_optimizer` resolves: the manifest a cell holds is the one it runs.
        "nodes": _inner_manifest_nodes(
            base.optimization.optimizer,
            base.optimization.nodes,
            spec.nodes,
            spec.depth_nodes,
            n_samples=spec.n_samples,
        ),
    }
    if spec.inner_optimizer_temperature is not None:
        # The clamp's seed is the CELL's, so every candidate measured on a cell draws one random stream (CRN).
        opt_update["determinism"] = (
            base.optimization.determinism or DeterminismClamp()
        ).model_copy(
            update={
                "temperature": spec.inner_optimizer_temperature,
                "seed": spec.run_seed,
            }
        )
    if not solver:
        raise ValueError("the inner pipeline declares no prompt node: no solver to pin")
    po: dict[str, Any] = {k: dict(v) for k, v in (base.pipeline_overlay or {}).items()}
    narrowing = dict(base.optimizer_narrowing)
    for prompt_node in solver:
        node = dict(po.get(prompt_node.name, {}))
        node["seed"] = spec.run_seed
        if spec.inner_model:
            node["model"] = spec.inner_model
            if spec.inner_provider:
                node["provider"] = spec.inner_provider
        if spec.inner_route_order:
            node["route_order"] = list(spec.inner_route_order)
        po[prompt_node.name] = node
        # The solver's SPEND AXES — model, effort, output cap, temperature — are the instrument, so the
        # inner generator writes prompt text alone: a lift bought with tokens is not optimizer learning.
        own = narrowing.get(prompt_node.name) or NodeSearchNarrowing()
        narrowing[prompt_node.name] = own.model_copy(update={"param_keys": []})
    return base.model_copy(
        update={
            "sp_budget_origin": n_search,
            # Every bench-only row of the bank is held out; `bench: 0` ranks no search row away.
            "dataset_split": DatasetSplit(bench=0, tolerance=spec.n_samples_bench // 10),
            "run_seed": spec.run_seed,
            "bench_trigger": "each_round",
            "optimization": base.optimization.model_copy(update=opt_update),
            "pipeline_overlay": po,
            "optimizer_narrowing": narrowing,
        }
    )
