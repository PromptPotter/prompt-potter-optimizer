from __future__ import annotations

import ast
import random
from collections.abc import Mapping, Sequence
from typing import Annotated, Any, ClassVar

from pydantic import Field

from promptpotter.application import optimizer_contract
from promptpotter.application.bench.node_context import (
    Calls,
    Children,
    Lineage,
    NodeContext,
    Progress,
    Replay,
    Target,
)
from promptpotter.application.bench.resume_and_fork.replayers import ReplayContext, Replayed
from promptpotter.application.optimizer_contract import (
    CycleRead,
    Draw,
    MeasuredRound,
    PromptArity,
    Proposals,
    Proposing,
    Ranking,
    RewriteKnobs,
    RoundSamples,
)
from promptpotter.application.optimizer_manifest import SelectedOptimizer
from promptpotter.application.optimizers.descriptors import prompt_chars, shared_cells
from promptpotter.application.optimizers.paper_templates import (
    rewrite,
    rewritten,
    shown,
    task_description,
    unmarked,
)
from promptpotter.application.optimizers.variation.members import Survival, SurvivalKnobs
from promptpotter.application.scoring.race_protocol import BlockRace, View
from promptpotter.domain.arm_reading import DisplayMetric
from promptpotter.domain.arms import Elimination, Proposal
from promptpotter.domain.campaign_config import Estimand, Knob
from promptpotter.domain.opt_search_point import Locus, OptSearchPoint, VariationMode
from promptpotter.domain.optimizer_state import StatePart, StateSlice
from promptpotter.domain.paired_reading import ArmKey
from promptpotter.domain.pipeline_schema import NodeKind, PipelineSchema
from promptpotter.domain.pool import DemoPool, Pool
from promptpotter.domain.results import OptimizerFact, RoundResult
from promptpotter.domain.sample import Sample
from promptpotter.domain.scoring import NO_CELLS, ROW_GRADES, CellSheet, GradedCell
from promptpotter.domain.search_point import SHOTS_KEY
from promptpotter.domain.strict_model import StrictModel
from promptpotter.domain.validators import StopSignal
from promptpotter.infrastructure.store.io import parse_bounded
from promptpotter.shared.statistics import paired_mean_t


# The population CAPO's selector keeps is the bench's (`OptimizerState.population`), banked beside this.
class CapoObjective(StateSlice, kind="capo_objective"):
    """What the race and the population rank on (§4), set by the initial population, which writes it first."""

    length_penalty: float
    # The longest initial prompt's scored length, the length term's divisor.
    length_norm: int

    def of(self, cell: GradedCell, chars: int) -> float:
        """Unclamped, so a long prompt can take a cell below zero."""
        fitness = float(ROW_GRADES["fitness"].read(cell))
        return fitness - self.length_penalty * chars / self.length_norm


def init_prompt(ctx: NodeContext) -> str:
    node = ctx.read(Target).prompt_node
    return ctx.read(Calls).prompt().compile_prompt(task_description=task_description(ctx, node))


def crossover_prompt(ctx: NodeContext, mother: OptSearchPoint, father: OptSearchPoint) -> str:
    node = ctx.read(Target).prompt_node
    return (
        ctx.read(Calls)
        .prompt()
        .compile_prompt(
            task_description=task_description(ctx, node),
            mother=shown(ctx, mother, node),
            father=shown(ctx, father, node),
        )
    )


def mutation_prompt(ctx: NodeContext, individual: OptSearchPoint) -> str:
    node = ctx.read(Target).prompt_node
    return (
        ctx.read(Calls)
        .prompt()
        .compile_prompt(
            task_description=task_description(ctx, node), instruction=shown(ctx, individual, node)
        )
    )


class PairedTCut(Replayed, kind="paired_t_cut"):
    """The rivals are named, not their rows: a replay reads every arm off the rescored round."""

    objective: CapoObjective
    arm: ArmKey
    queries_scored: int
    alpha: float
    survivors: int
    raced_against: list[ArmKey]
    outscored_by: list[ArmKey]

    def replay(self, ctx: ReplayContext) -> bool:
        rows = ctx.round_data.rows
        arm = _cell_objectives(rows(self.arm).cells[: self.queries_scored], self.objective)
        rivals = {
            pid: _cell_objectives(rows(pid).cells, self.objective) for pid in self.raced_against
        }
        beaten = sum(1 for p, _ in _readings(arm, rivals).values() if p < self.alpha)
        return beaten >= self.survivors


class PopulationKept(Replayed, kind="population_kept"):
    survivors: list[ArmKey]
    # Each survivor's scores on the cells earlier rounds read it on, which a replay's one round lacks.
    earlier: dict[ArmKey, dict[str, float]]

    def means(self, rows: Mapping[ArmKey, CellSheet]) -> dict[ArmKey, float]:
        """promptolution's CAPO: each survivor's mean raw score over every cell it was evaluated
        on, its cache across rounds included; an arm with no graded cell is unranked."""
        return {
            key: sum(read.values()) / len(read)
            for key in self.survivors
            if (read := {**self.earlier.get(key, {}), **_scores(rows[key].cells)})
        }

    def rank(self, rows: Mapping[ArmKey, CellSheet]) -> list[ArmKey]:
        """Best-first on ``means``."""
        means = self.means(rows)
        return sorted(means, key=lambda key: -means[key])

    def replay(self, ctx: ReplayContext) -> list[ArmKey]:
        return self.rank({key: ctx.round_data.rows(key) for key in self.survivors})


_SHOT_MOVES = ("add", "drop", "reorder")


def mutate_shots(
    shots: Sequence[str], pool: Sequence[str], *, k_max: int, rng: random.Random
) -> list[str]:
    """promptolution's three moves, drawn uniformly: add a pool shot, drop one, or only reorder."""
    out = list(shots)
    move = rng.choice(_SHOT_MOVES)
    unused = [i for i in pool if i not in out]
    if move == "add" and len(out) < k_max and unused:
        out.append(rng.choice(unused))
    elif move == "drop" and out:
        out.pop(rng.randrange(len(out)))
    rng.shuffle(out)
    return out


def cross_shots(parents: Sequence[Sequence[str]], *, rng: random.Random) -> list[str]:
    union = list(dict.fromkeys(i for shots in parents for i in shots))
    n = sum(len(shots) for shots in parents) // len(parents)
    return rng.sample(union, min(n, len(union)))


class FewShot(optimizer_contract.Proposer):
    name: ClassVar[str] = "few_shot"
    kind: ClassVar[NodeKind] = NodeKind.ALGORITHM
    prompt_arity: ClassVar[PromptArity] = "one"
    proposes: ClassVar[Proposing] = "edits"
    variation_mode: ClassVar[VariationMode] = "deterministic"
    knobs: ClassVar[type[StrictModel]] = optimizer_contract.ShotKnobs
    sees: ClassVar[frozenset[type[CycleRead]]] = frozenset({Children, Replay, Target})
    draws: ClassVar[Mapping[type[Pool], Draw]] = {
        DemoPool: optimizer_contract.CountKnob(optimizer_contract.ShotKnobs, "k_max")
    }

    async def propose(self, ctx: NodeContext, proposals: Proposals) -> Proposals:
        k_max = ctx.knobs(optimizer_contract.ShotKnobs).k_max
        pool = [s.key for s in ctx.drawn(DemoPool)]
        node = ctx.read(Target).prompt_node.name
        children, replay = ctx.read(Children), ctx.read(Replay)
        return optimizer_contract.Proposals(
            [
                children.edit(
                    proposal,
                    prompt_loci={
                        Locus(node, SHOTS_KEY): mutate_shots(
                            proposal.individual.prompt(node).shots,
                            pool,
                            k_max=k_max,
                            rng=replay.rng(f":{i}"),
                        )
                    },
                )
                for i, proposal in enumerate(proposals.proposals)
            ]
        )


class BlocksKnobs(StrictModel):
    block_size: Annotated[int, Knob(Estimand.SELECTION, Estimand.STOPPING)] = Field(
        ge=1,
        description="Cells per block. An eliminator decides only once a block is complete, and a "
        "paired t-test read on one block wants 30 or more.",
    )
    max_blocks: Annotated[int, Knob(Estimand.SELECTION)] = Field(
        ge=1,
        description="The most blocks a round's panel holds — fewer where the search split cannot "
        "fill them.",
    )


class Blocks(optimizer_contract.Sampler):
    """The same cells in the same blocks every round, resume and fork: never shuffled."""

    name: ClassVar[str] = "blocks"
    kind: ClassVar[NodeKind] = NodeKind.SAMPLER
    knobs: ClassVar[type[StrictModel]] = BlocksKnobs
    size_knob: ClassVar[str | None] = None

    def drawn(self, knobs: BlocksKnobs, search: int, _pipeline: str) -> optimizer_contract.DrawSize:
        n_blocks = min(knobs.max_blocks, search // knobs.block_size)
        return optimizer_contract.DrawSize(
            cells=n_blocks * knobs.block_size, block_size=knobs.block_size
        )

    def round_facts(
        self, selected: SelectedOptimizer, round_result: RoundResult
    ) -> list[OptimizerFact]:
        if round_result.round == 0:
            return []
        block_size = selected.knobs_of(self.name, BlocksKnobs).block_size
        deepest = max(
            (-(-len(round_result.rows(key)) // block_size) for key in round_result.proposed),
            default=0,
        )
        return [
            OptimizerFact(
                key="blocks", label="Blocks raced", text=str(deepest), value=deepest, kind="stat"
            )
        ]

    def draw(self, ctx: NodeContext, pool: list[Sample]) -> RoundSamples:
        knobs = ctx.knobs(BlocksKnobs)
        n_blocks = min(knobs.max_blocks, len(pool) // knobs.block_size)
        if n_blocks == 0:
            raise ValueError(
                f"blocks: the search split holds {len(pool)} rows, fewer than one block of "
                f"{knobs.block_size}"
            )
        cells = pool[: n_blocks * knobs.block_size]
        return optimizer_contract.RoundSamples(
            cells=cells, order=list(cells), block_size=knobs.block_size
        )


class PairedTKnobs(StrictModel):
    alpha: Annotated[float, Knob(Estimand.STOPPING)] = Field(
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


def _scores(cells: Sequence[GradedCell]) -> dict[str, float]:
    return {c.key: float(ROW_GRADES["fitness"].read(c)) for c in cells if c.scored}


def _readings(
    arm: Mapping[str, float], rivals: Mapping[ArmKey, Mapping[str, float]]
) -> dict[ArmKey, tuple[float, int]]:
    """A rival sharing under two cells with *arm* was not tested, and is absent from the result."""
    out: dict[ArmKey, tuple[float, int]] = {}
    for pid, rival in rivals.items():
        shared = shared_cells(arm, rival)
        p = paired_mean_t([rival[s] for s in shared], [arm[s] for s in shared], tail="greater")[3]
        if p is not None:
            out[pid] = (p, len(shared))
    return out


def _labels(labels: list[str], cap: int = 2) -> str:
    return ", ".join(labels[:cap]) + (f" (+{len(labels) - cap})" if len(labels) > cap else "")


class PairedTRace(BlockRace):
    def __init__(
        self,
        ctx: NodeContext,
        *,
        survivors: int,
        block_size: int,
        n_cells: int,
        objective: CapoObjective,
    ) -> None:
        super().__init__(ctx.node, block_size=block_size, n_cells=n_cells)
        self._progress, self._replay = ctx.read(Progress), ctx.read(Replay)
        self._alpha = ctx.knobs(PairedTKnobs).alpha
        self._survivors = survivors
        self._objective = objective

    @property
    def n_priors(self) -> int:
        return len(self.racing) - 1

    def cut(self, view: View[ArmKey]) -> dict[ArmKey, StopSignal]:
        rows = {key: walk.taken for key, walk in view.walks.items()}
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
            breakdown = {pid: p for pid, (p, _) in tested.items()}
            p_best = min(p for p, _ in tested.values())
            snapshot = optimizer_contract.RaceSnapshot(p_best, cid, len(arm_rows), breakdown)
            self._progress.race_standing(snapshot)
            # Uncorrected for the multiple tests, as CAPO races: a correction makes cuts rarer.
            beaten_by = sorted(pid for pid, (p, _) in tested.items() if p < self._alpha)
            if len(beaten_by) >= self._survivors:
                stops[cid] = self.signal(
                    "eliminated",
                    "outscored",
                    len(arm_rows),
                    raced_against=sorted(rivals),
                    outscored_by=beaten_by,
                    p_best=p_best,
                    paired_breakdown=breakdown,
                )
        left = [cid for cid in rows if cid not in stops]
        if len(left) <= self._survivors:
            for cid in left:
                if len(rows[cid]) < self.n_cells:
                    stops[cid] = self.signal(
                        "locked_in",
                        "settled",
                        len(rows[cid]),
                        raced_against=sorted(pid for pid in rows if pid != cid),
                    )
        return stops

    def ruling(
        self,
        signal: StopSignal,
        context: dict[str, Any],
        key: ArmKey,
        labels: Mapping[ArmKey, str],
    ) -> Elimination:
        cr = signal.check_result

        def walk_order(keys: list[ArmKey]) -> list[ArmKey]:
            return [key for key in labels if key in keys]

        context["block"] = -(-cr["queries_scored"] // self.block_size)
        context["blocks"] = -(-cr["total_samples"] // self.block_size)
        context["raced_against"] = walk_order(cr["raced_against"])
        where = (
            f"at block {context['block']}/{context['blocks']} "
            f"(q{cr['queries_scored']}/{cr['total_samples']})"
        )
        if signal.outcome != "eliminated":
            reason = f"settled {where}: {self._survivors} or fewer left racing"
            return Elimination(reason=reason, context=context)
        self._replay.decide(
            PairedTCut(
                objective=self._objective,
                arm=key,
                queries_scored=cr["queries_scored"],
                alpha=self._alpha,
                survivors=self._survivors,
                raced_against=cr["raced_against"],
                outscored_by=cr["outscored_by"],
            ),
            True,
        )
        context["outscored_by"] = walk_order(cr["outscored_by"])
        outscored, raced = context["outscored_by"], context["raced_against"]
        named = _labels([labels[k] for k in outscored])
        reason = f"outscored {where} by {len(outscored)} of {len(raced)}: {named}"
        return Elimination(reason=reason, context=context)


class PairedT(optimizer_contract.Eliminator):
    name: ClassVar[str] = "paired_t"
    kind: ClassVar[NodeKind] = NodeKind.ELIMINATOR
    knobs: ClassVar[type[StrictModel]] = PairedTKnobs
    reads: ClassVar[frozenset[type[StatePart]]] = frozenset({CapoObjective})
    sees: ClassVar[frozenset[type[CycleRead]]] = frozenset({Progress, Replay})
    abort_lenses: ClassVar[Mapping[str, frozenset[str]]] = {}
    # Cut once μ others are significantly better: it can no longer be among the μ kept.
    stop_disqualifies: ClassVar[bool] = True

    consults: ClassVar[frozenset[type]] = frozenset({Survival})

    def round_facts(
        self, _selected: SelectedOptimizer, round_result: RoundResult
    ) -> list[OptimizerFact]:
        if round_result.round == 0:
            return []
        arms = round_result.proposed
        cut = optimizer_contract.eliminated_arms(round_result)
        return [
            OptimizerFact(
                key="cut", label="Cut", text=f"{cut} of {len(arms)} arms", value=cut, kind="stat"
            )
        ]

    def race(self, ctx: NodeContext, panel: RoundSamples) -> PairedTRace:
        return PairedTRace(
            ctx,
            survivors=ctx.knobs(SurvivalKnobs).size,
            block_size=panel.block_size,
            n_cells=len(panel.order),
            objective=ctx.slice(CapoObjective),
        )


class CapoInitKnobs(RewriteKnobs):
    size: Annotated[int, Knob(Estimand.SEARCH)] = Field(
        ge=2, description="The individuals the initial population holds — the paper's μ."
    )
    k_max: Annotated[int, Knob(Estimand.SEARCH)] = Field(
        ge=0, description="The most demo-pool shots an initial individual is given."
    )
    length_penalty: Annotated[float, Knob(Estimand.SELECTION, Estimand.STOPPING)] = Field(
        ge=0.0,
        description="The paper's gamma: what CAPO's objective charges per cell for the "
        "prompt's length over the longest initial prompt's. The race and the population both "
        "rank on that objective; the campaign's formula still scores every arm.",
    )


def seed_instructions(raw: str) -> list[str] | None:
    """The reply's array of instructions, stripped; ``None`` out of form, an array holding no
    non-blank string included."""
    start, end = raw.find("["), raw.rfind("]")
    try:
        listed = parse_bounded(ast.literal_eval, raw[start : end + 1]) if 0 <= start < end else None
    except ValueError:
        return None
    if not isinstance(listed, list) or not all(isinstance(s, str) for s in listed):
        return None
    return [s.strip() for s in listed if s.strip()] or None


class CapoInit(optimizer_contract.Proposer):
    """Draws the initial population, which sets the objective every later race and selection ranks on."""

    name: ClassVar[str] = "capo_init"
    kind: ClassVar[NodeKind] = NodeKind.LLM
    prompt_arity: ClassVar[PromptArity] = "one"
    proposes: ClassVar[Proposing] = "opens"
    variation_mode: ClassVar[VariationMode] = "llm"
    knobs: ClassVar[type[StrictModel]] = CapoInitKnobs
    writes: ClassVar[frozenset[type[StatePart]]] = frozenset({CapoObjective})
    sees: ClassVar[frozenset[type[CycleRead]]] = frozenset(
        {Calls, Children, Lineage, Replay, Target}
    )
    draws: ClassVar[Mapping[type[Pool], Draw]] = {
        DemoPool: optimizer_contract.CountKnob(CapoInitKnobs, "k_max")
    }

    def arms(self, selected: SelectedOptimizer) -> int:
        return selected.knobs_of(self.name, CapoInitKnobs).size

    def refuse_pipeline(self, schema: PipelineSchema) -> None:
        if not schema.prompt_nodes():
            raise ValueError(
                "CAPO's objective charges each cell for its prompt's length, and this pipeline "
                "renders no prompt node CAPO could edit"
            )

    async def propose(self, ctx: NodeContext, _proposals: Proposals) -> Proposals:
        knobs = ctx.knobs(CapoInitKnobs)
        raw = await ctx.read(Calls).ask(init_prompt(ctx))
        listed = seed_instructions(raw)
        instructions = listed or []
        pool = [s.key for s in ctx.drawn(DemoPool)]
        target = ctx.read(Target)
        node = target.prompt_node
        shots = Locus(node.name, SHOTS_KEY)
        rng = ctx.read(Replay).rng()
        drawn = rng.sample(instructions, min(knobs.size, len(instructions)))
        children = ctx.read(Children)
        origin = ctx.read(Lineage).origin
        # Out of form, the reply is one child refused, as every text node's is (`paper_templates.py`).
        initial = optimizer_contract.Proposals(
            (
                []
                if listed is not None
                else [children.child([origin], failures=[unmarked(self.name, raw)])]
            )
            + [
                children.child(
                    [origin],
                    prompt_loci={
                        **rewritten(ctx, node, text),
                        shots: rng.sample(pool, min(rng.randint(0, knobs.k_max), len(pool))),
                    },
                    changes_description=f"initial instruction {n + 1}",
                )
                for n, text in enumerate(drawn)
            ]
        )
        # The longest initial prompt that stands; where none stands, the origin each was drawn from.
        live = [p.individual for p in initial.proposals if not p.invalid] or [origin]
        ctx.put(
            CapoObjective(
                length_penalty=knobs.length_penalty,
                length_norm=max(
                    len(ind.render_target(node, target.framing, demo=target.demo_split))
                    for ind in live
                ),
            )
        )
        return initial


def _mated(ctx: NodeContext, proposals: Proposals) -> list[list[OptSearchPoint]]:
    lineage = ctx.read(Lineage)
    return [lineage.parents(p.individual) for p in proposals.proposals]


class CapoCrossover(optimizer_contract.Proposer):
    name: ClassVar[str] = "capo_crossover"
    kind: ClassVar[NodeKind] = NodeKind.LLM
    prompt_arity: ClassVar[PromptArity] = "one"
    proposes: ClassVar[Proposing] = "edits"
    variation_mode: ClassVar[VariationMode] = "llm"
    knobs: ClassVar[type[StrictModel]] = RewriteKnobs
    sees: ClassVar[frozenset[type[CycleRead]]] = frozenset({Calls, Children, Lineage, Target})

    async def propose(self, ctx: NodeContext, proposals: Proposals) -> Proposals:
        pairs = _mated(ctx, proposals)
        if lone := [len(pair) for pair in pairs if len(pair) != 2]:
            raise ValueError(f"a crossover merges two parents; an offspring names {lone[0]}")
        answers = await ctx.read(Calls).ask_each(
            {i: crossover_prompt(ctx, a, b) for i, (a, b) in enumerate(pairs)}
        )
        return optimizer_contract.Proposals(
            [rewrite(ctx, p, raw) for p, raw in zip(proposals.proposals, answers, strict=True)]
        )


class ShotCrossover(optimizer_contract.Proposer):
    name: ClassVar[str] = "shot_crossover"
    kind: ClassVar[NodeKind] = NodeKind.ALGORITHM
    prompt_arity: ClassVar[PromptArity] = "one"
    proposes: ClassVar[Proposing] = "edits"
    variation_mode: ClassVar[VariationMode] = "deterministic"
    sees: ClassVar[frozenset[type[CycleRead]]] = frozenset({Children, Lineage, Replay, Target})

    async def propose(self, ctx: NodeContext, proposals: Proposals) -> Proposals:
        rng = ctx.read(Replay).rng()
        node = ctx.read(Target).prompt_node.name
        children = ctx.read(Children)
        return optimizer_contract.Proposals(
            [
                children.edit(
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


class CapoMutate(optimizer_contract.Proposer):
    """A child its crossover already failed is passed on untouched: it will never be measured."""

    name: ClassVar[str] = "capo_mutate"
    kind: ClassVar[NodeKind] = NodeKind.LLM
    prompt_arity: ClassVar[PromptArity] = "one"
    proposes: ClassVar[Proposing] = "edits"
    variation_mode: ClassVar[VariationMode] = "llm"
    knobs: ClassVar[type[StrictModel]] = RewriteKnobs
    sees: ClassVar[frozenset[type[CycleRead]]] = frozenset({Calls, Children, Lineage, Target})

    async def propose(self, ctx: NodeContext, proposals: Proposals) -> Proposals:
        live = [i for i, p in enumerate(proposals.proposals) if not p.invalid]
        answers = await ctx.read(Calls).ask_each(
            {i: mutation_prompt(ctx, proposals.individuals[i]) for i in live}
        )
        mutated = list(proposals.proposals)
        for i, raw in zip(live, answers, strict=True):
            was = mutated[i].individual.lineage.changes_description
            mutated[i] = rewrite(ctx, mutated[i], raw, f"{was}, rephrased")
        return optimizer_contract.Proposals(mutated)


class PopulationRejoin(optimizer_contract.Proposer):
    """Members go AHEAD of the offspring: they walk first, so every offspring is raced against them."""

    name: ClassVar[str] = "population_rejoin"
    kind: ClassVar[NodeKind] = NodeKind.ALGORITHM
    proposes: ClassVar[Proposing] = "adds"
    variation_mode: ClassVar[VariationMode] = "deterministic"
    sees: ClassVar[frozenset[type[CycleRead]]] = frozenset({Lineage})
    consults: ClassVar[frozenset[type]] = frozenset({Survival})

    def arms(self, selected: SelectedOptimizer) -> int:
        return selected.reached_knobs(self, SurvivalKnobs).size

    async def propose(self, ctx: NodeContext, proposals: Proposals) -> Proposals:
        return optimizer_contract.Proposals(
            [
                *(Proposal(individual=m) for m in ctx.read(Lineage).population),
                *proposals.proposals,
            ]
        )


class PopulationSelector(optimizer_contract.RankingSelector):
    """Ranks the race's survivors as the reference ranks them (`PopulationKept.rank`); `survival`
    keeps the top μ."""

    name: ClassVar[str] = "population"
    kind: ClassVar[NodeKind] = NodeKind.SELECTOR
    sees: ClassVar[frozenset[type[CycleRead]]] = frozenset({Lineage, Replay})
    elects_partial: ClassVar[bool] = True
    elects_on: ClassVar[DisplayMetric] = "fitness"

    def parent_cells(
        self,
        ctx: NodeContext,
        panel: RoundSamples,
        rows: Mapping[ArmKey, CellSheet],
    ) -> list[Sample]:
        # The incumbent's reading is the cells it walked as a member; round 1's origin reads block one.
        walked = {cell.key for cell in rows.get(ArmKey(ctx.read(Lineage).parent.id), NO_CELLS)}
        return [s for s in panel.cells if s.key in walked] or panel.cells[: panel.block_size]

    def parent_read(self, panel: optimizer_contract.DrawSize) -> int:
        # Round 1's origin is no arm and reads block one; later the incumbent races as an arm.
        return panel.block_size

    def rank(self, ctx: NodeContext, measured: MeasuredRound) -> Ranking:
        lineage = ctx.read(Lineage)
        earlier = {
            key: read
            for key in measured.electable
            if (
                read := {
                    k: v for rr in lineage.rounds for k, v in _scores(rr.rows(key).cells).items()
                }
            )
        }
        inputs = PopulationKept(survivors=measured.electable, earlier=earlier)
        ranked = ctx.read(Replay).decide(inputs, inputs.rank(measured.rows))
        carried = lineage.population
        verdict = (
            f"ranked {len(ranked)} surviving arms by mean score on every cell each was read "
            f"on; best {measured.labels[ranked[0]]}"
            if ranked
            else f"no arm survived the race; the population of {len(carried)} stands"
        )
        # The incumbent races as an arm, so it stands on the score every arm does.
        means = inputs.means(measured.rows)
        return Ranking(
            standing=means,
            arms=means,
            verdict_reason=verdict,
            ranked=[measured.arms[key].individual for key in ranked] if ranked else carried,
        )


MEMBERS = (
    FewShot(),
    Blocks(),
    PairedT(),
    CapoInit(),
    CapoCrossover(),
    ShotCrossover(),
    CapoMutate(),
    PopulationRejoin(),
    PopulationSelector(),
)
