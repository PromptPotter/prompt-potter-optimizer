"""LEVI (arXiv 2605.09764): its node members and its runtime."""

from __future__ import annotations

import math
import random
from collections.abc import Mapping, Sequence
from typing import TYPE_CHECKING, Annotated, Any, ClassVar

import numpy as np
from pydantic import Field, model_validator

from promptpotter.application.bench.node_context import Calls, Children, Lineage, Replay, Target
from promptpotter.application.optimizers import nodes, paper_templates
from promptpotter.application.optimizers.descriptors import (
    DescriptorFeature,
    behaviour_descriptor,
    cell_objectives,
)
from promptpotter.application.optimizers.levi import prompts
from promptpotter.application.optimizers.levi.state import (
    LEVI_MANIFEST,
    DescriptorCalibration,
    DescriptorStats,
    EliteArchive,
    LeviCalibration,
    LeviElite,
)
from promptpotter.application.optimizers.nodes import RewriteKnobs
from promptpotter.application.optimizers.paper_templates import child
from promptpotter.config.paths import optimizers_root
from promptpotter.domain.campaign_config import Estimand, Knob, Scope
from promptpotter.domain.paired_reading import ArmKey
from promptpotter.domain.pipeline_schema import NodeKind
from promptpotter.domain.results import OptimizerFact
from promptpotter.domain.run_records import CheckpointKind
from promptpotter.domain.strict_model import StrictModel
from promptpotter.shared.statistics import greedy_column_subset, logistic

if TYPE_CHECKING:
    from pathlib import Path
    from types import ModuleType

    from promptpotter.application.bench.cycle import Cycle
    from promptpotter.application.bench.node_context import NodeContext
    from promptpotter.application.bench.resume_and_fork.replayers import (
        ReplayContext,
        Replayer,
    )
    from promptpotter.application.optimizer_manifest import SelectedOptimizer
    from promptpotter.application.optimizers.nodes import (
        CycleRead,
        MeasuredRound,
        Panel,
        PromptArity,
        Proposals,
        Selection,
    )
    from promptpotter.domain.arm_reading import DisplayMetric
    from promptpotter.domain.arms import Proposal
    from promptpotter.domain.opt_search_point import OptSearchPoint, VariationMode
    from promptpotter.domain.optimizer_state import StatePart
    from promptpotter.domain.results import RoundResult
    from promptpotter.domain.sample import Sample
    from promptpotter.domain.scoring import CellSheet


class LeviCheckpointKind(CheckpointKind):
    PROXY_SELECTED = "proxy_selected"


_LLOYD_ITERATIONS = 100


class ProxyCssKnobs(StrictModel):
    discovery: Annotated[int, Knob(Scope.POLICY, Estimand.SELECTION)] = Field(
        ge=1,
        description="The discovery set: the search-split cells, first in bank order, the "
        "calibration round scores every calibration prompt on and the proxy is chosen among.",
    )
    size: Annotated[int, Knob(Scope.POLICY, Estimand.SELECTION)] = Field(
        ge=1,
        description="K_proxy: the discovery-set cells every round after calibration scores its "
        "arms on, chosen once from the calibration prompts' rows.",
    )
    rank_weight: Annotated[float, Knob(Scope.POLICY, Estimand.SELECTION)] = Field(
        ge=0.0,
        description="r: the weight on rank faithfulness — the share of calibration-prompt pairs "
        "the proxy orders as the discovery set does.",
    )
    separation_weight: Annotated[float, Knob(Scope.POLICY, Estimand.SELECTION)] = Field(
        ge=0.0,
        description="s: the weight on separation — how widely the calibration prompts' scores "
        "spread on the chosen cells.",
    )
    redundancy_weight: Annotated[float, Knob(Scope.POLICY, Estimand.SELECTION)] = Field(
        ge=0.0,
        description="c: the penalty on redundancy — a cell's mean |Pearson r| with the cells "
        "already chosen.",
    )

    @model_validator(mode="after")
    def _proxy_fits_discovery(self) -> ProxyCssKnobs:
        if self.size > self.discovery:
            raise ValueError("proxy_css: size must be at most discovery, the set it is chosen from")
        return self


def choose_proxy(
    knobs: ProxyCssKnobs, rows_by_arm: Mapping[ArmKey, CellSheet], order: list[str]
) -> list[str]:
    graded = [cell_objectives(rows) for rows in rows_by_arm.values()]
    cells = [key for key in order if all(key in g for g in graded)]
    if not cells:
        raise ValueError("LEVI's calibration round graded no cell for every calibration prompt")
    picked = greedy_column_subset(
        [[g[c] for c in cells] for g in graded],
        knobs.size,
        rank_weight=knobs.rank_weight,
        separation_weight=knobs.separation_weight,
        redundancy_weight=knobs.redundancy_weight,
    )
    return [cells[j] for j in sorted(picked)]


class ProxyCss(nodes.Member):
    """The same cells in the same order every round, resume and fork."""

    name: ClassVar[str] = "proxy_css"
    kind: ClassVar[NodeKind] = NodeKind.SAMPLER
    knobs: ClassVar[type[StrictModel]] = ProxyCssKnobs
    reads: ClassVar[frozenset[type[StatePart]]] = frozenset({DescriptorCalibration})
    size_knob: ClassVar[str | None] = None

    def round_cells(self, knobs: ProxyCssKnobs, search: int) -> int:
        # The proxy is chosen among the discovery set, which the search split must hold whole.
        return knobs.size if knobs.discovery <= search else 0

    def draw(self, ctx: NodeContext, pool: list[Sample]) -> Panel:
        calibration = ctx.slice(DescriptorCalibration).calibration
        if calibration is None:
            knobs = ctx.knobs(ProxyCssKnobs)
            if knobs.discovery > len(pool):
                raise ValueError(
                    f"proxy_css: a discovery set of {knobs.discovery} cells needs a search split "
                    f"that large, and this one holds {len(pool)}"
                )
            discovery = pool[: knobs.discovery]
            return nodes.Panel(cells=discovery, order=list(discovery), block_size=len(discovery))
        by_key = {s.key: s for s in pool}
        if missing := [key for key in calibration.proxy if key not in by_key]:
            raise ValueError(f"proxy_css: {len(missing)} proxy cells left the search split")
        cells = [by_key[key] for key in calibration.proxy]
        return nodes.Panel(cells=cells, order=list(cells), block_size=len(cells))


def _lloyd(points: np.ndarray, k: int, rng: np.random.Generator) -> tuple[np.ndarray, np.ndarray]:
    """A cluster left empty keeps its centre."""
    centres = points[rng.choice(len(points), size=k, replace=False)].copy()
    labels = np.full(len(points), -1)
    for _ in range(_LLOYD_ITERATIONS):
        distances = ((points[:, None, :] - centres[None, :, :]) ** 2).sum(axis=2)
        moved = distances.argmin(axis=1)
        if np.array_equal(moved, labels):
            break
        labels = moved
        for c in range(k):
            if (members := points[labels == c]).size:
                centres[c] = members.mean(axis=0)
    return centres, labels


def _generator(ctx: NodeContext) -> np.random.Generator:
    return np.random.default_rng(ctx.read(Replay).rng().getrandbits(64))


def _welford(stats: DescriptorStats, x: Sequence[float]) -> DescriptorStats:
    count = stats.count + 1
    mean, m2 = list(stats.mean), list(stats.m2)
    for d, value in enumerate(x):
        delta = value - mean[d]
        mean[d] += delta / count
        m2[d] += delta * (value - mean[d])
    return DescriptorStats(count=count, mean=mean, m2=m2)


def _normalized(stats: DescriptorStats, x: Sequence[float]) -> list[float]:
    out = []
    for d, value in enumerate(x):
        sd = math.sqrt(stats.m2[d] / stats.count)
        z = (value - stats.mean[d]) / sd if sd > 0.0 else 0.0
        out.append(logistic(z))
    return out


def _nearest(centroids: Sequence[Sequence[float]], x: Sequence[float]) -> int:
    return int(((np.asarray(centroids) - np.asarray(x)) ** 2).sum(axis=1).argmin())


class MapElitesKnobs(StrictModel):
    centroids: Annotated[int, Knob(Scope.POLICY, Estimand.SEARCH)] = Field(
        ge=1,
        description="The Voronoi cells the archive partitions descriptor space into; each keeps "
        "only the best individual mapped to it.",
    )
    cvt_samples: Annotated[int, Knob(Scope.POLICY, Estimand.SEARCH)] = Field(
        ge=1,
        description="The uniform draws inside the calibration prompts' descriptor bounds that "
        "k-means places the centroids among; at least `centroids`.",
    )
    descriptors: Annotated[list[DescriptorFeature], Knob(Scope.POLICY, Estimand.SEARCH)] = Field(
        min_length=1,
        description="What places an arm in the archive: its scored prompt's length "
        "(`target_prompt_chars`) and its objective on each proxy cell (`cell_objectives`).",
    )

    @model_validator(mode="after")
    def _draws_cover_centroids(self) -> MapElitesKnobs:
        # Checked where the manifest resolves, before the calibration round is paid.
        if self.cvt_samples < self.centroids:
            raise ValueError("map_elites: cvt_samples must be at least centroids")
        return self


def _calibrate(
    ctx: NodeContext,
    offered: list[tuple[ArmKey, OptSearchPoint, CellSheet]],
    order: list[str],
) -> LeviCalibration:
    """Alg. 1 lines 8-14."""
    proxy_knobs = ctx.knobs(ProxyCssKnobs)
    knobs = ctx.knobs(MapElitesKnobs)
    rows_by_arm = {key: rows for key, _, rows in offered}
    proxy = choose_proxy(proxy_knobs, rows_by_arm, order)
    ctx.read(Replay).decide(
        LeviCheckpointKind.PROXY_SELECTED,
        {
            "calibration_ids": list(rows_by_arm),
            "order": order,
            "round_num": ctx.round_num,
            **proxy_knobs.model_dump(),
        },
        proxy,
    )
    placed = [
        d
        for _, _, rows in offered
        if (d := behaviour_descriptor(rows, proxy, knobs.descriptors)) is not None
    ]
    if not placed:
        raise ValueError("LEVI's calibration round placed no calibration prompt on the proxy")
    stats = DescriptorStats(count=0, mean=[0.0] * len(placed[0]), m2=[0.0] * len(placed[0]))
    for d in placed:
        stats = _welford(stats, d)
    seen = np.asarray([_normalized(stats, d) for d in placed])
    if knobs.centroids > 1 and (seen.min(axis=0) == seen.max(axis=0)).all():
        raise ValueError(
            f"LEVI's calibration placed {len(placed)} prompt(s) on one descriptor point: every "
            "centroid would land there and the archive hold one cell"
        )
    rng = _generator(ctx)
    draws = rng.uniform(seen.min(axis=0), seen.max(axis=0), size=(knobs.cvt_samples, seen.shape[1]))
    centres, _ = _lloyd(draws, knobs.centroids, rng)
    return LeviCalibration(proxy=proxy, centroids=centres.tolist(), stats=stats)


class MapElites(nodes.Member):
    name: ClassVar[str] = "map_elites"
    kind: ClassVar[NodeKind] = NodeKind.SELECTOR
    knobs: ClassVar[type[StrictModel]] = MapElitesKnobs
    reads: ClassVar[frozenset[type[StatePart]]] = frozenset({DescriptorCalibration, EliteArchive})
    writes: ClassVar[frozenset[type[StatePart]]] = frozenset({DescriptorCalibration, EliteArchive})
    sees: ClassVar[frozenset[type[CycleRead]]] = frozenset({Lineage, Replay})
    consults: ClassVar[frozenset[type]] = frozenset({ProxyCss})
    elects_on: ClassVar[DisplayMetric] = "composite"
    elects_partial: ClassVar[bool] = False

    def parent_cells(
        self,
        _ctx: NodeContext,
        panel: Panel,
        _rows: Mapping[ArmKey, CellSheet],
    ) -> list[Sample]:
        return panel.cells

    def select(self, ctx: NodeContext, measured: MeasuredRound) -> Selection:
        knobs = ctx.knobs(MapElitesKnobs)
        offered = [
            (key, measured.arms[key].individual, measured.rows[key]) for key in measured.electable
        ]
        calibration = ctx.slice(DescriptorCalibration).calibration
        if calibration is None:
            # The origin is the fifth calibration prompt (App. A).
            parent = measured.parent
            offered.insert(0, (parent.key, parent.individual, parent.rows))
            order = [cell.key for cell in measured.parent.rows]
            calibration = _calibrate(ctx, offered, order)
        stats = calibration.stats
        elites = {e.cell: e for e in ctx.slice(EliteArchive).elites}
        placed = kept = 0
        arms = set(measured.arms)
        placed_scores: dict[ArmKey, float] = {}
        # Alg. 2's TRYINSERT, in walk order.
        for key, ind, rows in offered:
            graded = cell_objectives(rows)
            x = behaviour_descriptor(rows, calibration.proxy, knobs.descriptors)
            if x is None or any(c not in graded for c in calibration.proxy):
                continue
            placed += 1
            stats = _welford(stats, x)
            cell = _nearest(calibration.centroids, _normalized(stats, x))
            score = sum(graded[c] for c in calibration.proxy) / len(calibration.proxy)
            if key in arms:
                placed_scores[key] = score
            if cell not in elites or score > elites[cell].score:
                kept += 1
                elites[cell] = LeviElite(
                    cell=cell, score=score, round=ctx.round_num, individual_id=ind.id
                )
        calibration = calibration.model_copy(update={"stats": stats})
        archive = sorted(elites.values(), key=lambda e: e.cell)
        lineage = ctx.read(Lineage)
        held = {ind.id: ind for ind in (*lineage.population, *(ind for _, ind, _ in offered))}
        top = max((e.score for e in archive), default=None)
        leaders = {e.individual_id for e in archive if e.score == top}
        # A tie never advances: the incumbent holds, then the first arm walked.
        walked = [key for key, ind, _ in offered if ind.id in leaders]
        incumbent_leads = lineage.parent.id in leaders
        if leaders and not walked and not incumbent_leads:
            raise ValueError("map_elites: the best elite is neither the incumbent nor this round's")
        selected = walked[0] if walked and not incumbent_leads else None
        verdict = (
            f"placed {placed} of {len(offered)} arms, {kept} into an empty or outscored cell; the "
            f"archive holds {len(archive)} of {len(calibration.centroids)} cells"
            + (f", best {top:.3f}" if top is not None else "")
        )
        ctx.put(DescriptorCalibration(calibration=calibration))
        ctx.put(EliteArchive(elites=archive))
        return nodes.Selection(
            selected=selected,
            leading=selected or max(placed_scores, key=placed_scores.__getitem__, default=None),
            verdict_reason=verdict,
            population=[held[cid] for cid in dict.fromkeys(e.individual_id for e in archive)],
        )


class LeviParadigmShiftKnobs(RewriteKnobs):
    interval: Annotated[int, Knob(Scope.POLICY, Estimand.SEARCH)] = Field(
        ge=1,
        description="One paradigm shift every this many evaluations: each round after calibration "
        "is one paradigm shift and `interval - 1` refinements.",
    )
    n_clusters: Annotated[int, Knob(Scope.POLICY, Estimand.SEARCH)] = Field(
        ge=1,
        description="k: the clusters k-means cuts the occupied cells into; each hands its best "
        "elite to the paradigm shift as a representative.",
    )


class LeviSeedKnobs(RewriteKnobs):
    n_diverse_seeds: Annotated[int, Knob(Scope.POLICY, Estimand.SEARCH)] = Field(
        ge=1,
        description="The seeds the calibration round asks for, each shown every prompt before it.",
    )


class LeviSeed(nodes.Member):
    """The seed pass `M_l.DIVERSESEED` (Alg. 1): the calibration round's arms."""

    name: ClassVar[str] = "levi_seed"
    kind: ClassVar[NodeKind] = NodeKind.LLM
    prompt_arity: ClassVar[PromptArity] = "one"
    opens: ClassVar[bool] = True
    variation_mode: ClassVar[VariationMode] = "llm"
    knobs: ClassVar[type[StrictModel]] = LeviSeedKnobs
    sees: ClassVar[frozenset[type[CycleRead]]] = frozenset({Calls, Children, Lineage, Target})

    async def propose(self, ctx: NodeContext, _panel: Panel, _proposals: Proposals) -> Proposals:
        origin = ctx.read(Lineage).origin
        node, calls = ctx.read(Target).prompt_node, ctx.read(Calls)
        shown: list[tuple[OptSearchPoint, float | None]] = [(origin, None)]
        seeds: list[Proposal] = []
        for i in range(ctx.knobs(LeviSeedKnobs).n_diverse_seeds):
            raw = await calls.ask(prompts.paradigm_shift_prompt(ctx, shown), i)
            proposal = child(ctx, node, [origin], raw, f"diverse seed {i + 1}")
            seeds.append(proposal)
            if not proposal.validation_failures:
                shown.append((proposal.individual, None))
        return nodes.Proposals(seeds)


class LeviParadigmShift(nodes.Member):
    """The large model's route (§3.2, App. F)."""

    name: ClassVar[str] = "levi_paradigm_shift"
    kind: ClassVar[NodeKind] = NodeKind.LLM
    prompt_arity: ClassVar[PromptArity] = "one"
    opens: ClassVar[bool] = False
    variation_mode: ClassVar[VariationMode] = "llm"
    knobs: ClassVar[type[StrictModel]] = LeviParadigmShiftKnobs
    reads: ClassVar[frozenset[type[StatePart]]] = frozenset({DescriptorCalibration, EliteArchive})
    sees: ClassVar[frozenset[type[CycleRead]]] = frozenset(
        {Calls, Children, Lineage, Replay, Target}
    )

    async def propose(self, ctx: NodeContext, _panel: Panel, proposals: Proposals) -> Proposals:
        n_clusters = ctx.knobs(LeviParadigmShiftKnobs).n_clusters
        shift = await self._shift(ctx, n_clusters, len(proposals.proposals))
        return nodes.Proposals([*proposals.proposals, shift])

    async def _shift(self, ctx: NodeContext, n_clusters: int, idx: int) -> Proposal:
        calibration = ctx.slice(DescriptorCalibration).calibration
        if calibration is None:
            raise ValueError(
                "levi_paradigm_shift: no calibration stands, so no elite to shift from"
            )
        elites = ctx.slice(EliteArchive).elites
        centroids = np.asarray(calibration.centroids)
        occupied = np.asarray([centroids[e.cell] for e in elites])
        k = min(n_clusters, len(elites))
        _, labels = _lloyd(occupied, k, _generator(ctx))
        clusters: dict[int, list[LeviElite]] = {}
        for elite, label in zip(elites, labels.tolist(), strict=True):
            clusters.setdefault(label, []).append(elite)
        representatives = sorted(
            (max(members, key=lambda e: e.score) for members in clusters.values()),
            key=lambda e: -e.score,
        )
        held = {ind.id: ind for ind in ctx.read(Lineage).population}
        regions = [held[e.individual_id] for e in representatives]
        prompt = prompts.paradigm_shift_prompt(
            ctx, [(ind, e.score) for ind, e in zip(regions, representatives, strict=True)]
        )
        return child(
            ctx,
            ctx.read(Target).prompt_node,
            regions,
            await ctx.read(Calls).ask(prompt, idx),
            f"paradigm shift from {len(representatives)} regions",
        )


class LeviRefineKnobs(RewriteKnobs):
    sampler_temperatures: Annotated[list[float], Knob(Scope.POLICY, Estimand.SEARCH)] = Field(
        min_length=1,
        description="T: the softmax temperatures a refinement's parent is drawn from the elites "
        "under, exp(score / T), one picked uniformly per call — lower favours the best.",
    )
    n_inspirations: Annotated[int, Knob(Scope.POLICY, Estimand.SEARCH)] = Field(
        ge=0, description="Other elites a refinement is shown beside its parent."
    )
    inspiration_drop: Annotated[float, Knob(Scope.POLICY, Estimand.SEARCH)] = Field(
        ge=0.0, le=1.0, description="How often a refinement is shown no inspiration at all."
    )
    feedback_failures: Annotated[int, Knob(Scope.POLICY, Estimand.SEARCH)] = Field(
        ge=0,
        description="The parent's failed proxy cells a refinement is shown, drawn at random.",
    )


def _softmax_pick(elites: Sequence[LeviElite], temperature: float, rng: random.Random) -> LeviElite:
    top = max(e.score for e in elites)
    weights = [math.exp((e.score - top) / temperature) for e in elites]
    return rng.choices(list(elites), weights=weights)[0]


class LeviRefine(nodes.Member):
    """The small model's route (§3.2, Alg. 2 lines 9-10)."""

    name: ClassVar[str] = "levi_refine"
    kind: ClassVar[NodeKind] = NodeKind.LLM
    prompt_arity: ClassVar[PromptArity] = "one"
    opens: ClassVar[bool] = True
    variation_mode: ClassVar[VariationMode] = "llm"
    knobs: ClassVar[type[StrictModel]] = LeviRefineKnobs
    reads: ClassVar[frozenset[type[StatePart]]] = frozenset({DescriptorCalibration, EliteArchive})
    sees: ClassVar[frozenset[type[CycleRead]]] = frozenset(
        {Calls, Children, Lineage, Replay, Target}
    )
    consults: ClassVar[frozenset[type]] = frozenset({LeviParadigmShift})

    async def propose(self, ctx: NodeContext, _panel: Panel, _proposals: Proposals) -> Proposals:
        calibration = ctx.slice(DescriptorCalibration).calibration
        elites = ctx.slice(EliteArchive).elites
        shift = ctx.knobs(LeviParadigmShiftKnobs)
        asked = [self._refinement(ctx, calibration, elites, i) for i in range(shift.interval - 1)]
        replies = await ctx.read(Calls).ask_each({i: p for i, (_, p) in enumerate(asked)})
        node = ctx.read(Target).prompt_node
        return nodes.Proposals(
            [
                child(ctx, node, [parent], raw, f"refine {parent.id[:6]}")
                for (parent, _), raw in zip(asked, replies, strict=True)
            ]
        )

    def _refinement(
        self,
        ctx: NodeContext,
        calibration: LeviCalibration | None,
        elites: Sequence[LeviElite],
        idx: int,
    ) -> tuple[OptSearchPoint, str]:
        assert calibration is not None
        knobs = ctx.knobs(LeviRefineKnobs)
        rng = ctx.read(Replay).rng(f":{idx}")
        parent = _softmax_pick(elites, rng.choice(knobs.sampler_temperatures), rng)
        others = [e for e in elites if e is not parent]
        inspirations = (
            []
            if rng.random() < knobs.inspiration_drop
            else rng.sample(others, min(knobs.n_inspirations, len(others)))
        )
        proxy = set(calibration.proxy)
        lineage = ctx.read(Lineage)
        measured_in = next(rr for rr in lineage.rounds if rr.round == parent.round)
        rows = measured_in.sheet_of(parent.individual_id)
        held = {ind.id: ind for ind in lineage.population}
        prompt = prompts.refine_prompt(
            ctx,
            parent=(held[parent.individual_id], parent.score),
            parent_rows=[cell for cell in rows if cell.facts.sample_key in proxy],
            inspirations=[(held[e.individual_id], e.score) for e in inspirations],
            n_failures=knobs.feedback_failures,
            rng=rng,
        )
        return held[parent.individual_id], prompt


def _replay_proxy_selected(
    ctx: ReplayContext, inputs_ref: dict[str, Any], _data: dict[str, Any]
) -> list[str]:
    return choose_proxy(
        ProxyCssKnobs.model_validate({k: inputs_ref[k] for k in ProxyCssKnobs.model_fields}),
        {cid: ctx.round_data.sheet_of(cid) for cid in inputs_ref["calibration_ids"]},
        list(inputs_ref["order"]),
    )


_PROPOSERS = (LeviSeed.name, LeviParadigmShift.name, LeviRefine.name)


class LeviRuntime(nodes.OptimizerRuntime):
    name: ClassVar[str] = LEVI_MANIFEST
    manifest_dir: ClassVar[Path] = optimizers_root() / LEVI_MANIFEST
    prompt_sources: ClassVar[tuple[ModuleType, ...]] = (paper_templates, prompts)
    replayers: ClassVar[Mapping[str, Replayer]] = {
        LeviCheckpointKind.PROXY_SELECTED: _replay_proxy_selected,
    }

    def round_packages(self, _cycle: Cycle, rounds: list[RoundResult]) -> dict[int, dict[str, str]]:
        return nodes.rows_read_packages(rounds, _PROPOSERS)

    def arms(self, selected: SelectedOptimizer) -> int:
        seeds = selected.knobs_of(LeviSeed.name, LeviSeedKnobs).n_diverse_seeds
        return max(
            seeds, selected.knobs_of(LeviParadigmShift.name, LeviParadigmShiftKnobs).interval
        )

    def round_cells_ceiling(self, selected: SelectedOptimizer, search: int) -> int:
        shift = selected.knobs_of(LeviParadigmShift.name, LeviParadigmShiftKnobs)
        seeds = selected.knobs_of(LeviSeed.name, LeviSeedKnobs).n_diverse_seeds
        discovery = selected.knobs_of(ProxyCss.name, ProxyCssKnobs).discovery
        proxy = selected.round_cells(search)
        if not proxy:
            return 0
        return max((seeds + 1) * discovery, (shift.interval + 1) * proxy)

    def round_facts(
        self, _selected: SelectedOptimizer, round_result: RoundResult
    ) -> list[OptimizerFact]:
        state = round_result.closed_state
        if (calibration := state.slice(DescriptorCalibration).calibration) is None:
            return []
        elites, k = len(state.slice(EliteArchive).elites), len(calibration.proxy)
        # Calibration is round 1, and every round after it runs one paradigm shift.
        shifts = round_result.round - 1
        return [
            OptimizerFact(
                key="archive",
                label="Archive",
                text=f"{elites} of {len(calibration.centroids)} cells",
                value=elites,
                kind="stat",
            ),
            OptimizerFact(key="proxy", label="Proxy K", text=str(k), value=k, kind="stat"),
            OptimizerFact(
                key="paradigm_shifts",
                label="Paradigm shifts",
                text=str(shifts),
                value=shifts,
                kind="stat",
            ),
        ]


MEMBERS = (ProxyCss(), LeviSeed(), LeviParadigmShift(), LeviRefine(), MapElites())
RUNTIME = LeviRuntime()
