"""An outer dataset's ``panel``: its typed cells.

The spawn context sits apart from ``spawn.py``: the runner publishes it on its way past, while a spawn reaches back DOWN into it."""

from __future__ import annotations

import itertools
import re
from collections.abc import Mapping, Sequence
from dataclasses import replace
from typing import TYPE_CHECKING, Annotated, Any

from pydantic import Field, PrivateAttr, model_validator

from promptpotter import connectors
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
from promptpotter.domain.l4.cells import CellOptimizer, InnerCell, InnerCells
from promptpotter.domain.layer_fold import LaidAs, Layer
from promptpotter.domain.pipeline_params import NodeCall
from promptpotter.domain.pipeline_schema import (
    ManifestNodeOverlay,
    NodeKind,
    NodeSearchNarrowing,
    PipelineNode,
)
from promptpotter.domain.sample import Sample
from promptpotter.domain.spend import Budget
from promptpotter.domain.strict_model import FrozenModel
from promptpotter.infrastructure.run_scope import (
    UNBOUND,
    InnerSpawnContext,
    InstrumentMode,
    rescope,
    run_scope,
)
from promptpotter.infrastructure.store.archive_queries import ArmOf, CampaignWalks
from promptpotter.infrastructure.store.campaign_store.store import CampaignStore
from promptpotter.infrastructure.store.dataset_access import (
    resolve_dataset,
    runnable_definition,
)
from promptpotter.infrastructure.store.stores import Stores
from promptpotter.shared.errors import CellUnscoreableError, DatasetAccessError, PayloadInvalidError

if TYPE_CHECKING:
    from promptpotter.application.initialization.session import Session


def panel_cell(sample: Sample, seats: Sequence[Seat]) -> PinnedCell:
    """The seat of *seats* the row's pin IS, whatever it is labelled this campaign."""
    try:
        return PinnedCell.from_pin(sample.source_pin or {}, seats)
    except ValueError as exc:
        raise CellUnscoreableError(
            f"{sample.query!r} is no cell of an inner panel: {exc}", spent={}
        ) from exc


def _level_slug(value: object) -> str:
    """A generated seat's id, byte-stable across runs."""
    return re.sub(r"[^A-Za-z0-9]+", "-", str(value)).strip("-") or "none"


class InnerTasks(FrozenModel):
    """A panel of seats, each a fresh inner campaign run as an inner hop of the arm's cell."""

    inner_benchmark: str = Field(min_length=1)
    # A `campaign:` partial laid over each seat dataset's own block: one geometry per panel. Nodes
    # are an optimizer's, so they ride `inner_nodes`, by the optimizer each seat runs.
    inner_campaign: Annotated[dict[str, Any], LaidAs(CampaignConfig)]
    inner_nodes: dict[str, dict[str, ManifestNodeOverlay]] = Field(default_factory=dict)
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

    def query_of(self, cell: Seat) -> str:
        """The cell's row as the outer reads it, spelled off what it IS (the fields that tell two
        cells apart, `_cells_are_distinct`'s), so renaming its seat moves no `Sample.key`."""
        route = "+".join(cell.inner_route_order or ())
        parts = (cell.inner_model, cell.inner_provider, route)
        return "/".join([self.dataset_for(cell), f"seed-{cell.run_seed}", *(p for p in parts if p)])

    @property
    def datasets(self) -> frozenset[str]:
        return frozenset(self.dataset_for(cell) for cell in self.tasks)

    def resolve_cells(self, stores: Stores) -> InnerCells:
        """Each cell runs its own optimizer: the treatment is the cell's."""
        if "scoring" not in self.inner_campaign:
            # The inner scoring and ladder are the panel's alone (`_laid` clears each seat's).
            raise PayloadInvalidError(
                "the panel's `inner_campaign` declares no `scoring`: every seat's inner run is "
                "scored by the one measurand the panel declares, never its dataset's own",
                code="pipeline_config_invalid",
            )
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
            if runnable_definition(dataset).owner.self_optimization:
                # Its seat keys would be built at this panel's own init, and no inner run spawns one.
                raise PayloadInvalidError(
                    f"the panel's seats measure {name!r}, itself an outer dataset: name an "
                    "inner_benchmark on another backend",
                    code="pipeline_config_invalid",
                )
            laid = self._laid(CampaignConfig.model_validate(dataset_campaign_block(dataset)))
            if (refused := _unreadable_geometry(laid)) is not None:
                raise PayloadInvalidError(
                    f"the panel's `inner_campaign` over {name!r} {refused}",
                    code="pipeline_config_invalid",
                )
            selected = select_optimizer(_inner_optimization(laid.optimization))
            by_dataset[name] = InnerCell(laid, cell_optimizer(selected, laid.scoring))
        return InnerCells(
            cells=tuple(self.tasks),
            by_dataset=by_dataset,
            datasets={seat.id: self.dataset_for(seat) for seat in self.tasks},
            estimator=BehaviourKeys.held().cell_estimator,
        )

    def _laid(self, own: CampaignConfig) -> CampaignConfig:
        """*own* under the panel's one measurand and geometry, then its optimizer's `inner_nodes`
        over its nodes, the seat's controllers cleared: one grade and one ladder across a panel."""
        laid = own.overlaid(Layer("seat", {"scoring": None}), Layer("panel", self.inner_campaign))
        selected = select_optimizer(_inner_optimization(laid.optimization))
        declared = selected.schema.declared_nodes
        ladders = {node.name: None for node in declared if node.kind is NodeKind.CONTROLLER}
        own_nodes = self.inner_nodes.get(selected.name, {}).items()
        nodes = {n: o.model_dump(mode="json", exclude_unset=True) for n, o in own_nodes}
        return laid.overlaid(
            Layer("seat", {"optimization": {"nodes": ladders}}),
            Layer("panel", {"optimization": {"nodes": nodes}}),
        )

    @model_validator(mode="after")
    def _nodes_ride_their_optimizer(self) -> InnerTasks:
        if "nodes" in self.inner_campaign.get("optimization", {}):
            raise ValueError(
                "`inner_campaign.optimization.nodes` is one optimizer's: use `inner_nodes`"
            )
        return self

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


class ResolvedInnerPanel(FrozenModel):
    """The panel with its cells resolved (`read`): what run init, the graph and the identity read.
    Each cell's key is laid where the bank is built (`spawn.py::panel_rows`), never here: it
    reads what each seat's session loads."""

    inner_panel: InnerTasks
    _cells: InnerCells | None = PrivateAttr(None)

    @classmethod
    def read(cls, stores: Stores, panel: Mapping[str, Any]) -> ResolvedInnerPanel:
        parsed = InnerTasks.model_validate(panel)
        read = cls(inner_panel=parsed)
        read._cells = parsed.resolve_cells(stores)
        return read

    def cells(self, stores: Stores) -> InnerCells:
        """Resolved once per read; a pinned panel (`Connector.pinned`) resolves on its first ask."""
        if self._cells is None:
            self._cells = self.inner_panel.resolve_cells(stores)
        return self._cells

    def samples(self, keys: Mapping[str, str]) -> list[dict[str, Any]]:
        """*keys* by seat id (`cell_key`). ``held_out`` seats the cell on the outer bench and stays
        out of the pin: the seat is where the cell sits this campaign, not what it measures, so its
        cells replay once it is searched."""
        return [
            {
                "query": self.inner_panel.query_of(t),
                "ground_truth": None,
                "source_pin": PinnedCell(cell=t, instrument=keys[t.id]).pin(),
                "held_out": t.held_out,
                "measured_dataset": self.inner_panel.dataset_for(t),
            }
            for t in self.inner_panel.tasks
        ]


def is_self_optimization(connector: str) -> bool:
    return connectors.CONNECTORS.get(connector).self_optimization


INNER_ARM_GATE: ArmGateMode = "off"


def _unreadable_geometry(laid: CampaignConfig) -> str | None:
    """Why a seat's laid campaign reads no held-out area; ``None`` where it reads one."""
    rounds = laid.optimization.budget.rounds
    if rounds is None or rounds < 2:
        return f"lays `optimization.budget.rounds: {rounds}`: the area reads two rounds or more"
    if not laid.dataset_split.bench:
        return "lays no `dataset_split.bench`: the area is read on held-out rows alone"
    if laid.bench_trigger != "every_adoption":
        return f"lays `bench_trigger: {laid.bench_trigger}`, which grades no adoption but the last"
    return None


def _inner_optimization(opt: OptimizationConfig) -> OptimizationConfig:
    """An inner cycle's selection key: its nodes as laid, under the inner gate."""
    return opt.model_copy(update={"arm_gate": INNER_ARM_GATE})


def cell_optimizer(inner: SelectedOptimizer, scoring: object) -> CellOptimizer:
    """A cell's optimizer in the outer's terms, read once at resolve; *scoring* is its campaign's."""
    llm = [node.name for node in inner.schema.declared_nodes if node.kind is NodeKind.LLM]
    return CellOptimizer(
        name=inner.name,
        declared=tuple(inner.schema.declared_nodes),
        pipelines={name: list(steps) for name, steps in inner.schema.pipelines.items()},
        levers={node.name: _lever(inner, node, scoring) for node in inner.schema.declared_nodes},
        outer_levers={name: inner.outer_levers(name) for name in llm},
        fired_on=fired_on(inner.schema),
        measurement=inner.plan.measurement,
        templates={name: base_optimizer_template(inner, name) for name in llm},
    )


def _lever(inner: SelectedOptimizer, node: PipelineNode, scoring: object) -> dict[str, Any]:
    """What an arm's edit to *node* lands on, beside what makes two cells' measurands one: the
    measurement node's scoring and the controller's ladder (`InnerCells.owners` refuses a split)."""
    declared = inner.manifest.nodes[node.name].model_dump(
        mode="json", exclude_unset=True, exclude={"config"}
    )
    lever = {**declared, "config": inner.call_settings(node.name)}
    if node.name == inner.plan.measurement:
        lever["scoring"] = scoring
    if node.kind is NodeKind.CONTROLLER:
        lever["ladder"] = inner.knobs(node.name).model_dump(mode="json")
    return lever


def inner_instrument_config(
    cell: Seat,
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
        node["seed"] = cell.run_seed
        if cell.inner_model:
            node["model"] = cell.inner_model
            if cell.inner_provider:
                node["provider"] = cell.inner_provider
        if cell.inner_route_order:
            node["route_order"] = list(cell.inner_route_order)
        po[prompt_node.name] = NodeCall.model_validate(node)
        # The solver's SPEND AXES — model, effort, output cap, temperature — are the instrument, so the
        # inner generator writes prompt text alone: a lift bought with tokens is not optimizer learning.
        own = narrowing.get(prompt_node.name) or NodeSearchNarrowing()
        narrowing[prompt_node.name] = own.model_copy(update={"param_keys": []})
    return base.model_copy(
        update={
            "run_seed": cell.run_seed,
            "optimization": base.optimization.model_copy(update=opt_update),
            "pipeline_overlay": po,
            "optimizer_narrowing": narrowing,
        }
    )


def inner_spawn_of(session: Session) -> InnerSpawnContext | None:
    """``None`` is a run with no cycle to spawn under; the run scope carries it (`run_scope.py`)."""
    cycle_id = session.cycle_id
    if not cycle_id or session.dataset is None or not session.campaign_id:
        return None
    client = session.backend_client
    return InnerSpawnContext(
        stores=session.store,
        inside=(*session.store.inside, session.hop),
        enclosing=(*session.state.control.enclosing, session.store.subject(session.hop)),
        acting=session.state.control.acting,
        purpose=session.purpose,
        ruler=_round_zero_ruler(session),
        cells=client.workload.experiment.cells(session.store)
        if client.connector.self_optimization
        else None,
    )


def _round_zero_ruler(session: Session) -> int:
    """The version the cycle's round 0 closed on, off its close, so a resume and a replay pin the
    one the first launch did; until round 0 closes, the clock the origin opens on."""
    held = session.store.campaigns.standing_rounds(session.hop, moment=None).rounds.get(0)
    if held is None:
        return session.store.ruler.version()
    return held.close.level_series.stamp.version


def inner_spawn_context() -> InnerSpawnContext | None:
    if (spawn := run_scope().spawn) is UNBOUND:
        raise RuntimeError(
            "no inner-spawn context is scoped, so whether this run is an L4 outer is unknown: "
            "the entry driving it scopes `inner_spawn_of` first"
        )
    return spawn


def enter_inner_cell(ctx: InnerSpawnContext, campaigns: CampaignStore, campaign_id: str) -> ArmOf:
    """The archive stays the cell's CACHE, and MEMORY only for its campaign's walks: the scope it returns."""
    # INSIDE the cell's own task, or the mode leaks to the spawner; before the cell's run binds its
    # own round, so this still reads the spawner's: the root's, as the spawner is the root.
    spawner = run_scope()
    mode = InstrumentMode(spawner.depth + 1, root_round=spawner.round, enclosed_as=spawner.filed_as)
    rescope(instrument=mode, filed_as=None)
    return ArmOf(CampaignWalks(campaigns, campaign_id), ctx.ruler)


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
