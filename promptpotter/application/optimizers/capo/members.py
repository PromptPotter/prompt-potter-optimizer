from __future__ import annotations

import ast
import random
from collections.abc import Mapping, Sequence
from typing import TYPE_CHECKING, Annotated, Any, ClassVar

from pydantic import Field

from promptpotter.application.campaign_config import Estimand, Knob, Scope
from promptpotter.application.optimizers import nodes, paper_templates
from promptpotter.application.optimizers.capo import prompts
from promptpotter.application.optimizers.capo.state import CAPO_MANIFEST, CapoObjective
from promptpotter.application.optimizers.descriptors import prompt_chars
from promptpotter.application.optimizers.paper_templates import RewriteKnobs, rewrite, rewritten
from promptpotter.config.paths import optimizers_root
from promptpotter.domain.opt_search_point import Locus, OptSearchPoint
from promptpotter.domain.paired_reading import ArmKey
from promptpotter.domain.pipeline_schema import NodeKind
from promptpotter.domain.pool import DemoPool
from promptpotter.domain.results import Cut, Elimination, OptimizerFact, Proposal
from promptpotter.domain.run_records import CheckpointKind
from promptpotter.domain.scoring import NO_CELLS
from promptpotter.domain.search_point import SHOTS_KEY
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
    from promptpotter.application.optimizer_manifest import SelectedOptimizer
    from promptpotter.application.optimizers.nodes import (
        CatchUpFn,
        Draw,
        MeasuredRound,
        Panel,
        PromptArity,
        Proposals,
        Selection,
    )
    from promptpotter.application.scoring.query_loop import Walk
    from promptpotter.domain.opt_search_point import VariationMode
    from promptpotter.domain.optimizer_state import StatePart
    from promptpotter.domain.pool import Pool
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
    shots: Sequence[str], pool: Sequence[str], *, k_max: int, rng: random.Random
) -> list[str]:
    out = list(shots)
    move = rng.randrange(3)
    unused = [i for i in pool if i not in out]
    if move == 0 and len(out) < k_max and unused:
        out.append(rng.choice(unused))
    elif move == 1 and out:
        out.pop(rng.randrange(len(out)))
    rng.shuffle(out)
    return out


def cross_shots(parents: Sequence[Sequence[str]], *, rng: random.Random) -> list[str]:
    union = list(dict.fromkeys(i for shots in parents for i in shots))
    n = sum(len(shots) for shots in parents) // len(parents)
    return rng.sample(union, min(n, len(union)))


class FewShot:
    name: ClassVar[str] = "few_shot"
    kind: ClassVar[NodeKind] = NodeKind.ALGORITHM
    prompt_arity: ClassVar[PromptArity] = "one"
    opens: ClassVar[bool] = False
    variation_mode: ClassVar[VariationMode] = "deterministic"
    knobs: ClassVar[type[StrictModel]] = FewShotKnobs
    reads: ClassVar[frozenset[type[StatePart]]] = nodes.NO_STATE
    writes: ClassVar[frozenset[type[StatePart]]] = nodes.NO_STATE
    draws: ClassVar[Mapping[type[Pool], Draw]] = {DemoPool: nodes.CountKnob(FewShotKnobs, "k_max")}
    consults: ClassVar[frozenset[type]] = nodes.NO_CONSULTS

    async def propose(
        self, ctx: NodeContext[FewShotKnobs], panel: Panel, proposals: Proposals
    ) -> Proposals:
        k_max = ctx.knobs.k_max
        pool = [s.key for s in ctx.drawn(DemoPool)]
        node = ctx.prompt_node.name
        return nodes.Proposals(
            [
                ctx.edit(
                    proposal,
                    prompt_loci={
                        Locus(node, SHOTS_KEY): mutate_shots(
                            proposal.individual.prompt(node).shots,
                            pool,
                            k_max=k_max,
                            rng=ctx.rng(f":{i}"),
                        )
                    },
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
        description="The most blocks a round's panel holds — fewer where the search split cannot "
        "fill them.",
    )


class Blocks:
    """The same cells in the same blocks every round, resume and fork: never shuffled."""

    name: ClassVar[str] = "blocks"
    kind: ClassVar[NodeKind] = NodeKind.SAMPLER
    prompt_arity: ClassVar[PromptArity] = "none"
    knobs: ClassVar[type[StrictModel]] = BlocksKnobs
    reads: ClassVar[frozenset[type[StatePart]]] = nodes.NO_STATE
    writes: ClassVar[frozenset[type[StatePart]]] = nodes.NO_STATE
    draws: ClassVar[Mapping[type[Pool], Draw]] = nodes.NO_DRAWS
    consults: ClassVar[frozenset[type]] = nodes.NO_CONSULTS
    size_knob: ClassVar[str | None] = None

    def round_cells(self, knobs: BlocksKnobs, search: int) -> int:
        return min(knobs.max_blocks, search // knobs.block_size) * knobs.block_size

    def draw(self, ctx: NodeContext[BlocksKnobs], pool: list[Sample]) -> Panel:
        knobs = ctx.knobs
        n_blocks = min(knobs.max_blocks, len(pool) // knobs.block_size)
        if n_blocks == 0:
            raise ValueError(
                f"blocks: the search split holds {len(pool)} rows, fewer than one block of "
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


def _cell_objectives(cells: Sequence[GradedCell], objective: CapoObjective) -> dict[str, float]:
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
    survivors: Sequence[ArmKey],
    rows: Mapping[ArmKey, CellSheet],
    *,
    size: int,
    objective: CapoObjective,
) -> list[ArmKey]:
    readings = {key: _cell_objectives(rows[key].cells, objective) for key in survivors}
    shared = set.intersection(*(set(r) for r in readings.values())) if readings else set()

    def mean(key: ArmKey) -> float:
        return sum(readings[key][s] for s in shared) / len(shared) if shared else 0.0

    return sorted(survivors, key=mean, reverse=True)[:size]


def _readings(
    arm: Mapping[str, float], rivals: Mapping[ArmKey, Mapping[str, float]]
) -> dict[ArmKey, tuple[float, int]]:
    """A rival sharing under two cells with *arm* was not tested, and is absent from the result."""
    out: dict[ArmKey, tuple[float, int]] = {}
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
        objective: CapoObjective,
    ) -> None:
        self._ctx = ctx
        self.node = ctx.node
        self._alpha = ctx.knobs.alpha
        self._survivors = survivors
        self._objective = objective
        self._block_size = block_size
        self._n_cells = n_cells
        self._racing: set[ArmKey] = set()

    @property
    def n_priors(self) -> int:
        return len(self._racing) - 1

    @property
    def blocks(self) -> PairedTRace:
        return self

    @property
    def block_size(self) -> int:
        return self._block_size

    def rule(self, ahead: Sequence[tuple[ArmKey, Walk | None]]) -> None:
        return None

    def open_turn(self, key: ArmKey) -> None:
        self._racing.add(key)

    def close(self, rows: Mapping[ArmKey, Sequence[GradedCell]]) -> dict[ArmKey, StopSignal]:
        readings: dict[ArmKey, dict[str, float]] = {
            cid: _cell_objectives(r, self._objective) for cid, r in rows.items()
        }
        stops: dict[ArmKey, StopSignal] = {}
        for cid, arm_rows in rows.items():
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
            self._ctx.callbacks.on_race_standing(self.node, self._ctx.round_num, snapshot)
            # Uncorrected for the multiple tests, as CAPO races: a correction makes cuts rarer.
            beaten_by = sorted(pid for pid, (p, _) in tested.items() if p < self._alpha)
            if len(beaten_by) >= self._survivors:
                stops[cid] = StopSignal(
                    self.node,
                    "eliminated",
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
        left = [cid for cid in rows if cid not in stops]
        if len(left) <= self._survivors:
            for cid in left:
                if len(rows[cid]) < self._n_cells:
                    stops[cid] = StopSignal(
                        self.node,
                        "locked_in",
                        {
                            "gate": self.settled,
                            "queries_scored": len(rows[cid]),
                            "total_samples": self._n_cells,
                            "raced_against": sorted(pid for pid in rows if pid != cid),
                        },
                    )
        self._racing = {cid for cid in left if cid not in stops}
        return stops

    def judge(
        self,
        signal: StopSignal | None,
        *,
        key: ArmKey,
        results: Sequence[GradedCell],
        labels: Mapping[ArmKey, str],
    ) -> Elimination | None:
        if signal is None or signal.check_name != self.node:
            return None
        cr = signal.check_result

        def walk_order(keys: list[ArmKey]) -> list[ArmKey]:
            return [key for key in labels if key in keys]

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
        if signal.outcome != "eliminated":
            reason = f"settled {where}: {self._survivors} or fewer left racing"
            return Elimination(reason=reason, context=context)
        # The rivals are named, not their rows: a replay reads every arm off the rescored round.
        self._ctx.decide(
            CapoCheckpointKind.PAIRED_T_CUT,
            {
                "arm": key,
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
        named = _labels([labels[k] for k in outscored])
        reason = f"outscored {where} by {len(outscored)} of {len(raced)}: {named}"
        return Elimination(reason=reason, context=context)

    def admit(self, key: ArmKey, results: Sequence[GradedCell], sp: JobSearchPoint) -> None:
        return None


class PairedT:
    name: ClassVar[str] = "paired_t"
    kind: ClassVar[NodeKind] = NodeKind.ELIMINATOR
    prompt_arity: ClassVar[PromptArity] = "none"
    knobs: ClassVar[type[StrictModel]] = PairedTKnobs
    reads: ClassVar[frozenset[type[StatePart]]] = frozenset({CapoObjective})
    writes: ClassVar[frozenset[type[StatePart]]] = nodes.NO_STATE
    draws: ClassVar[Mapping[type[Pool], Draw]] = nodes.NO_DRAWS
    abort_lenses: ClassVar[Mapping[str, frozenset[str]]] = {}
    # Cut once μ others are significantly better: it can no longer be among the μ kept.
    stop_disqualifies: ClassVar[bool] = True

    @property
    def consults(self) -> frozenset[type]:
        return frozenset({PopulationSelector})

    def race(
        self,
        ctx: NodeContext[PairedTKnobs],
        panel: Panel,
        proposals: Proposals,
        catch_up: CatchUpFn,
    ) -> PairedTRace:
        return PairedTRace(
            ctx,
            survivors=ctx.knobs_of(PopulationKnobs).size,
            block_size=panel.block_size,
            n_cells=len(panel.order),
            objective=ctx.slice(CapoObjective),
        )


class CapoInitKnobs(RewriteKnobs):
    size: Annotated[int, Knob(Scope.POLICY, Estimand.SEARCH)] = Field(
        ge=2, description="The individuals the initial population holds — the paper's μ."
    )
    k_max: Annotated[int, Knob(Scope.POLICY, Estimand.SEARCH)] = Field(
        ge=0, description="The most demo-pool shots an initial individual is given."
    )
    length_penalty: Annotated[float, Knob(Scope.POLICY, Estimand.SELECTION, Estimand.STOPPING)] = (
        Field(
            ge=0.0,
            description="The paper's gamma: what CAPO's objective charges per cell for the "
            "prompt's length over the longest initial prompt's. The race and the population both "
            "rank on that objective; the campaign's formula still scores every arm.",
        )
    )


class CapoInit:
    """Draws the initial population, which sets the objective every later race and selection ranks on."""

    name: ClassVar[str] = "capo_init"
    kind: ClassVar[NodeKind] = NodeKind.LLM
    prompt_arity: ClassVar[PromptArity] = "one"
    opens: ClassVar[bool] = True
    variation_mode: ClassVar[VariationMode] = "llm"
    knobs: ClassVar[type[StrictModel]] = CapoInitKnobs
    reads: ClassVar[frozenset[type[StatePart]]] = nodes.NO_STATE
    writes: ClassVar[frozenset[type[StatePart]]] = frozenset({CapoObjective})
    draws: ClassVar[Mapping[type[Pool], Draw]] = {DemoPool: nodes.CountKnob(CapoInitKnobs, "k_max")}
    consults: ClassVar[frozenset[type]] = nodes.NO_CONSULTS

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
        pool = [s.key for s in ctx.drawn(DemoPool)]
        node = ctx.prompt_node
        shots = Locus(node.name, SHOTS_KEY)
        rng = ctx.rng()
        drawn = rng.sample(instructions, min(knobs.size, len(instructions)))
        initial = nodes.Proposals(
            [
                ctx.child(
                    [ctx.origin],
                    prompt_loci={
                        **rewritten(ctx, node, text),
                        shots: rng.sample(pool, min(rng.randint(0, knobs.k_max), len(pool))),
                    },
                    changes_description=f"initial instruction {n + 1}",
                )
                for n, text in enumerate(drawn)
            ]
        )
        live = [p.individual for p in initial.proposals if not p.refused]
        if not live:
            raise ValueError(f"{self.name} drew no initial individual that passed validation")
        ctx.put(
            CapoObjective(
                length_penalty=knobs.length_penalty,
                length_norm=max(
                    len(ind.render_target(node, ctx.framing, demo=ctx.demo_split)) for ind in live
                ),
            )
        )
        return initial


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
    prompt_arity: ClassVar[PromptArity] = "none"
    opens: ClassVar[bool] = True
    variation_mode: ClassVar[VariationMode] = "deterministic"
    knobs: ClassVar[type[StrictModel]] = MatingKnobs
    reads: ClassVar[frozenset[type[StatePart]]] = nodes.NO_STATE
    writes: ClassVar[frozenset[type[StatePart]]] = nodes.NO_STATE
    draws: ClassVar[Mapping[type[Pool], Draw]] = nodes.NO_DRAWS
    consults: ClassVar[frozenset[type]] = nodes.NO_CONSULTS

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
    return [[carried[pid] for pid in p.individual.lineage.parent_ids] for p in proposals.proposals]


class CapoCrossoverKnobs(RewriteKnobs):
    """Beside the merge's call config, which loci it rewrites."""


class CapoCrossover:
    name: ClassVar[str] = "capo_crossover"
    kind: ClassVar[NodeKind] = NodeKind.LLM
    prompt_arity: ClassVar[PromptArity] = "one"
    opens: ClassVar[bool] = False
    variation_mode: ClassVar[VariationMode] = "llm"
    knobs: ClassVar[type[StrictModel]] = CapoCrossoverKnobs
    reads: ClassVar[frozenset[type[StatePart]]] = nodes.NO_STATE
    writes: ClassVar[frozenset[type[StatePart]]] = nodes.NO_STATE
    draws: ClassVar[Mapping[type[Pool], Draw]] = nodes.NO_DRAWS
    consults: ClassVar[frozenset[type]] = nodes.NO_CONSULTS

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
    prompt_arity: ClassVar[PromptArity] = "one"
    opens: ClassVar[bool] = False
    variation_mode: ClassVar[VariationMode] = "deterministic"
    knobs: ClassVar[type[StrictModel]] = ShotCrossoverKnobs
    reads: ClassVar[frozenset[type[StatePart]]] = nodes.NO_STATE
    writes: ClassVar[frozenset[type[StatePart]]] = nodes.NO_STATE
    draws: ClassVar[Mapping[type[Pool], Draw]] = nodes.NO_DRAWS
    consults: ClassVar[frozenset[type]] = nodes.NO_CONSULTS

    async def propose(
        self, ctx: NodeContext[ShotCrossoverKnobs], panel: Panel, proposals: Proposals
    ) -> Proposals:
        rng = ctx.rng()
        node = ctx.prompt_node.name
        return nodes.Proposals(
            [
                ctx.edit(
                    p,
                    prompt_loci={
                        Locus(node, SHOTS_KEY): cross_shots(
                            [m.prompt(node).shots for m in mates], rng=rng
                        )
                    },
                )
                for p, mates in zip(proposals.proposals, _mated(ctx, proposals), strict=True)
            ]
        )


class CapoMutateKnobs(RewriteKnobs):
    """Beside the rewrite's call config, which loci it rewrites."""


class CapoMutate:
    """A child its crossover already failed is passed on untouched: it will never be measured."""

    name: ClassVar[str] = "capo_mutate"
    kind: ClassVar[NodeKind] = NodeKind.LLM
    prompt_arity: ClassVar[PromptArity] = "one"
    opens: ClassVar[bool] = False
    variation_mode: ClassVar[VariationMode] = "llm"
    knobs: ClassVar[type[StrictModel]] = CapoMutateKnobs
    reads: ClassVar[frozenset[type[StatePart]]] = nodes.NO_STATE
    writes: ClassVar[frozenset[type[StatePart]]] = nodes.NO_STATE
    draws: ClassVar[Mapping[type[Pool], Draw]] = nodes.NO_DRAWS
    consults: ClassVar[frozenset[type]] = nodes.NO_CONSULTS

    async def propose(
        self, ctx: NodeContext[CapoMutateKnobs], panel: Panel, proposals: Proposals
    ) -> Proposals:
        live = [i for i, p in enumerate(proposals.proposals) if not p.refused]
        answers = await ctx.ask_each(
            {i: prompts.mutation_prompt(ctx, proposals.individuals[i]) for i in live}
        )
        mutated = list(proposals.proposals)
        for i, raw in zip(live, answers, strict=True):
            was = mutated[i].individual.lineage.changes_description
            mutated[i] = rewrite(ctx, mutated[i], raw, f"{was}, rephrased")
        return nodes.Proposals(mutated)


class PopulationRejoinKnobs(StrictModel):
    """It moves the population and nothing else; it takes no knob."""


class PopulationRejoin:
    """Members go AHEAD of the offspring: they walk first, so every offspring is raced against them."""

    name: ClassVar[str] = "population_rejoin"
    kind: ClassVar[NodeKind] = NodeKind.ALGORITHM
    prompt_arity: ClassVar[PromptArity] = "none"
    opens: ClassVar[bool] = False
    variation_mode: ClassVar[VariationMode] = "deterministic"
    knobs: ClassVar[type[StrictModel]] = PopulationRejoinKnobs
    reads: ClassVar[frozenset[type[StatePart]]] = nodes.NO_STATE
    writes: ClassVar[frozenset[type[StatePart]]] = nodes.NO_STATE
    draws: ClassVar[Mapping[type[Pool], Draw]] = nodes.NO_DRAWS
    consults: ClassVar[frozenset[type]] = nodes.NO_CONSULTS

    async def propose(
        self, ctx: NodeContext[PopulationRejoinKnobs], panel: Panel, proposals: Proposals
    ) -> Proposals:
        return nodes.Proposals(
            [
                *(Proposal(individual=m) for m in ctx.population),
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
    prompt_arity: ClassVar[PromptArity] = "none"
    knobs: ClassVar[type[StrictModel]] = PopulationKnobs
    reads: ClassVar[frozenset[type[StatePart]]] = frozenset({CapoObjective})
    writes: ClassVar[frozenset[type[StatePart]]] = nodes.NO_STATE
    draws: ClassVar[Mapping[type[Pool], Draw]] = nodes.NO_DRAWS
    consults: ClassVar[frozenset[type]] = nodes.NO_CONSULTS
    elects_on: ClassVar[DisplayMetric] = "accuracy"
    elects_partial: ClassVar[bool] = True

    def parent_cells(
        self,
        ctx: NodeContext[PopulationKnobs],
        panel: Panel,
        rows: Mapping[ArmKey, CellSheet],
    ) -> list[Sample]:
        # The incumbent's reading is the cells it walked as a member; round 1's origin reads block one.
        walked = {cell.key for cell in rows.get(ArmKey(ctx.parent.id), NO_CELLS)}
        return [s for s in panel.cells if s.key in walked] or panel.cells[: panel.block_size]

    def select(
        self, ctx: NodeContext[PopulationKnobs], measured: MeasuredRound, proposals: Proposals
    ) -> Selection:
        size = ctx.knobs.size
        survivors = measured.electable
        objective = ctx.slice(CapoObjective)
        rows = {key: measured.rows[key] for key in survivors}
        kept = _rank_survivors(survivors, rows, size=size, objective=objective)
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
        best = kept[0] if kept else None
        if kept:
            ctx.keep([measured.arms[key].individual for key in kept])
        carried = ctx.population
        verdict = (
            f"kept {len(kept)} of {len(survivors)} surviving arms (μ {size}) by mean objective "
            f"(length penalty {objective.length_penalty}) on their shared cells; "
            f"best {measured.labels[best]}"
            if best is not None
            else f"no arm survived the race; the population of {len(carried)} stands"
        )
        return nodes.Selection(
            selected=best if best is not None and best != ctx.parent.id else None,
            # The race's best survivor, which the incumbent may be: it races as an arm.
            leading=best,
            verdict_reason=verdict,
        )


def _replay_paired_t_cut(
    ctx: ReplayContext, inputs_ref: dict[str, Any], data: dict[str, Any]
) -> bool:
    rows = ctx.round_data.rows
    objective = _recorded_objective(inputs_ref)
    arm = _cell_objectives(
        rows(ArmKey(inputs_ref["arm"])).cells[: int(inputs_ref["queries_scored"])],
        objective,
    )
    rivals = {
        ArmKey(pid): _cell_objectives(rows(ArmKey(pid)).cells, objective)
        for pid in inputs_ref["raced_against"]
    }
    alpha = float(inputs_ref["alpha"])
    beaten = sum(1 for p, _ in _readings(arm, rivals).values() if p < alpha)
    return beaten >= int(inputs_ref["survivors"])


def _replay_population_kept(
    ctx: ReplayContext, inputs_ref: dict[str, Any], data: dict[str, Any]
) -> list[ArmKey]:
    survivors = [ArmKey(str(key)) for key in inputs_ref["survivors"]]
    return _rank_survivors(
        survivors,
        {key: ctx.round_data.rows(key) for key in survivors},
        size=int(inputs_ref["size"]),
        objective=_recorded_objective(inputs_ref),
    )


def _recorded_objective(inputs_ref: Mapping[str, Any]) -> CapoObjective:
    return CapoObjective(
        length_penalty=float(inputs_ref["length_penalty"]),
        length_norm=int(inputs_ref["length_norm"]),
    )


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

    def refuse_origin(self, origin_results: CellSheet) -> None:
        if prompt_chars(origin_results) is None:
            raise ValueError(
                "CAPO's objective charges each cell for its prompt's length, and this pipeline's "
                "rows carry no target_prompt_chars: it renders no prompt node CAPO could edit"
            )

    def arms(self, selected: SelectedOptimizer) -> int:
        # The population races beside the round's offspring (`PopulationRejoin`).
        size = selected.knobs_of(PopulationSelector.name, PopulationKnobs).size
        return size + selected.knobs_of(Mating.name, MatingKnobs).offspring

    def round_cells_ceiling(self, selected: SelectedOptimizer, search: int) -> int:
        # Round 1's origin is no member: its reading is one block beside the arms'.
        block = selected.knobs_of(Blocks.name, BlocksKnobs).block_size
        panel = selected.round_cells(search)
        return self.arms(selected) * panel + block if panel else 0

    def round_facts(
        self, selected: SelectedOptimizer, round_result: RoundResult
    ) -> list[OptimizerFact]:
        if round_result.round == 0:
            return []
        arms = round_result.panel
        cut = sum(1 for arm in arms.values() if isinstance(arm, Cut) and arm.kind == "eliminated")
        block_size = selected.knobs_of(Blocks.name, BlocksKnobs).block_size
        deepest = max(
            (-(-len(round_result.rows(key)) // block_size) for key in arms),
            default=0,
        )
        kept = len(round_result.closed_state.population)
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
