"""LEVI's node implementations (arXiv 2605.09764), each registered under the node name its
manifest uses, and its runtime, registered under the manifest's."""

from __future__ import annotations

import asyncio
import math
import random
from collections.abc import Mapping, Sequence
from dataclasses import replace
from typing import TYPE_CHECKING, Annotated, Any, ClassVar, cast

import numpy as np
from pydantic import Field, model_validator

from promptpotter.application.bench.resume_and_fork.decisions import (
    GatingMode,
    record_decision,
)
from promptpotter.application.campaign_config import Estimand, Knob, Scope
from promptpotter.application.optimizers import nodes, paper_templates
from promptpotter.application.optimizers.descriptors import (
    DescriptorFeature,
    behaviour_descriptor,
    cell_objectives,
)
from promptpotter.application.optimizers.levi import operators
from promptpotter.application.optimizers.levi.state import (
    LEVI_MANIFEST,
    DescriptorStats,
    LeviCalibration,
    LeviElite,
    LeviRoundState,
    LeviState,
    levi_state,
)
from promptpotter.application.optimizers.paper_templates import ask, marked, unmarked, walk_rng
from promptpotter.config.paths import optimizers_root
from promptpotter.domain.opt_search_point import OptSearchPoint, node_source
from promptpotter.domain.pipeline_schema import NodeKind
from promptpotter.domain.results import CandidateProposal, OptimizerFact, candidate_label
from promptpotter.domain.run_records import CandidateMintedRecord, CheckpointKind
from promptpotter.domain.strict_model import StrictModel
from promptpotter.shared.statistics import greedy_column_subset

if TYPE_CHECKING:
    from pathlib import Path
    from types import ModuleType

    from pydantic import BaseModel

    from promptpotter.application.bench.cycle import Cycle
    from promptpotter.application.bench.resume_and_fork.replayers import (
        ReplayContext,
        Replayer,
    )
    from promptpotter.application.campaign_config import CampaignConfig
    from promptpotter.application.initialization.session import Session
    from promptpotter.application.optimizer_manifest import SelectedOptimizer
    from promptpotter.application.optimizers.nodes import (
        Measured,
        Panel,
        Population,
        ReviewReading,
        RoundContext,
        Selection,
    )
    from promptpotter.domain.cycle_paths import CycleHop
    from promptpotter.domain.results import RoundResult
    from promptpotter.domain.sample import Sample
    from promptpotter.infrastructure.store.campaign_store.store import CampaignStore

__all__ = [
    "MEMBERS",
    "RUNTIME",
    "LeviCheckpointKind",
    "LeviParadigmShiftKnobs",
    "LeviRefineKnobs",
    "MapElitesKnobs",
    "ProxyCssKnobs",
    "choose_proxy",
]


class LeviCheckpointKind(CheckpointKind):
    """Decisions LEVI's members take: the proxy benchmark its calibration round chooses."""

    PROXY_SELECTED = "proxy_selected"


# Lloyd's iterations before a k-means stops short of a fixed point.
_LLOYD_ITERATIONS = 100


class ProxyCssKnobs(StrictModel):
    size: Annotated[int, Knob(Scope.POLICY, Estimand.SELECTION)] = Field(
        ge=1,
        description="K_proxy: the search-pool cells every round after calibration scores its arms "
        "on, chosen once from the calibration prompts' rows.",
    )
    rank_weight: Annotated[float, Knob(Scope.POLICY, Estimand.SELECTION)] = Field(
        ge=0.0,
        description="r: the weight on rank faithfulness — the share of calibration-prompt pairs "
        "the proxy orders as the whole pool does.",
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


def choose_proxy(
    knobs: ProxyCssKnobs, rows_by_arm: Mapping[str, Sequence[Mapping[str, Any]]], order: list[str]
) -> list[str]:
    """The proxy off the calibration prompts' rows, over the cells every one of them graded, in
    ``order`` — the calibration panel's."""
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


class ProxyCss:
    """LEVI's panel: the whole search pool in the calibration round, the proxy it chose ever after —
    the same cells in the same order every round, resume and fork."""

    name: ClassVar[str] = "proxy_css"
    kind: ClassVar[NodeKind] = NodeKind.SAMPLER
    knobs: ClassVar[type[StrictModel]] = ProxyCssKnobs
    couplings: ClassVar[tuple[nodes.MemberCoupling, ...]] = ()
    # K_proxy is the paper's; the calibration round draws the whole pool.
    size_knob: ClassVar[str | None] = None

    def draws(self, selected: SelectedOptimizer, pool: int) -> int:
        return min(cast("ProxyCssKnobs", selected.knobs(self.name)).size, pool)

    def draw(self, ctx: RoundContext, pool: list[Sample]) -> Panel:
        calibration = levi_state(ctx.state).calibration
        if calibration is None:
            return nodes.Panel(cells=list(pool), order=list(pool), block_size=len(pool))
        by_key = {s.key: s for s in pool}
        if missing := [key for key in calibration.proxy if key not in by_key]:
            raise ValueError(f"proxy_css: {len(missing)} proxy cells left the search pool")
        cells = [by_key[key] for key in calibration.proxy]
        return nodes.Panel(cells=cells, order=list(cells), block_size=len(cells))


def _lloyd(points: np.ndarray, k: int, rng: np.random.Generator) -> tuple[np.ndarray, np.ndarray]:
    """k-means from ``k`` of ``points`` drawn at random; a cluster left empty keeps its centre."""
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


def _generator(cycle: Cycle, round_num: int, node: str) -> np.random.Generator:
    return np.random.default_rng(walk_rng(cycle, round_num, node).getrandbits(64))


def _welford(stats: DescriptorStats, x: Sequence[float]) -> DescriptorStats:
    count = stats.count + 1
    mean, m2 = list(stats.mean), list(stats.m2)
    for d, value in enumerate(x):
        delta = value - mean[d]
        mean[d] += delta / count
        m2[d] += delta * (value - mean[d])
    return DescriptorStats(count=count, mean=mean, m2=m2)


def _normalized(stats: DescriptorStats, x: Sequence[float]) -> list[float]:
    """z-scored on the running statistics, then squashed into [0, 1] by a sigmoid (§3)."""
    out = []
    for d, value in enumerate(x):
        sd = math.sqrt(stats.m2[d] / stats.count)
        z = (value - stats.mean[d]) / sd if sd > 0.0 else 0.0
        out.append(1.0 / (1.0 + math.exp(-z)))
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
    ctx: RoundContext,
    offered: list[tuple[OptSearchPoint, Sequence[Mapping[str, Any]]]],
    order: list[str],
) -> LeviCalibration:
    """Alg. 1 lines 8-14: the proxy off the calibration rows, the running statistics over their
    descriptors, and the centroids k-means places inside the bounds those descriptors span."""
    cycle = ctx.cycle
    proxy_knobs = cast("ProxyCssKnobs", cycle.optimizer.knobs(ProxyCss.name))
    knobs = cast("MapElitesKnobs", cycle.optimizer.knobs(MapElites.name))
    rows_by_arm = {ind.lineage.id: rows for ind, rows in offered}
    proxy = choose_proxy(proxy_knobs, rows_by_arm, order)
    record_decision(
        cycle.pending_decisions,
        LeviCheckpointKind.PROXY_SELECTED,
        {
            "calibration_ids": list(rows_by_arm),
            "order": order,
            "round_num": ctx.round_num,
            **proxy_knobs.model_dump(),
        },
        proxy,
        node=ProxyCss.name,
        round=ctx.round_num,
    )
    placed = [
        d
        for _, rows in offered
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
    rng = _generator(cycle, ctx.round_num, MapElites.name)
    draws = rng.uniform(seen.min(axis=0), seen.max(axis=0), size=(knobs.cvt_samples, seen.shape[1]))
    centres, _ = _lloyd(draws, knobs.centroids, rng)
    return LeviCalibration(proxy=proxy, centroids=centres.tolist(), stats=stats)


class MapElites:
    """LEVI's solution database: a CVT-MAP-Elites archive. Each arm the round measured is placed by
    its descriptor and kept where its cell is empty or it outscores the cell's elite; the round
    advances when the best elite is not the incumbent."""

    name: ClassVar[str] = "map_elites"
    kind: ClassVar[NodeKind] = NodeKind.SELECTOR
    knobs: ClassVar[type[StrictModel]] = MapElitesKnobs
    couplings: ClassVar[tuple[nodes.MemberCoupling, ...]] = ()
    stamps_theta: ClassVar[bool] = False
    reads_parent: ClassVar[bool] = True

    def select(self, ctx: RoundContext, measured: Measured, population: Population) -> Selection:
        cycle = ctx.cycle
        state = levi_state(ctx.state)
        knobs = cast("MapElitesKnobs", cycle.optimizer.knobs(self.name))
        offered: list[tuple[OptSearchPoint, Sequence[Mapping[str, Any]]]] = [
            (ind, measured.rows[ind.lineage.id]) for ind in measured.electable
        ]
        calibration = state.calibration
        if calibration is None:
            # The incumbent is the origin, scored on the whole pool beside the seeds: the fifth
            # calibration prompt (App. A).
            offered.insert(0, (measured.parent.opt_sp, measured.parent_rows))
            order = [str(r["sample_key"]) for r in measured.parent_rows]
            calibration = _calibrate(ctx, offered, order)
        stats = calibration.stats
        elites = {e.cell: e for e in state.elites}
        placed = kept = 0
        # Alg. 2's TRYINSERT, in walk order.
        for ind, rows in offered:
            graded = cell_objectives(rows)
            x = behaviour_descriptor(rows, calibration.proxy, knobs.descriptors)
            if x is None or any(c not in graded for c in calibration.proxy):
                continue
            placed += 1
            stats = _welford(stats, x)
            cell = _nearest(calibration.centroids, _normalized(stats, x))
            score = sum(graded[c] for c in calibration.proxy) / len(calibration.proxy)
            if cell not in elites or score > elites[cell].score:
                kept += 1
                elites[cell] = LeviElite(
                    cell=cell,
                    score=score,
                    round=ctx.round_num,
                    individual=ind.model_copy(deep=True),
                )
        calibration = calibration.model_copy(update={"stats": stats})
        archive = sorted(elites.values(), key=lambda e: e.cell)
        top = max((e.score for e in archive), default=None)
        leaders = {e.individual.lineage.id for e in archive if e.score == top}
        # A tie never advances: the incumbent holds against an equal score, and among this
        # round's arms the first walked leads.
        walked = [ind.lineage.id for ind, _ in offered if ind.lineage.id in leaders]
        if leaders and not walked and cycle.opt_sp.lineage.id not in leaders:
            raise ValueError("map_elites: the best elite is neither the incumbent nor this round's")
        selected_id = walked[0] if walked and cycle.opt_sp.lineage.id not in leaders else ""
        verdict = (
            f"placed {placed} of {len(offered)} arms, {kept} into an empty or outscored cell; the "
            f"archive holds {len(archive)} of {len(calibration.centroids)} cells"
            + (f", best {top:.3f}" if top is not None else "")
        )
        return nodes.Selection(
            selected_id=selected_id,
            scores=list(measured.scores),
            verdict_reason=verdict,
            optimizer_state=state.snapshot(
                population.optimizer_state.prompt_hashes,
                calibration=calibration,
                elites=archive,
                rounds_without_advance=0 if selected_id else state.rounds_without_advance + 1,
            ),
        )


class LeviParadigmShiftKnobs(StrictModel):
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
    n_diverse_seeds: Annotated[int, Knob(Scope.POLICY, Estimand.SEARCH)] = Field(
        ge=1,
        description="The seeds the calibration round asks for, each shown every prompt before it.",
    )


def _proposal(
    node: str, parents: list[OptSearchPoint], raw: str, changes: str
) -> CandidateProposal:
    text = marked(raw)
    child = OptSearchPoint.derive(
        parents,
        source=node_source(LEVI_MANIFEST, node),
        changes_description=changes,
        instruction=parents[0].instruction if text is None else text,
    )
    return CandidateProposal(
        opt_sp=child, validation_failures=[] if text is not None else [unmarked(node, raw)]
    )


class LeviParadigmShift:
    """The large model's route (§3.2, App. F): in the calibration round, the diverse seeds, each
    shown the ones before it; after it, one prompt unlike the best elite of each cluster of cells.
    The last proposer, so it mints the round's individuals onto the ledger."""

    name: ClassVar[str] = "levi_paradigm_shift"
    kind: ClassVar[NodeKind] = NodeKind.LLM
    knobs: ClassVar[type[StrictModel]] = LeviParadigmShiftKnobs
    couplings: ClassVar[tuple[nodes.MemberCoupling, ...]] = ()

    async def propose(
        self, ctx: RoundContext, panel: Panel, population: Population | None
    ) -> Population:
        if population is None:
            raise ValueError(
                "levi_paradigm_shift follows the refinements in LEVI's walk; a manifest walking "
                "it first hands it no population"
            )
        cycle = ctx.cycle
        state = levi_state(ctx.state)
        knobs = cast("LeviParadigmShiftKnobs", cycle.optimizer.knobs(self.name))
        offset = len(population.proposals)
        if state.calibration is None:
            added = await self._seeds(ctx, knobs.n_diverse_seeds, offset)
        else:
            added = [await self._shift(ctx, state, knobs.n_clusters, offset)]
        assert cycle.tracking.current_sp is not None
        base = cycle.tracking.current_sp.pipeline_params
        walked = replace(
            population,
            proposals=[*population.proposals, *added],
            individuals=[*population.individuals, *(p.opt_sp for p in added)],
            pipeline_params=[*population.pipeline_params, *([base] * len(added))],
        )
        if (ledger := cycle.session.state.ledger) is not None:
            for idx, ind in enumerate(walked.individuals):
                ledger.append(
                    CandidateMintedRecord(
                        round=ctx.round_num,
                        idx=idx,
                        candidate_id=ind.lineage.id,
                        parent_ids=list(ind.lineage.parent_ids),
                        label=candidate_label(ctx.round_num, idx),
                        changes_description=ind.lineage.changes_description,
                        source=ind.lineage.source,
                    )
                )
        return walked

    async def _seeds(self, ctx: RoundContext, n: int, offset: int) -> list[CandidateProposal]:
        cycle = ctx.cycle
        origin = cycle.origin_round.opt_sp
        assert origin is not None, "round 0 closes with the origin's individual"
        shown: list[tuple[OptSearchPoint, float | None]] = [(origin, None)]
        proposals: list[CandidateProposal] = []
        for i in range(n):
            prompt = operators.paradigm_shift_prompt(cycle, self.name, shown)
            raw = await ask(ctx, self.name, offset + i, prompt)
            proposal = _proposal(self.name, [origin], raw, f"diverse seed {i + 1}")
            proposals.append(proposal)
            if not proposal.validation_failures:
                shown.append((proposal.opt_sp, None))
        return proposals

    async def _shift(
        self, ctx: RoundContext, state: LeviState, n_clusters: int, idx: int
    ) -> CandidateProposal:
        cycle = ctx.cycle
        assert state.calibration is not None
        centroids = np.asarray(state.calibration.centroids)
        occupied = np.asarray([centroids[e.cell] for e in state.elites])
        k = min(n_clusters, len(state.elites))
        _, labels = _lloyd(occupied, k, _generator(cycle, ctx.round_num, self.name))
        clusters: dict[int, list[LeviElite]] = {}
        for elite, label in zip(state.elites, labels.tolist(), strict=True):
            clusters.setdefault(label, []).append(elite)
        representatives = sorted(
            (max(members, key=lambda e: e.score) for members in clusters.values()),
            key=lambda e: -e.score,
        )
        prompt = operators.paradigm_shift_prompt(
            cycle, self.name, [(e.individual, e.score) for e in representatives]
        )
        return _proposal(
            self.name,
            [e.individual for e in representatives],
            await ask(ctx, self.name, idx, prompt),
            f"paradigm shift from {len(representatives)} regions",
        )


class LeviRefineKnobs(StrictModel):
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


def _elite_rows(cycle: Cycle, elite: LeviElite) -> list[dict[str, Any]]:
    rr = next(rr for rr in cycle.rounds if rr.round == elite.round)
    cid = elite.individual.lineage.id
    return (
        rr.all_candidate_results[cid]
        if cid in rr.all_candidate_results
        else (rr.reference_results[cid])
    )


class LeviRefine:
    """The small model's route (§3.2, Alg. 2 lines 9-10): `interval - 1` single-parent rewrites
    a round, each parent drawn from the elites by softmax."""

    name: ClassVar[str] = "levi_refine"
    kind: ClassVar[NodeKind] = NodeKind.LLM
    knobs: ClassVar[type[StrictModel]] = LeviRefineKnobs
    couplings: ClassVar[tuple[nodes.MemberCoupling, ...]] = ()

    async def propose(
        self, ctx: RoundContext, panel: Panel, population: Population | None
    ) -> Population:
        if population is not None:
            raise ValueError(
                "levi_refine opens LEVI's walk; a manifest walking it after another proposer "
                "hands it offspring it would discard"
            )
        cycle = ctx.cycle
        state = levi_state(ctx.state)
        shift = cast("LeviParadigmShiftKnobs", cycle.optimizer.knobs(LeviParadigmShift.name))
        # The calibration round refines nothing: its seeds are the evaluations it spends.
        n = 0 if state.calibration is None else shift.interval - 1
        children = list(await asyncio.gather(*(self._refine(ctx, state, i) for i in range(n))))
        assert cycle.tracking.current_sp is not None
        return nodes.Population(
            proposals=children,
            individuals=[c.opt_sp for c in children],
            pipeline_params=[cycle.tracking.current_sp.pipeline_params] * len(children),
            optimizer_state=state.snapshot(
                cycle.optimizer.prompt_hashes(),
                calibration=state.calibration,
                elites=state.elites,
                rounds_without_advance=state.rounds_without_advance,
            ),
        )

    async def _refine(self, ctx: RoundContext, state: LeviState, idx: int) -> CandidateProposal:
        cycle = ctx.cycle
        assert state.calibration is not None
        knobs = cast("LeviRefineKnobs", cycle.optimizer.knobs(self.name))
        rng = walk_rng(cycle, ctx.round_num, f"{self.name}:{idx}")
        parent = _softmax_pick(state.elites, rng.choice(knobs.sampler_temperatures), rng)
        others = [e for e in state.elites if e is not parent]
        inspirations = (
            []
            if rng.random() < knobs.inspiration_drop
            else rng.sample(others, min(knobs.n_inspirations, len(others)))
        )
        proxy = set(state.calibration.proxy)
        prompt = operators.refine_prompt(
            cycle,
            self.name,
            parent=(parent.individual, parent.score),
            parent_rows=[r for r in _elite_rows(cycle, parent) if r["sample_key"] in proxy],
            inspirations=[(e.individual, e.score) for e in inspirations],
            n_failures=knobs.feedback_failures,
            rng=rng,
        )
        return _proposal(
            self.name,
            [parent.individual],
            await ask(ctx, self.name, idx, prompt),
            f"refine {parent.individual.lineage.id[:6]}",
        )


def _replay_proxy_selected(
    ctx: ReplayContext, inputs_ref: dict[str, Any], data: dict[str, Any]
) -> list[str]:
    round_data = ctx.round_data
    rows = {**round_data.reference_results, **round_data.all_candidate_results}
    return choose_proxy(
        ProxyCssKnobs.model_validate({k: inputs_ref[k] for k in ProxyCssKnobs.model_fields}),
        {cid: rows[cid] for cid in inputs_ref["calibration_ids"]},
        list(inputs_ref["order"]),
    )


_PROPOSERS = (LeviParadigmShift.name, LeviRefine.name)

LEVI_CHECKPOINT_GATING: dict[CheckpointKind, GatingMode] = {
    LeviCheckpointKind.PROXY_SELECTED: GatingMode.REPLAYED,
}
LEVI_REPLAYERS: dict[str, Replayer] = {
    LeviCheckpointKind.PROXY_SELECTED: _replay_proxy_selected,
}


class LeviRuntime:
    """LEVI beyond its nodes: its archive state, its prompt identity and its replayed proxy."""

    name: ClassVar[str] = LEVI_MANIFEST
    manifest_dir: ClassVar[Path] = optimizers_root() / LEVI_MANIFEST
    own_axes: ClassVar[dict[str, set[str]]] = {}
    priced_surface: ClassVar[Mapping[str, int]] = {}
    phases: ClassVar[tuple[nodes.OptimizerPhase, ...]] = ()
    response_models: ClassVar[Mapping[str, type[BaseModel]]] = {}

    def start(
        self, session: Session, config: CampaignConfig, origin_results: list[dict[str, Any]]
    ) -> LeviState:
        return LeviState()

    def complete(self) -> None:
        return None

    def source_digest(self, *covered: ModuleType) -> str:
        return paper_templates.preset_source_digest(operators, *covered)

    def override_param_types(self, node: str) -> dict[str, str]:
        return {}

    def override_levers(self, node: str, declared: Mapping[str, Any]) -> dict[str, Any]:
        return {}

    @property
    def checkpoint_gating(self) -> Mapping[CheckpointKind, GatingMode]:
        return LEVI_CHECKPOINT_GATING

    @property
    def replayers(self) -> Mapping[str, Replayer]:
        return LEVI_REPLAYERS

    def round_packages(self, cycle: Cycle, rounds: list[RoundResult]) -> dict[int, dict[str, str]]:
        # Through the archive's scores and the failures a refinement is shown.
        return nodes.rows_read_packages(rounds, _PROPOSERS)

    async def rederive(
        self,
        campaign_store: CampaignStore,
        hop: CycleHop,
        session: Session,
        cycle: Cycle,
        drifted: list[RoundResult],
    ) -> None:
        return None

    def review(
        self,
        selected: SelectedOptimizer,
        rounds: list[RoundResult],
        audits: list[dict[str, Any] | None],
        *,
        context_object: list[str],
        origin_composite_fitness: float | None,
    ) -> ReviewReading | None:
        return None

    def pacing(self, selected: SelectedOptimizer) -> nodes.OptimizerPacing:
        # Calibration races the diverse seeds; every later round, `interval` evaluations.
        shift = cast("LeviParadigmShiftKnobs", selected.knobs(LeviParadigmShift.name))
        return nodes.OptimizerPacing(
            patience=None,
            stalls_left=None,
            arms_per_round=max(shift.n_diverse_seeds, shift.interval),
            limits=(),
        )

    def opening(self, ctx: RoundContext) -> nodes.RoundOpening:
        return nodes.standing_opening(ctx)

    def round_facts(
        self, selected: SelectedOptimizer, round_result: RoundResult
    ) -> list[OptimizerFact]:
        state = round_result.optimizer_state.payload_as(LeviRoundState)
        if (calibration := state.calibration) is None:
            return []
        elites, k = len(state.elites), len(calibration.proxy)
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


MEMBERS = (ProxyCss(), LeviParadigmShift(), LeviRefine(), MapElites())
RUNTIME = LeviRuntime()
