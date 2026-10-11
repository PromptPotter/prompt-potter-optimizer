from __future__ import annotations

import ast
import random
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import TYPE_CHECKING, Annotated, Any, ClassVar

from pydantic import Field

from promptpotter.application.campaign_config import Estimand, Knob, Scope
from promptpotter.application.optimizers import nodes, paper_templates
from promptpotter.application.optimizers.capo import prompts
from promptpotter.application.optimizers.capo.state import CAPO_MANIFEST, CapoRoundState
from promptpotter.application.optimizers.descriptors import prompt_chars
from promptpotter.application.optimizers.paper_templates import RewriteKnobs, rewrite, rewritten
from promptpotter.application.scoring.candidate_report import fatal_validation_failures
from promptpotter.config.paths import optimizers_root
from promptpotter.domain.opt_search_point import OptSearchPoint
from promptpotter.domain.pipeline_schema import NodeKind
from promptpotter.domain.results import ArmOutcome, CandidateProposal, OptimizerFact
from promptpotter.domain.run_records import CheckpointKind
from promptpotter.domain.scoring import NO_CELLS, ROW_GRADES
from promptpotter.domain.strict_model import StrictModel
from promptpotter.domain.validators import StopSignal
from promptpotter.shared.statistics import paired_mean_t

if TYPE_CHECKING:
    from pathlib import Path
    from types import ModuleType

    from promptpotter.application.bench.node_context import NodeContext
    from promptpotter.application.bench.resume_and_fork.replayers import (
        ReplayContext,
        Replayer,
    )
    from promptpotter.application.campaign_config import CampaignConfig
    from promptpotter.application.initialization.session import Session
    from promptpotter.application.optimizer_manifest import SelectedOptimizer
    from promptpotter.application.optimizers.nodes import (
        BankedState,
        CatchUpFn,
        Measured,
        Panel,
        Proposals,
        Selection,
    )
    from promptpotter.application.scoring.query_loop import Walk
    from promptpotter.domain.results import DisplayMetric, RoundResult
    from promptpotter.domain.sample import Sample
    from promptpotter.domain.scoring import CellSheet, GradedCell
    from promptpotter.domain.search_point import JobSearchPoint

__all__ = [
    "MEMBERS",
    "RUNTIME",
    "BlocksKnobs",
    "CapoCheckpointKind",
    "CapoCrossoverKnobs",
    "CapoInitKnobs",
    "FewShotKnobs",
    "MatingKnobs",
    "PairedTKnobs",
    "PopulationKnobs",
    "cross_shots",
    "mutate_shots",
]


class CapoCheckpointKind(CheckpointKind):
    PAIRED_T_CUT = "paired_t_cut"
    POPULATION_KEPT = "population_kept"


class FewShotKnobs(StrictModel):
    k_max: Annotated[int, Knob(Scope.POLICY, Estimand.SEARCH)] = Field(
        ge=0, description="The most shots an individual may carry after the mutation."
    )


def mutate_shots(
    shots: Sequence[int], pool: Sequence[int], *, k_max: int, rng: random.Random
) -> list[int]:
    out = list(shots)
    move = rng.randrange(3)
    unused = [i for i in pool if i not in out]
    if move == 0 and len(out) < k_max and unused:
        out.append(rng.choice(unused))
    elif move == 1 and out:
        out.pop(rng.randrange(len(out)))
    rng.shuffle(out)
    return out


def cross_shots(parents: Sequence[Sequence[int]], *, rng: random.Random) -> list[int]:
    union = list(dict.fromkeys(i for shots in parents for i in shots))
    n = sum(len(shots) for shots in parents) // len(parents)
    return rng.sample(union, min(n, len(union)))


class FewShot:
    name: ClassVar[str] = "few_shot"
    kind: ClassVar[NodeKind] = NodeKind.ALGORITHM
    opens: ClassVar[bool] = False
    knobs: ClassVar[type[StrictModel]] = FewShotKnobs

    async def propose(
        self, ctx: NodeContext[FewShotKnobs], panel: Panel, proposals: Proposals
    ) -> Proposals:
        k_max = ctx.knobs.k_max
        pool = [s.id for s in ctx.demo_pool]
        return nodes.Proposals(
            [
                ctx.edit(
                    proposal,
                    shot_ids=mutate_shots(
                        proposal.opt_sp.shot_ids, pool, k_max=k_max, rng=ctx.rng(f":{i}")
                    ),
                )
                for i, proposal in enumerate(proposals.proposals)
            ]
        )


class BlocksKnobs(StrictModel):
    block_size: Annotated[int, Knob(Scope.POLICY, Estimand.SELECTION, Estimand.STOPPING)] = Field(
        ge=1,
        description="Cells per block. An eliminator decides only once a block is complete, and a "
        "paired t-test read on one block wants 30 or more.",
    )
    max_blocks: Annotated[int, Knob(Scope.POLICY, Estimand.SELECTION)] = Field(
        ge=1,
        description="The most blocks a round's panel holds — fewer where the search pool cannot "
        "fill them.",
    )


class Blocks:
    """The same cells in the same blocks every round, resume and fork: never shuffled."""

    name: ClassVar[str] = "blocks"
    kind: ClassVar[NodeKind] = NodeKind.SAMPLER
    knobs: ClassVar[type[StrictModel]] = BlocksKnobs
    size_knob: ClassVar[str | None] = None

    def draws(self, selected: SelectedOptimizer, pool: int) -> int:
        knobs = selected.knobs_of(self.name, BlocksKnobs)
        return min(knobs.max_blocks, pool // knobs.block_size) * knobs.block_size

    def draw(self, ctx: NodeContext[BlocksKnobs], pool: list[Sample]) -> Panel:
        knobs = ctx.knobs
        n_blocks = min(knobs.max_blocks, len(pool) // knobs.block_size)
        if n_blocks == 0:
            raise ValueError(
                f"blocks: the search pool holds {len(pool)} rows, fewer than one block of "
                f"{knobs.block_size}"
            )
        cells = pool[: n_blocks * knobs.block_size]
        return nodes.Panel(cells=cells, order=list(cells), block_size=knobs.block_size)


class PairedTKnobs(StrictModel):
    alpha: Annotated[float, Knob(Scope.POLICY, Estimand.STOPPING)] = Field(
        gt=0.0,
        lt=1.0,
        description="Significance level of the one-sided paired t-test asking whether another arm "
        "scores above this one on the cells both measured. Larger → more arms read as beaten → "
        "earlier cuts.",
    )
    length_penalty: Annotated[float, Knob(Scope.POLICY, Estimand.SELECTION, Estimand.STOPPING)] = (
        Field(
            ge=0.0,
            description="The paper's gamma: what CAPO's objective charges per cell for the "
            "prompt's length over the longest initial prompt's. The race and the population both "
            "rank on that objective; the campaign's formula still scores every arm.",
        )
    )


@dataclass(frozen=True)
class _Objective:
    """Unclamped, so a long prompt can take a cell below zero."""

    length_penalty: float
    length_norm: int

    def of(self, cell: GradedCell, chars: int) -> float:
        fitness = float(ROW_GRADES["fitness"].read(cell))
        return fitness - self.length_penalty * chars / self.length_norm


def _objective(ctx: NodeContext[Any]) -> _Objective:
    working = nodes.state_as(ctx, CapoRoundState)
    length_norm = working.payload.length_norm
    if length_norm is None:
        # First asked by round 1's race, when the population is still the initial one.
        if not ctx.population:
            raise ValueError(
                "CAPO's objective divides by the longest initial prompt's length, and no initial "
                "population was generated before its race"
            )
        length_norm = max(
            len(ind.render_target(ctx.framing, demo=ctx.demo_pool)) for ind in ctx.population
        )
        working.payload = CapoRoundState(length_norm=length_norm)
    return _Objective(ctx.knobs_of(PairedT.name, PairedTKnobs).length_penalty, length_norm)


def _cell_objectives(cells: Sequence[GradedCell], objective: _Objective) -> dict[str, float]:
    graded = [cell for cell in cells if cell.scored]
    if not graded:
        return {}
    chars = prompt_chars(cells)
    if chars is None:
        raise ValueError(
            "every graded cell of this arm is a charged error, so none says how long its prompt is"
        )
    return {cell.key: objective.of(cell, chars) for cell in graded}


def _rank_survivors(
    survivors: Sequence[str],
    rows: Mapping[str, CellSheet],
    *,
    size: int,
    objective: _Objective,
) -> list[str]:
    readings = {cid: _cell_objectives(rows[cid].cells, objective) for cid in survivors}
    shared = set.intersection(*(set(r) for r in readings.values())) if readings else set()

    def mean(cid: str) -> float:
        return sum(readings[cid][s] for s in shared) / len(shared) if shared else 0.0

    return sorted(survivors, key=mean, reverse=True)[:size]


def _readings(
    arm: Mapping[str, float], rivals: Mapping[str, Mapping[str, float]]
) -> dict[str, tuple[float, int]]:
    """A rival sharing under two cells with *arm* was not tested, and is absent from the result."""
    out: dict[str, tuple[float, int]] = {}
    for pid, rival in rivals.items():
        shared = [sid for sid in arm if sid in rival]
        _, _, _, p, n = paired_mean_t(
            [rival[s] for s in shared], [arm[s] for s in shared], tail="greater"
        )
        if p is not None:
            out[pid] = (p, n)
    return out


def _labels(labels: list[str], cap: int = 2) -> str:
    return ", ".join(labels[:cap]) + (f" (+{len(labels) - cap})" if len(labels) > cap else "")


class PairedTRace(nodes.NoCatchUps):
    gate = "outscored"
    settled = "settled"

    def __init__(
        self,
        ctx: NodeContext[PairedTKnobs],
        *,
        survivors: int,
        block_size: int,
        n_cells: int,
        objective: _Objective,
    ) -> None:
        self._ctx = ctx
        self.node = ctx.node
        self._alpha = ctx.knobs.alpha
        self._survivors = survivors
        self._objective = objective
        self._block_size = block_size
        self._n_cells = n_cells
        self._arms: dict[int, str] = {}
        self._n_arms = 0
        self._racing: set[int] = set()

    @property
    def n_priors(self) -> int:
        return len(self._racing) - 1

    @property
    def blocks(self) -> PairedTRace:
        return self

    @property
    def block_size(self) -> int:
        return self._block_size

    def rule(self, ahead: Sequence[tuple[str, Walk | None]]) -> None:
        return None

    def open_turn(self, candidate_id: str, idx: int, n: int) -> None:
        self._arms[idx] = candidate_id
        self._n_arms = n
        self._racing.add(idx)

    def close(self, rows: Mapping[int, Sequence[GradedCell]]) -> dict[int, StopSignal]:
        readings = {self._arms[i]: _cell_objectives(r, self._objective) for i, r in rows.items()}
        stops: dict[int, StopSignal] = {}
        for i, arm_rows in rows.items():
            cid = self._arms[i]
            rivals = {pid: reading for pid, reading in readings.items() if pid != cid}
            tested = _readings(readings[cid], rivals)
            if not tested:
                continue
            # A rival's one-sided p is also P(arm beats it) as a t fiducial, which `p_better` reads.
            breakdown = {
                pid: {"p_better": p, "n_paired": float(width)} for pid, (p, width) in tested.items()
            }
            p_best = min(p for p, _ in tested.values())
            snapshot = nodes.RaceSnapshot(p_best, cid, len(arm_rows), breakdown, True)
            self._ctx.callbacks.on_race_standing(
                self.node, self._ctx.round_num, i, self._n_arms, snapshot
            )
            # Uncorrected for the multiple tests, as CAPO races: a correction makes cuts rarer.
            beaten_by = sorted(pid for pid, (p, _) in tested.items() if p < self._alpha)
            if len(beaten_by) >= self._survivors:
                stops[i] = StopSignal(
                    self.node,
                    ArmOutcome.ELIMINATED,
                    {
                        "gate": self.gate,
                        "queries_scored": len(arm_rows),
                        "total_samples": self._n_cells,
                        "raced_against": sorted(rivals),
                        "outscored_by": beaten_by,
                        "p_best": p_best,
                        "paired_breakdown": breakdown,
                    },
                )
        left = [i for i in rows if i not in stops]
        if len(left) <= self._survivors:
            for i in left:
                if len(rows[i]) < self._n_cells:
                    stops[i] = StopSignal(
                        self.node,
                        ArmOutcome.LOCKED_IN,
                        {
                            "gate": self.settled,
                            "queries_scored": len(rows[i]),
                            "total_samples": self._n_cells,
                            "raced_against": sorted(self._arms[j] for j in rows if j != i),
                        },
                    )
        self._racing = set(left) - set(stops)
        return stops

    def judge(
        self,
        signal: StopSignal | None,
        *,
        candidate_id: str,
        results: Sequence[GradedCell],
        labels: dict[str, str],
    ) -> nodes.EliminationReading | None:
        if signal is None or signal.check_name != self.node:
            return None
        cr = signal.check_result

        def walk_order(pids: list[str]) -> list[str]:
            return [label for pid, label in labels.items() if pid in pids]

        context: dict[str, Any] = {
            "gate": cr["gate"],
            "queries_scored": cr["queries_scored"],
            "total_queries": cr["total_samples"],
            "block": -(-cr["queries_scored"] // self._block_size),
            "blocks": -(-cr["total_samples"] // self._block_size),
            "raced_against": walk_order(cr["raced_against"]),
        }
        where = (
            f"at block {context['block']}/{context['blocks']} "
            f"(q{cr['queries_scored']}/{cr['total_samples']})"
        )
        if signal.outcome is not ArmOutcome.ELIMINATED:
            reason = f"settled {where}: {self._survivors} or fewer left racing"
            return nodes.EliminationReading(reason=reason, context=context)
        # The rivals are named, not their rows: a replay reads every arm off the rescored round.
        self._ctx.decide(
            CapoCheckpointKind.PAIRED_T_CUT,
            {
                "candidate_id": candidate_id,
                "round_num": self._ctx.round_num,
                "queries_scored": cr["queries_scored"],
                "alpha": self._alpha,
                "survivors": self._survivors,
                "length_penalty": self._objective.length_penalty,
                "length_norm": self._objective.length_norm,
                "raced_against": list(cr["raced_against"]),
            },
            True,
            data={"outscored_by": list(cr["outscored_by"])},
        )
        context["outscored_by"] = walk_order(cr["outscored_by"])
        outscored, raced = context["outscored_by"], context["raced_against"]
        reason = f"outscored {where} by {len(outscored)} of {len(raced)}: {_labels(outscored)}"
        return nodes.EliminationReading(reason=reason, context=context)

    def admit(self, candidate_id: str, results: Sequence[GradedCell], sp: JobSearchPoint) -> None:
        return None


class PairedT:
    name: ClassVar[str] = "paired_t"
    kind: ClassVar[NodeKind] = NodeKind.ELIMINATOR
    knobs: ClassVar[type[StrictModel]] = PairedTKnobs
    abort_lenses: ClassVar[Mapping[str, frozenset[str]]] = {}
    # Cut once μ others are significantly better: it can no longer be among the μ kept.
    stop_disqualifies: ClassVar[bool] = True

    def race(
        self,
        ctx: NodeContext[PairedTKnobs],
        panel: Panel,
        proposals: Proposals,
        catch_up: CatchUpFn,
    ) -> PairedTRace:
        return PairedTRace(
            ctx,
            survivors=ctx.knobs_of(PopulationSelector.name, PopulationKnobs).size,
            block_size=panel.block_size,
            n_cells=len(panel.order),
            objective=_objective(ctx),
        )


class CapoInitKnobs(RewriteKnobs):
    size: Annotated[int, Knob(Scope.POLICY, Estimand.SEARCH)] = Field(
        ge=2, description="The individuals the initial population holds — the paper's μ."
    )
    k_max: Annotated[int, Knob(Scope.POLICY, Estimand.SEARCH)] = Field(
        ge=0, description="The most demo-pool shots an initial individual is given."
    )


class CapoInit:
    name: ClassVar[str] = "capo_init"
    kind: ClassVar[NodeKind] = NodeKind.LLM
    opens: ClassVar[bool] = True
    knobs: ClassVar[type[StrictModel]] = CapoInitKnobs

    async def propose(
        self, ctx: NodeContext[CapoInitKnobs], panel: Panel, proposals: Proposals
    ) -> Proposals:
        knobs = ctx.knobs
        raw = await ctx.ask(prompts.init_prompt(ctx))
        start, end = raw.find("["), raw.rfind("]")
        listed = ast.literal_eval(raw[start : end + 1]) if 0 <= start < end else None
        if not isinstance(listed, list) or not all(isinstance(s, str) for s in listed):
            raise ValueError(f"{self.name} answered no array of instructions: {raw[:300]!r}")
        instructions = [s.strip() for s in listed if s.strip()]
        pool = [s.id for s in ctx.demo_pool]
        rng = ctx.rng()
        drawn = rng.sample(instructions, min(knobs.size, len(instructions)))
        return nodes.Proposals(
            [
                ctx.child(
                    [ctx.origin],
                    changes_description=f"initial instruction {n + 1}",
                    **rewritten(text, knobs.rewrites),
                    shot_ids=rng.sample(pool, min(rng.randint(0, knobs.k_max), len(pool))),
                )
                for n, text in enumerate(drawn)
            ]
        )


class MatingKnobs(StrictModel):
    offspring: Annotated[int, Knob(Scope.POLICY, Estimand.SEARCH)] = Field(
        ge=1,
        description="c, the offspring one round makes — each from two parents drawn at random "
        "from the population, never by score.",
    )


class Mating:
    """Each child is its first parent's copy naming both; the recombining nodes after it write it."""

    name: ClassVar[str] = "mating"
    kind: ClassVar[NodeKind] = NodeKind.ALGORITHM
    opens: ClassVar[bool] = True
    knobs: ClassVar[type[StrictModel]] = MatingKnobs

    async def propose(
        self, ctx: NodeContext[MatingKnobs], panel: Panel, proposals: Proposals
    ) -> Proposals:
        carried = list(ctx.population)
        if len(carried) < 2:
            raise ValueError(f"a mating needs two parents; the population holds {len(carried)}")
        rng = ctx.rng()
        pairs = [rng.sample(carried, 2) for _ in range(ctx.knobs.offspring)]
        return nodes.Proposals(
            [
                ctx.child([a, b], changes_description=f"crossover {a.id[:6]}+{b.id[:6]}")
                for a, b in pairs
            ]
        )


def _mated(ctx: NodeContext[Any], proposals: Proposals) -> list[list[OptSearchPoint]]:
    carried = {individual.id: individual for individual in ctx.population}
    return [[carried[pid] for pid in p.opt_sp.lineage.parent_ids] for p in proposals.proposals]


class CapoCrossoverKnobs(RewriteKnobs):
    """Beside the merge's call config, which loci it rewrites."""


class CapoCrossover:
    name: ClassVar[str] = "capo_crossover"
    kind: ClassVar[NodeKind] = NodeKind.LLM
    opens: ClassVar[bool] = False
    knobs: ClassVar[type[StrictModel]] = CapoCrossoverKnobs

    async def propose(
        self, ctx: NodeContext[CapoCrossoverKnobs], panel: Panel, proposals: Proposals
    ) -> Proposals:
        pairs = _mated(ctx, proposals)
        if lone := [len(pair) for pair in pairs if len(pair) != 2]:
            raise ValueError(f"a crossover merges two parents; an offspring names {lone[0]}")
        answers = await ctx.ask_each(
            {i: prompts.crossover_prompt(ctx, a, b) for i, (a, b) in enumerate(pairs)}
        )
        return nodes.Proposals(
            [rewrite(ctx, p, raw) for p, raw in zip(proposals.proposals, answers, strict=True)]
        )


class ShotCrossoverKnobs(StrictModel):
    """It draws from the parents' shots and nothing else; it takes no knob."""


class ShotCrossover:
    name: ClassVar[str] = "shot_crossover"
    kind: ClassVar[NodeKind] = NodeKind.ALGORITHM
    opens: ClassVar[bool] = False
    knobs: ClassVar[type[StrictModel]] = ShotCrossoverKnobs

    async def propose(
        self, ctx: NodeContext[ShotCrossoverKnobs], panel: Panel, proposals: Proposals
    ) -> Proposals:
        rng = ctx.rng()
        return nodes.Proposals(
            [
                ctx.edit(p, shot_ids=cross_shots([m.shot_ids for m in mates], rng=rng))
                for p, mates in zip(proposals.proposals, _mated(ctx, proposals), strict=True)
            ]
        )


class CapoMutateKnobs(RewriteKnobs):
    """Beside the rewrite's call config, which loci it rewrites."""


class CapoMutate:
    """A child its crossover already failed is passed on untouched: it will never be measured."""

    name: ClassVar[str] = "capo_mutate"
    kind: ClassVar[NodeKind] = NodeKind.LLM
    opens: ClassVar[bool] = False
    knobs: ClassVar[type[StrictModel]] = CapoMutateKnobs

    async def propose(
        self, ctx: NodeContext[CapoMutateKnobs], panel: Panel, proposals: Proposals
    ) -> Proposals:
        live = [
            i
            for i, p in enumerate(proposals.proposals)
            if not fatal_validation_failures(p.validation_failures)
        ]
        answers = await ctx.ask_each(
            {i: prompts.mutation_prompt(ctx, proposals.individuals[i]) for i in live}
        )
        mutated = list(proposals.proposals)
        for i, raw in zip(live, answers, strict=True):
            was = mutated[i].opt_sp.lineage.changes_description
            mutated[i] = rewrite(ctx, mutated[i], raw, f"{was}, rephrased")
        return nodes.Proposals(mutated)


class PopulationRejoinKnobs(StrictModel):
    """It moves the population and nothing else; it takes no knob."""


class PopulationRejoin:
    """Members go AHEAD of the offspring: they walk first, so every offspring is raced against them."""

    name: ClassVar[str] = "population_rejoin"
    kind: ClassVar[NodeKind] = NodeKind.ALGORITHM
    opens: ClassVar[bool] = False
    knobs: ClassVar[type[StrictModel]] = PopulationRejoinKnobs

    async def propose(
        self, ctx: NodeContext[PopulationRejoinKnobs], panel: Panel, proposals: Proposals
    ) -> Proposals:
        return nodes.Proposals(
            [
                *(CandidateProposal(opt_sp=m) for m in ctx.population),
                *proposals.proposals,
            ]
        )


class PopulationKnobs(StrictModel):
    size: Annotated[int, Knob(Scope.POLICY, Estimand.SELECTION, Estimand.STOPPING)] = Field(
        ge=2,
        description="μ, the individuals CAPO carries into the next round: the race's survivors, "
        "best first, cut to this many. The paired-t race reads it too: an arm is cut once μ "
        "others are significantly better, since it can no longer be among them.",
    )


class PopulationSelector:
    name: ClassVar[str] = "population"
    kind: ClassVar[NodeKind] = NodeKind.SELECTOR
    knobs: ClassVar[type[StrictModel]] = PopulationKnobs
    elects_on: ClassVar[DisplayMetric] = "accuracy"
    elects_partial: ClassVar[bool] = True

    def parent_cells(
        self,
        ctx: NodeContext[PopulationKnobs],
        panel: Panel,
        rows: Mapping[str, CellSheet],
    ) -> list[Sample]:
        # The incumbent's reading is the cells it walked as a member; round 1's origin reads block one.
        walked = {cell.sample_id for cell in rows.get(ctx.parent.id, NO_CELLS)}
        return [s for s in panel.cells if s.id in walked] or panel.cells[: panel.block_size]

    def select(
        self, ctx: NodeContext[PopulationKnobs], measured: Measured, proposals: Proposals
    ) -> Selection:
        size = ctx.knobs.size
        survivors = [ind.id for ind in measured.electable]
        objective = _objective(ctx)
        kept = _rank_survivors(survivors, measured.rows, size=size, objective=objective)
        ctx.decide(
            CapoCheckpointKind.POPULATION_KEPT,
            {
                "survivors": survivors,
                "round_num": ctx.round_num,
                "size": size,
                "length_penalty": objective.length_penalty,
                "length_norm": objective.length_norm,
            },
            kept,
        )
        by_id = {ind.id: ind for ind in measured.electable}
        labels = {cs.candidate_id: cs.label for cs in measured.scores}
        best = kept[0] if kept else ""
        selected_id = best if best and best != ctx.parent.id else ""
        if kept:
            ctx.keep([by_id[cid] for cid in kept])
        carried = ctx.population
        verdict = (
            f"kept {len(kept)} of {len(survivors)} surviving arms (μ {size}) by mean objective "
            f"(length penalty {objective.length_penalty}) on their shared cells; "
            f"best {labels[best]}"
            if kept
            else f"no arm survived the race; the population of {len(carried)} stands"
        )
        return nodes.Selection(
            selected_id=selected_id,
            # The race's best survivor, which the incumbent may be: it races as an arm.
            leading_id=best,
            scores=list(measured.scores),
            verdict_reason=verdict,
        )


def _replay_paired_t_cut(
    ctx: ReplayContext, inputs_ref: dict[str, Any], data: dict[str, Any]
) -> bool:
    rows = ctx.round_data.all_candidate_results
    objective = _recorded_objective(inputs_ref)
    arm = _cell_objectives(
        rows[inputs_ref["candidate_id"]].cells[: int(inputs_ref["queries_scored"])], objective
    )
    rivals = {
        pid: _cell_objectives(rows[pid].cells, objective) for pid in inputs_ref["raced_against"]
    }
    alpha = float(inputs_ref["alpha"])
    beaten = sum(1 for p, _ in _readings(arm, rivals).values() if p < alpha)
    return beaten >= int(inputs_ref["survivors"])


def _replay_population_kept(
    ctx: ReplayContext, inputs_ref: dict[str, Any], data: dict[str, Any]
) -> list[str]:
    return _rank_survivors(
        list(inputs_ref["survivors"]),
        ctx.round_data.all_candidate_results,
        size=int(inputs_ref["size"]),
        objective=_recorded_objective(inputs_ref),
    )


def _recorded_objective(inputs_ref: Mapping[str, Any]) -> _Objective:
    return _Objective(float(inputs_ref["length_penalty"]), int(inputs_ref["length_norm"]))


class CapoRuntime(nodes.OptimizerRuntime):
    name: ClassVar[str] = CAPO_MANIFEST
    manifest_dir: ClassVar[Path] = optimizers_root() / CAPO_MANIFEST
    prompt_sources: ClassVar[tuple[ModuleType, ...]] = (paper_templates, prompts)
    replayers: ClassVar[Mapping[str, Replayer]] = {
        CapoCheckpointKind.PAIRED_T_CUT: _replay_paired_t_cut,
        CapoCheckpointKind.POPULATION_KEPT: _replay_population_kept,
    }
    couplings: ClassVar[Mapping[str, tuple[nodes.MemberCoupling, ...]]] = {
        FewShot.name: (
            nodes.MemberCoupling(
                name="shots_without_demo_pool",
                knobs=("k_max",),
                bench_knobs=("dataset_split",),
                estimand=Estimand.SEARCH,
                relation="An individual's shots are drawn from the demo pool `dataset_split.demo` holds out.",
                consequence=(
                    "The campaign declares no demo pool, so every individual carries no shot and "
                    "k_max is inert: CAPO runs as an instruction-only search."
                ),
                severity="inert",
                predicate=lambda c, k, d: (
                    k.k_max > 0 and (c.dataset_split is None or c.dataset_split.demo == 0)
                ),
            ),
        )
    }

    def start(
        self, session: Session, config: CampaignConfig, origin_results: CellSheet
    ) -> BankedState[CapoRoundState]:
        if prompt_chars(origin_results) is None:
            raise ValueError(
                "CAPO's objective charges each cell for its prompt's length, and this pipeline's "
                "rows carry no target_prompt_chars: it renders no prompt node CAPO could edit"
            )
        return nodes.BankedState(CapoRoundState(length_norm=None))

    def arms(self, selected: SelectedOptimizer) -> int:
        # The population races beside the round's offspring (`PopulationRejoin`).
        size = selected.knobs_of(PopulationSelector.name, PopulationKnobs).size
        return size + selected.knobs_of(Mating.name, MatingKnobs).offspring

    def round_cells_ceiling(self, selected: SelectedOptimizer, pool: int) -> int:
        # Round 1's origin is no member: its reading is one block beside the arms'.
        block = selected.knobs_of(Blocks.name, BlocksKnobs).block_size
        panel = selected.round_cells(pool)
        return self.arms(selected) * panel + block if panel else 0

    def round_facts(
        self, selected: SelectedOptimizer, round_result: RoundResult
    ) -> list[OptimizerFact]:
        if round_result.round == 0:
            return []
        arms = round_result.candidate_scores
        cut = sum(1 for cs in arms if cs.outcome is ArmOutcome.ELIMINATED)
        block_size = selected.knobs_of(Blocks.name, BlocksKnobs).block_size
        deepest = max(
            (-(-len(rows) // block_size) for rows in round_result.all_candidate_results.values()),
            default=0,
        )
        kept = len(round_result.optimizer_state.population)
        size = selected.knobs_of(PopulationSelector.name, PopulationKnobs).size
        return [
            OptimizerFact(
                key="blocks", label="Blocks raced", text=str(deepest), value=deepest, kind="stat"
            ),
            OptimizerFact(
                key="cut", label="Cut", text=f"{cut} of {len(arms)} arms", value=cut, kind="stat"
            ),
            OptimizerFact(
                key="population",
                label="Population",
                text=f"{kept} kept (μ {size})",
                value=kept,
                kind="stat",
            ),
        ]


MEMBERS = (
    FewShot(),
    Blocks(),
    PairedT(),
    CapoInit(),
    Mating(),
    CapoCrossover(),
    ShotCrossover(),
    CapoMutate(),
    PopulationRejoin(),
    PopulationSelector(),
)
RUNTIME = CapoRuntime()
