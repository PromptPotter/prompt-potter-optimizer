"""``inner_tasks.yaml``, the panel; a dataset that OWNS this file IS an outer dataset, and no name test recognises one."""

from __future__ import annotations

import itertools
import re
from collections.abc import Mapping
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from pydantic import ConfigDict, Field, ValidationError, model_validator

from promptpotter import connectors
from promptpotter.application.campaign_config import (
    DEFAULT_ORIGIN_BUDGET,
    CampaignConfig,
    DeterminismClamp,
    LivesConfig,
    OptimizationConfig,
    merge_node_overlays,
)
from promptpotter.application.datasets.authored import (
    dataset_campaign_path,
    read_campaign_config_file,
)
from promptpotter.application.optimizer_manifest import SelectedOptimizer, resolve_optimizer
from promptpotter.domain.l4.proxies import INNER_RESULT_KEY, OUTER_PROXY_KEYS
from promptpotter.domain.pipeline_schema import ManifestNodeOverlay, NodeKind, NodeRole
from promptpotter.domain.spend import SpendCeilings
from promptpotter.domain.strict_model import StrictModel
from promptpotter.infrastructure.store.dataset_access import (
    dataset_pipeline_path,
    readable_dataset_dir,
)
from promptpotter.infrastructure.store.io import read_yaml_optional
from promptpotter.shared.errors import CellUnscoreableError

if TYPE_CHECKING:
    from pathlib import Path

    from promptpotter.connectors.protocol import Connector
    from promptpotter.domain.sample import Sample
    from promptpotter.infrastructure.store.stores import Stores


class InnerBenchmarkConfig(StrictModel):
    """A SPEND bound only: a target score or default ladder rescales every fitness against an undeclared benchmark."""

    model_config = ConfigDict(frozen=True)

    n_samples_per_inner_round: int = Field(ge=1)
    # Explicit `null` ⇒ the per-round count; the inner-origin θ is the term BOTH arms of every paired delta subtract.
    n_samples_origin: int | None = Field(default=DEFAULT_ORIGIN_BUDGET, ge=1)
    max_inner_rounds: int = Field(ge=1)
    # Part of what a cell IS, so it enters the cell's identity.
    inner_nodes: dict[str, ManifestNodeOverlay] = Field(default_factory=dict)
    # Held OUT of the identity: how much evidence a cell buys, never what it is.
    inner_depth_nodes: dict[str, ManifestNodeOverlay] = Field(default_factory=dict)
    inner_lives: LivesConfig | None = None
    # At the file default, identical optimizer prompts generate different candidates and the swing swamps the outer proxy.
    inner_optimizer_temperature: float | None = Field(default=None, ge=0.0, le=2.0)


class InnerTask(StrictModel):
    """``id`` is the outer query; omitted overrides inherit the top-level benchmark and model."""

    model_config = ConfigDict(frozen=True)

    id: str = Field(min_length=1)
    inner_dataset_seed: int = Field(default=0, ge=0)
    n_inner_rounds: int | None = Field(default=None, ge=1)
    inner_dataset: str | None = None
    inner_model: str | None = None
    inner_provider: str | None = None


def _level_slug(value: object) -> str:
    """The generated cell id is the OUTER QUERY, so it stays byte-stable across runs."""
    return re.sub(r"[^A-Za-z0-9]+", "-", str(value)).strip("-") or "none"


class InnerTasks(StrictModel):
    model_config = ConfigDict(frozen=True)

    inner_benchmark: str = Field(min_length=1)
    inner_benchmark_config: InnerBenchmarkConfig
    tasks: list[InnerTask] = Field(min_length=1)

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
        griddable = set(InnerTask.model_fields) - {"id", "n_inner_rounds"}
        for key, levels in axes.items():
            if key == "n_inner_rounds":
                raise ValueError(
                    "`axes.n_inner_rounds` is not a grid axis — depth is CONTINUED on a cell, "
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
            {"id": "__".join(_level_slug(v) for v in combo), **dict(zip(keys, combo, strict=True))}
            for combo in itertools.product(*(axes[k] for k in keys))
        ]
        return raw

    def dataset_for(self, cell: InnerTask) -> str:
        return cell.inner_dataset or self.inner_benchmark

    @property
    def datasets(self) -> frozenset[str]:
        return frozenset(self.dataset_for(cell) for cell in self.tasks)

    @model_validator(mode="after")
    def _cells_are_distinct(self) -> InnerTasks:
        """`inner_campaign_id` is content-addressed: a twin CONTINUES the first or trips the one-producer guard."""
        seen_ids: set[str] = set()
        seen_specs: dict[tuple[object, ...], str] = {}
        for task in self.tasks:
            if task.id in seen_ids:
                raise ValueError(f"duplicate task id {task.id!r}")
            seen_ids.add(task.id)
            # `n_inner_rounds` is absent deliberately: two cells differing only in depth ARE one cell.
            key = (
                self.dataset_for(task),
                task.inner_dataset_seed,
                task.inner_model,
                task.inner_provider,
            )
            if (twin := seen_specs.get(key)) is not None:
                raise ValueError(
                    f"tasks {twin!r} and {task.id!r} resolve to the same inner campaign "
                    f"(dataset={key[0]!r}, seed={key[1]}) — give one a different seed, or "
                    "drop it; two names for one cell is not two cells. A different "
                    "`n_inner_rounds` does not separate them: depth is CONTINUED on one cell, "
                    "never forked into a second."
                )
            seen_specs[key] = task.id
        return self


# SUBTRACTED, so a new spec field defaults to identity: a needless fork wastes a run, a wrong continue corrupts one.
_DEPTH_FIELDS: frozenset[str] = frozenset(
    {"n_rounds", "depth_nodes", "lives", "n_samples", "n_samples_origin"}
)


class InnerTaskSpec(StrictModel):
    model_config = ConfigDict(frozen=True)

    inner_dataset: str
    optimizer_treatment: str
    seed: int
    n_samples: int
    n_samples_origin: int | None = None
    n_rounds: int
    nodes: dict[str, ManifestNodeOverlay] = Field(default_factory=dict)
    depth_nodes: dict[str, ManifestNodeOverlay] = Field(default_factory=dict)
    lives: LivesConfig | None = None
    inner_model: str | None = None
    inner_provider: str | None = None
    inner_optimizer_temperature: float | None = None

    def treatment(self) -> dict[str, Any]:
        return {k: v for k, v in self.model_dump(mode="json").items() if k not in _DEPTH_FIELDS}


assert set(InnerTaskSpec.model_fields) >= _DEPTH_FIELDS


def _recursion_connector() -> Connector:
    return connectors.get("promptpotter")


def inner_tasks_path(dataset_dir: Path) -> Path:
    """ONE spelling: a probe/loader drift skips the observation contract rather than raising."""

    return dataset_dir / _recursion_connector().experiment_file


def is_self_optimization(backend_type: str) -> bool:
    return connectors.registered().get(backend_type) is _recursion_connector()


def load_inner_tasks(path: Path) -> InnerTasks:
    raw = read_yaml_optional(path)
    if raw is None:
        raise CellUnscoreableError(
            f"{path} is missing — the inner benchmark, its sample count and its round cap are "
            "all declared there. There is no default to run.",
            spent={},
        )
    try:
        return InnerTasks.model_validate(raw)
    except ValidationError as exc:
        raise CellUnscoreableError(
            f"{path} does not declare a runnable panel: {exc}", spent={}
        ) from exc


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
    sampler = resolve_optimizer(optimizer, merged).sampler
    if n_samples is None or sampler.size_knob is None:
        return merged
    size = {sampler.name: ManifestNodeOverlay(config={sampler.size_knob: n_samples})}
    return merge_node_overlays(merged, size)


def _select_inner_optimizer(
    campaign_config: Mapping[str, Any],
    nodes: Mapping[str, ManifestNodeOverlay],
    depth_nodes: Mapping[str, ManifestNodeOverlay],
    *,
    n_samples: int | None,
) -> SelectedOptimizer:
    opt = campaign_config.get("optimization") or {}
    own = {
        node: ManifestNodeOverlay.model_validate(raw)
        for node, raw in (opt.get("nodes") or {}).items()
    }
    name = opt.get("optimizer", OptimizationConfig.model_fields["optimizer"].default)
    return resolve_optimizer(
        name, _inner_manifest_nodes(name, own, nodes, depth_nodes, n_samples=n_samples)
    )


# `problem_description` and `answer_format` are absent: they carry the injection slots and the output contract.
OUTER_PROMPT_FIELDS: tuple[str, ...] = ("persona", "task_intent", "instruction", "thinking_style")

_OUTER_KINDS = frozenset({NodeKind.LLM, NodeKind.GATEWAY})


@dataclass(frozen=True)
class InnerCell:
    campaign_config: Mapping[str, Any]
    pipeline: Mapping[str, Any] | None
    optimizer: SelectedOptimizer


@dataclass(frozen=True)
class InnerCells:
    panel: InnerTasks
    by_dataset: Mapping[str, InnerCell]
    # The digest without the panel's depth, so a deepened cell continues its campaign.
    treatment: str

    @property
    def optimizer(self) -> SelectedOptimizer:
        return next(iter(self.by_dataset.values())).optimizer

    @property
    def chain(self) -> list[str]:
        inner = self.optimizer
        return [n for n in inner.schema.pipelines["default"] if inner.node(n).kind in _OUTER_KINDS]

    @property
    def terminal(self) -> str:
        """The chain's last llm node; only config-less measurement nodes follow, so only a FULL match replays a row."""
        return [n for n in self.chain if self.optimizer.node(n).kind is NodeKind.LLM][-1]

    def pipeline(self) -> dict[str, Any]:
        inner = self.optimizer
        observed = [{"pipeline_key": key} for key in (INNER_RESULT_KEY, *OUTER_PROXY_KEYS)]
        nodes: dict[str, dict[str, Any]] = {}
        for node in inner.schema.declared_nodes:
            if node.kind is NodeKind.GATEWAY:
                nodes[node.name] = {"type": node.kind.value, "config": {}}
            elif node.kind is NodeKind.LLM:
                levers = inner.outer_levers(node.name)
                terminal = node.name == self.terminal
                nodes[node.name] = {
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
        walks = {
            name: [n for n in steps if n in nodes] for name, steps in inner.schema.pipelines.items()
        }
        return {
            "name": inner.name,
            "nodes": nodes,
            "pipelines": {k: v for k, v in walks.items() if v},
        }


def resolve_inner_cells(stores: Stores, panel: InnerTasks) -> InnerCells:
    """RAISES where two datasets run different optimizers: a cell would measure an arm on nodes it never touched."""
    cfg = panel.inner_benchmark_config
    by_dataset: dict[str, InnerCell] = {}
    runs: dict[str, str] = {}
    for name in sorted(panel.datasets):
        dataset_dir = readable_dataset_dir(stores, name)
        campaign = read_campaign_config_file(dataset_campaign_path(dataset_dir))
        by_dataset[name] = InnerCell(
            campaign_config=campaign,
            pipeline=read_yaml_optional(dataset_pipeline_path(dataset_dir)),
            optimizer=_select_inner_optimizer(
                campaign,
                cfg.inner_nodes,
                cfg.inner_depth_nodes,
                n_samples=cfg.n_samples_per_inner_round,
            ),
        )
        identity = _select_inner_optimizer(campaign, cfg.inner_nodes, {}, n_samples=None)
        runs[name] = identity.treatment().digest
    if any(run != runs[min(runs)] for run in runs.values()):
        named = {name: c.optimizer.name for name, c in by_dataset.items()}
        raise ValueError(
            f"the panel's inner datasets run different inner optimizers ({named}). One panel "
            "measures one optimizer configuration: give its cells datasets whose optimization "
            "agrees, or split the panel."
        )
    return InnerCells(panel=panel, by_dataset=by_dataset, treatment=runs[min(runs)])


def resolve_inner_task(cells: InnerCells, sample: Sample) -> InnerTaskSpec:
    panel = cells.panel
    cfg = panel.inner_benchmark_config
    try:
        cell = InnerTask.model_validate(sample.source_pin)
    except ValidationError as exc:
        raise CellUnscoreableError(
            f"{sample.query!r} is no cell of an inner panel: {exc}", spent={}
        ) from exc
    return InnerTaskSpec(
        inner_dataset=panel.dataset_for(cell),
        optimizer_treatment=cells.treatment,
        seed=cell.inner_dataset_seed,
        n_samples=cfg.n_samples_per_inner_round,
        n_samples_origin=cfg.n_samples_origin,
        n_rounds=cell.n_inner_rounds or cfg.max_inner_rounds,
        nodes=cfg.inner_nodes,
        depth_nodes=cfg.inner_depth_nodes,
        lives=cfg.inner_lives,
        inner_model=cell.inner_model,
        inner_provider=cell.inner_provider,
        inner_optimizer_temperature=cfg.inner_optimizer_temperature,
    )


def inner_instrument_config(
    spec: InnerTaskSpec,
    base: CampaignConfig,
    *,
    llm_node: str,
    n_scored: int,
) -> CampaignConfig:
    """Token and spend caps are CLEARED: one tripping on measured tokens truncates the trajectory nondeterministically."""
    opt_update: dict[str, Any] = {
        "max_rounds": spec.n_rounds,
        "lives": spec.lives,
        "ceiling": SpendCeilings(),
        # The origin gate's await is unbounded, and an instrument cannot ask a human anything.
        "origin_gate": "off",
        # Under 2PL θ is in units of 1/a and each cycle graduates on its OWN CV, so the panel would average a mixture of scales.
        "enable_2pl_graduation": False,
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
        ).model_copy(update={"temperature": spec.inner_optimizer_temperature, "seed": spec.seed})
    po: dict[str, Any] = {k: dict(v) for k, v in (base.pipeline_overlay or {}).items()}
    node = dict(po.get(llm_node, {}))
    node["seed"] = spec.seed
    if spec.inner_model:
        node["model"] = spec.inner_model
        if spec.inner_provider:
            node["provider"] = spec.inner_provider
    po[llm_node] = node
    return base.model_copy(
        update={
            "sp_budget_origin": n_scored,
            "dataset_split": None,
            "optimization": base.optimization.model_copy(update=opt_update),
            "pipeline_overlay": po,
        }
    )


__all__ = [
    "OUTER_PROMPT_FIELDS",
    "InnerBenchmarkConfig",
    "InnerCell",
    "InnerCells",
    "InnerTaskSpec",
    "inner_instrument_config",
    "is_self_optimization",
    "load_inner_tasks",
    "resolve_inner_cells",
    "resolve_inner_task",
]
