"""An outer dataset's ``panel``: its typed cells.

The spawn context sits apart from ``spawn.py``: the runner publishes it on its way past, while a spawn reaches back DOWN into it."""

from __future__ import annotations

import itertools
import re
from collections.abc import Sequence
from dataclasses import replace
from typing import TYPE_CHECKING, Any

from pydantic import Field, ValidationError, model_validator

from promptpotter import connectors
from promptpotter.application.bench.difficulty import ruler_clock
from promptpotter.application.datasets.authored import dataset_campaign_block
from promptpotter.application.optimizer_manifest import (
    SelectedOptimizer,
    base_optimizer_template,
    fired_on,
    select_optimizer,
)
from promptpotter.application.optimizers.nodes import BehaviourKeys
from promptpotter.domain.campaign_config import (
    ArmGateMode,
    CampaignConfig,
    OptimizationConfig,
)
from promptpotter.domain.cells import PinnedCell, Seat
from promptpotter.domain.cycle_paths import CycleHop
from promptpotter.domain.l4.cells import CellOptimizer, InnerCell, InnerCells, InnerTaskSpec
from promptpotter.domain.layer_fold import Layer
from promptpotter.domain.pipeline_params import NodeCall
from promptpotter.domain.pipeline_schema import (
    NodeKind,
    NodeSearchNarrowing,
    PipelineNode,
)
from promptpotter.domain.prompt_block import Presumption
from promptpotter.domain.ruler import RulerReach
from promptpotter.domain.sample import Sample
from promptpotter.domain.spend import Budget
from promptpotter.domain.strict_model import FrozenModel
from promptpotter.infrastructure.llm.telemetry import current_round
from promptpotter.infrastructure.run_scope import UNBOUND, InnerSpawnContext, rescope, run_scope
from promptpotter.infrastructure.store.archive_queries import CampaignWalks, CellOf
from promptpotter.infrastructure.store.campaign_store.store import CampaignStore
from promptpotter.infrastructure.store.dataset_access import read_dataset_pipeline, resolve_dataset
from promptpotter.infrastructure.store.layout import inner_sandbox_dir
from promptpotter.infrastructure.store.stores import Stores
from promptpotter.shared.errors import CellUnscoreableError, DatasetAccessError, PayloadInvalidError
from promptpotter.shared.measurement_context import enter_instrument_mode

if TYPE_CHECKING:
    from promptpotter.application.initialization.session import Session


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
    # A `campaign:` partial laid over each seat dataset's own block: one geometry per panel.
    inner_campaign: dict[str, Any]
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
        griddable = set(Seat.model_fields) - {"kind", "id", "inner_route_order", "held_out"}
        for key, levels in axes.items():
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
        by_dataset: dict[str, InnerCell] = {}
        for name in sorted(self.datasets):
            try:
                dataset = resolve_dataset(stores, name)
            except DatasetAccessError as missing:
                seats = ", ".join(cell.id for cell in self.tasks if self.dataset_for(cell) == name)
                raise PayloadInvalidError(
                    f"seat(s) {seats} measure {name!r}, which no tier this account reads holds",
                    code="pipeline_config_invalid",
                ) from missing
            laid = CampaignConfig.model_validate(dataset_campaign_block(dataset)).overlaid(
                Layer("panel", self.inner_campaign)
            )
            if (refused := _unreadable_geometry(laid)) is not None:
                raise PayloadInvalidError(
                    f"the panel's `inner_campaign` over {name!r} {refused}",
                    code="pipeline_config_invalid",
                )
            selected = select_optimizer(_inner_optimization(laid.optimization))
            by_dataset[name] = InnerCell(
                campaign=laid,
                pipeline=read_dataset_pipeline(dataset),
                optimizer=cell_optimizer(selected),
                # Keyed before the cell's chain resolves: every admission, so no drawable edit hides.
                treatment=selected.treatment(frozenset(Presumption), stores.block_packs).digest,
            )
        measures = BehaviourKeys.held().cell_estimator
        return InnerCells(
            cells=tuple(self.tasks),
            by_dataset=by_dataset,
            specs={
                seat.id: self._seat_spec(by_dataset[self.dataset_for(seat)], seat, measures)
                for seat in self.tasks
            },
            estimator=measures,
        )

    def _seat_spec(self, held: InnerCell, cell: Seat, measures: str) -> InnerTaskSpec:
        """Depth is the panel's, never the pin's: the laid campaign's identity holds it."""
        # `config` only: widening a permission list changes nothing the origin does, so it voids no cell.
        nodes = (
            {n: (node or {}).get("config") for n, node in held.pipeline["nodes"].items()}
            if held.pipeline and isinstance(held.pipeline.get("nodes"), dict)
            else None
        )
        return InnerTaskSpec(
            inner_dataset=self.dataset_for(cell),
            optimizer_treatment=held.treatment,
            run_seed=cell.run_seed,
            campaign=held.campaign.identity(),
            nodes=nodes,
            inner_model=cell.inner_model,
            inner_provider=cell.inner_provider,
            inner_route_order=cell.inner_route_order,
            estimator=measures,
        )

    @model_validator(mode="after")
    def _cells_are_distinct(self) -> InnerTasks:
        """`inner_campaign_id` is content-addressed: a twin resumes the first's campaign or trips the one-producer guard."""
        seen_ids: set[str] = set()
        for cell in self.tasks:
            if cell.id in seen_ids:
                raise ValueError(f"duplicate task id {cell.id!r}")
            seen_ids.add(cell.id)
        seen_specs: dict[tuple[object, ...], str] = {}
        for task in self.tasks:
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
                    "drop it; two names for one cell is not two cells."
                )
            seen_specs[key] = task.id
        return self


def parse_inner_panel(raw: object) -> InnerTasks:
    return InnerTasks.model_validate(raw)


class ResolvedInnerPanel(FrozenModel):
    """The panel with each cell's instrument (`spawn.py::_resolve_inner_panel`): the ONE document run
    init, the samples and the fingerprint read."""

    inner_panel: InnerTasks
    # By cell id.
    instruments: dict[str, str]
    # By cell id: the dataset each cell's inner campaign measures, its δ scale's.
    datasets: dict[str, str]

    @model_validator(mode="after")
    def _every_cell_measured(self) -> ResolvedInnerPanel:
        ids = {cell.id for cell in self.inner_panel.tasks}
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
                "held_out": t.held_out,
                "measured_dataset": self.datasets[t.id],
            }
            for t in self.inner_panel.tasks
        ]


def is_self_optimization(connector: str) -> bool:
    return (c := connectors.CONNECTORS.table.get(connector)) is not None and c.self_optimization


INNER_ARM_GATE: ArmGateMode = "off"


def _unreadable_geometry(laid: CampaignConfig) -> str | None:
    """Why a seat's laid campaign reads no held-out area; ``None`` where it reads one."""
    rounds = laid.optimization.budget.rounds
    if rounds is None or rounds < 2:
        return f"lays `optimization.budget.rounds: {rounds}`: the area reads two rounds or more"
    if laid.dataset_split is None or not laid.dataset_split.bench:
        return "lays no `dataset_split.bench`: the area is read on held-out rows alone"
    if laid.bench_trigger != "every_adoption":
        return f"lays `bench_trigger: {laid.bench_trigger}`, which grades no adoption but the last"
    return None


def _inner_optimization(opt: OptimizationConfig) -> OptimizationConfig:
    """An inner cycle's selection key: its nodes as laid, under the inner gate."""
    return opt.model_copy(update={"arm_gate": INNER_ARM_GATE})


def cell_optimizer(inner: SelectedOptimizer) -> CellOptimizer:
    """A cell's optimizer in the outer's terms, read once at resolve."""
    llm = [node.name for node in inner.schema.declared_nodes if node.kind is NodeKind.LLM]
    return CellOptimizer(
        name=inner.name,
        declared=tuple(inner.schema.declared_nodes),
        pipelines={name: list(steps) for name, steps in inner.schema.pipelines.items()},
        levers={node.name: _lever(inner, node.name) for node in inner.schema.declared_nodes},
        outer_levers={name: inner.outer_levers(name) for name in llm},
        fired_on=fired_on(inner.schema),
        measurement=inner.plan.measurement,
        templates={name: base_optimizer_template(inner, name) for name in llm},
    )


def _lever(inner: SelectedOptimizer, node: str) -> dict[str, Any]:
    declared = inner.manifest.nodes[node].model_dump(
        mode="json", exclude_unset=True, exclude={"config"}
    )
    return {**declared, "config": inner.call_settings(node)}


def inner_instrument_config(
    spec: InnerTaskSpec,
    base: CampaignConfig,
    *,
    solver: Sequence[PipelineNode],
) -> CampaignConfig:
    """*base* is the seat's laid campaign (`InnerCell.campaign`). Caps are LIFTED: one tripping on
    measured tokens truncates nondeterministically; the outer book admits every send."""
    opt_update: dict[str, Any] = {
        "budget": Budget(
            usd=None, tokens=None, rounds=base.optimization.budget.rounds, wall_clock_s=None
        ),
        # No lives: the round budget is the width of the area its lift is read over.
        "lives": None,
        # Each gate's await is unbounded, and an instrument cannot ask a human anything.
        "origin_gate": "off",
        "arm_gate": INNER_ARM_GATE,
        # Under 2PL θ is in units of 1/a and each cycle graduates on its OWN CV, so the panel would average a mixture of scales.
        "enable_2pl_graduation": False,
    }
    if not solver:
        raise ValueError("the inner pipeline declares no prompt node: no solver to pin")
    po = dict(base.pipeline_overlay)
    narrowing = dict(base.optimizer_narrowing)
    for prompt_node in solver:
        node = po.get(prompt_node.name, NodeCall()).laid_keys()
        node["seed"] = spec.run_seed
        if spec.inner_model:
            node["model"] = spec.inner_model
            if spec.inner_provider:
                node["provider"] = spec.inner_provider
        if spec.inner_route_order:
            node["route_order"] = list(spec.inner_route_order)
        po[prompt_node.name] = NodeCall.model_validate(node)
        # The solver's SPEND AXES — model, effort, output cap, temperature — are the instrument, so the
        # inner generator writes prompt text alone: a lift bought with tokens is not optimizer learning.
        own = narrowing.get(prompt_node.name) or NodeSearchNarrowing()
        narrowing[prompt_node.name] = own.model_copy(update={"param_keys": []})
    return base.model_copy(
        update={
            "run_seed": spec.run_seed,
            "optimization": base.optimization.model_copy(update=opt_update),
            "pipeline_overlay": po,
            "optimizer_narrowing": narrowing,
        }
    )


def _outer_cells(session: Session) -> InnerCells | None:
    """``None`` off the recursion. The workload's panel, never a second read of the file the
    samples and fingerprint came from."""
    if not session.backend_client.connector.self_optimization:
        return None
    panel: ResolvedInnerPanel = session.backend_client.workload.experiment
    return panel.inner_panel.resolve_cells(session.store)


def inner_spawn_of(session: Session) -> InnerSpawnContext | None:
    """``None`` is a run with no cycle to spawn under; the run scope carries it (`run_scope.py`)."""
    cycle_id = session.cycle_id
    if not cycle_id or session.dataset is None or not session.campaign_id:
        return None
    # Never this store's ``projects_root``, which inside a sandbox already IS the sandbox.
    shared_root = session.store.shared_root
    inner_root = inner_sandbox_dir(
        shared_root,
        session.store.tenant_id,
        CycleHop(campaign_id=session.campaign_id, cycle_id=cycle_id),
    )
    cells = _outer_cells(session)
    return InnerSpawnContext(
        inner_sandbox_root=inner_root,
        identity=session.store.identity,
        shared_root=shared_root,
        install_datasets_root=session.store.install_datasets_root,
        research_datasets_root=session.store.research_datasets_root,
        inside=(*session.store.inside, session.hop),
        enclosing=(*session.state.control.enclosing, session.store.subject(session.hop)),
        acting=session.state.control.acting,
        purpose=session.purpose,
        ruler=ruler_clock(session),
        cells=cells,
    )


def inner_spawn_context() -> InnerSpawnContext | None:
    if (spawn := run_scope().spawn) is UNBOUND:
        raise RuntimeError(
            "no inner-spawn context is scoped, so whether this run is an L4 outer is unknown: "
            "the entry driving it scopes `inner_spawn_of` first"
        )
    return spawn


def inner_cells() -> InnerCells | None:
    """The cells an L4 outer's arms are measured on, each running its own manifest, never the
    outer's own; ``None`` off the recursion."""
    ctx = inner_spawn_context()
    return None if ctx is None else ctx.cells


def pin_inner_ruler(reach: RulerReach) -> None:
    """At each outer round's open, so every cell the round spawns reads the lines it reads."""
    ctx = inner_spawn_context()
    if ctx is not None and ctx.ruler != reach:
        rescope(spawn=replace(ctx, ruler=reach))


def enter_inner_cell(ctx: InnerSpawnContext, campaigns: CampaignStore, campaign_id: str) -> CellOf:
    """The archive stays the cell's CACHE, and MEMORY only for its campaign's walks: the scope it returns."""
    # Before the cell's run binds its own round, so this still reads the spawner's.
    enter_instrument_mode(spawned_in=current_round())
    return CellOf(CampaignWalks(campaigns, campaign_id), ctx.ruler)


def retarget_inner_spawn(session: Session) -> None:
    """Moves only the asker; the sandbox owner never follows a fork."""
    ctx = inner_spawn_context()
    cycle_id = session.cycle_id
    if ctx is None or not cycle_id:
        return
    asking = session.store.subject(session.hop)
    if ctx.enclosing[-1] == asking:
        return
    rescope(spawn=replace(ctx, enclosing=(*ctx.enclosing[:-1], asking)))
