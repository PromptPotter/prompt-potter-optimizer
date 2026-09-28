"""CAPO's node implementations, each registered under the node name its manifest uses, and its
runtime, registered under the manifest's."""

from __future__ import annotations

import ast
import asyncio
import hashlib
import inspect
import random
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, replace
from typing import TYPE_CHECKING, Annotated, Any, ClassVar, cast

from pydantic import Field

from promptpotter.application.campaign_config import Estimand, Knob, Scope
from promptpotter.application.optimization.resume_and_fork.decisions import (
    GatingMode,
    record_decision,
)
from promptpotter.application.optimizers import nodes, paper_templates
from promptpotter.application.optimizers.capo import operators
from promptpotter.application.optimizers.capo.operators import initial_population
from promptpotter.application.optimizers.capo.state import CapoState, capo_state
from promptpotter.application.optimizers.paper_templates import (
    ask,
    fill,
    marked,
    task_description,
    unmarked,
    walk_rng,
)
from promptpotter.application.scoring.candidate_report import fatal_validation_failures
from promptpotter.domain.opt_search_point import IndividualLineage, OptSearchPoint, node_source
from promptpotter.domain.optimizer_state import CAPO_MANIFEST
from promptpotter.domain.pipeline_schema import NodeKind
from promptpotter.domain.results import ArmOutcome, CandidateProposal, candidate_label
from promptpotter.domain.run_records import CandidateMintedRecord, CapoCheckpointKind
from promptpotter.domain.strict_model import StrictModel
from promptpotter.domain.validators import StopSignal
from promptpotter.shared.errors import is_error_result
from promptpotter.shared.statistics import paired_reading

if TYPE_CHECKING:
    from types import ModuleType

    from promptpotter.application.campaign_config import CampaignConfig
    from promptpotter.application.initialization.session import Session
    from promptpotter.application.optimization.cycle import Cycle
    from promptpotter.application.optimization.resume_and_fork.replayers import (
        ReplayContext,
        Replayer,
    )
    from promptpotter.application.optimizer_manifest import SelectedOptimizer
    from promptpotter.application.optimizers.nodes import (
        CatchUpFn,
        Measured,
        Panel,
        Population,
        RaceSnapshot,
        ReviewReading,
        RoundContext,
        Selection,
    )
    from promptpotter.application.scoring.query_loop import Walk
    from promptpotter.domain.cycle_paths import CycleHop
    from promptpotter.domain.results import RoundResult
    from promptpotter.domain.run_records import ResumeCheckpointKind, ResumeCheckpointRecord
    from promptpotter.domain.sample import Sample
    from promptpotter.domain.scoring import QueryMeasurement
    from promptpotter.domain.search_point import JobSearchPoint
    from promptpotter.infrastructure.store.campaign_store.store import CampaignStore

__all__ = [
    "MEMBERS",
    "RUNTIME",
    "BlocksKnobs",
    "CapoCrossoverKnobs",
    "FewShotKnobs",
    "PairedTKnobs",
    "PopulationKnobs",
    "cross_shots",
    "mutate_shots",
]


class FewShotKnobs(StrictModel):
    k_max: Annotated[int, Knob(Scope.POLICY, Estimand.SEARCH)] = Field(
        ge=0, description="The most shots an individual may carry after the mutation."
    )


def mutate_shots(
    shots: Sequence[int], pool: Sequence[int], *, k_max: int, rng: random.Random
) -> list[int]:
    """Add a pool row not already carried while under *k_max*, drop one, or keep them — each with
    probability 1/3, a move with nothing to act on keeping instead — then shuffle the order."""
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
    """A crossover child's shots: a sample of the parents' union, as many as their mean count
    rounded down. The recombining node calls it, being the one that holds the parents."""
    union = list(dict.fromkeys(i for shots in parents for i in shots))
    n = sum(len(shots) for shots in parents) // len(parents)
    return rng.sample(union, min(n, len(union)))


class FewShot:
    """Mutates the shots of every individual the proposer before it made, off the demo pool. Its
    draw is a function of the run's seed, the round and the individual's position alone."""

    name: ClassVar[str] = "few_shot"
    kind: ClassVar[NodeKind] = NodeKind.ALGORITHM
    knobs: ClassVar[type[StrictModel]] = FewShotKnobs
    couplings: ClassVar[tuple[nodes.MemberCoupling, ...]] = ()

    async def propose(
        self, ctx: RoundContext, panel: Panel, population: Population | None
    ) -> Population:
        if population is None:
            raise ValueError(
                "few_shot edits the shots of individuals a proposer before it made; a manifest "
                "walking it first hands it none"
            )
        cycle = ctx.cycle
        k_max = cast("FewShotKnobs", cycle.optimizer.knobs(self.name)).k_max
        pool = [s.id for s in cycle.session.scoring.require_partition().demo]
        clamp = cycle.config.optimization.determinism
        seed = None if clamp is None else clamp.seed
        proposals, individuals = [], []
        for i, (proposal, individual) in enumerate(
            zip(population.proposals, population.individuals, strict=True)
        ):
            rng = random.Random(f"{seed}:{ctx.round_num}:{i}")
            shots = mutate_shots(individual.shot_ids, pool, k_max=k_max, rng=rng)
            mutated = individual.model_copy(update={"shot_ids": shots})
            proposals.append(proposal.model_copy(update={"opt_sp": mutated}))
            individuals.append(mutated)
        return replace(population, proposals=proposals, individuals=individuals)


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
    """CAPO's panel: the first ``max_blocks`` whole blocks of the search pool, in the bank's order —
    the same cells in the same blocks every round, resume and fork. Never shuffled."""

    name: ClassVar[str] = "blocks"
    kind: ClassVar[NodeKind] = NodeKind.SAMPLER
    knobs: ClassVar[type[StrictModel]] = BlocksKnobs
    couplings: ClassVar[tuple[nodes.MemberCoupling, ...]] = ()

    def draw(self, ctx: RoundContext, pool: list[Sample]) -> Panel:
        knobs = cast("BlocksKnobs", ctx.cycle.optimizer.knobs(self.name))
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
    survivors: Annotated[int, Knob(Scope.POLICY, Estimand.STOPPING)] = Field(
        ge=1,
        description="μ, the arms the race keeps: an arm is cut once this many others are "
        "significantly better, since it can no longer be among them.",
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
    """CAPO's objective on one cell (§4): correctness less γ times the scored prompt's length over
    the longest initial prompt's. Unclamped, so a long prompt can take a cell below zero."""

    length_penalty: float
    length_norm: int

    def of(self, row: Mapping[str, Any]) -> float:
        chars = float(row["pipeline_data"]["target_prompt_chars"])
        return float(row["fitness"]) - self.length_penalty * chars / self.length_norm


def _objective(ctx: RoundContext) -> _Objective:
    norm = capo_state(ctx.state).length_norm
    if norm is None:
        raise ValueError(
            "CAPO's objective divides by the longest initial prompt's length, and no initial "
            "population was generated before its race"
        )
    knobs = cast("PairedTKnobs", ctx.cycle.optimizer.knobs(PairedT.name))
    return _Objective(knobs.length_penalty, norm)


def _cell_objectives(rows: Sequence[Mapping[str, Any]], objective: _Objective) -> dict[str, float]:
    return {
        str(sid): objective.of(r)
        for r in rows
        if (sid := r.get("sample_id")) is not None and not is_error_result(r)
    }


def _rank_survivors(
    survivors: Sequence[str],
    rows: Mapping[str, Sequence[Mapping[str, Any]]],
    *,
    size: int,
    objective: _Objective,
) -> list[str]:
    """The ``size`` best survivors, best first, by mean objective over the cells every one of them
    measured (App. B, `do_racing` line 35). A tie, or no shared cell at all, keeps walk order."""
    readings = {cid: _cell_objectives(rows[cid], objective) for cid in survivors}
    shared = set.intersection(*(set(r) for r in readings.values())) if readings else set()

    def mean(cid: str) -> float:
        return sum(readings[cid][s] for s in shared) / len(shared) if shared else 0.0

    return sorted(survivors, key=mean, reverse=True)[:size]


def _readings(
    arm: Mapping[str, float], rivals: Mapping[str, Mapping[str, float]]
) -> dict[str, tuple[float, int]]:
    """Each rival's one-sided paired-t p that it outscores *arm* on the cells both measured, and
    that width. Below two shared cells nothing was tested, so the rival is absent."""
    out: dict[str, tuple[float, int]] = {}
    for pid, rival in rivals.items():
        shared = [sid for sid in arm if sid in rival]
        _, _, _, p, n = paired_reading(
            [rival[s] for s in shared], [arm[s] for s in shared], tail="greater"
        )
        if p is not None:
            out[pid] = (p, n)
    return out


class PairedTRace:
    """Every live arm walks each block, then at its close each is tested against every other live
    arm; the arms μ others beat are cut together, and once μ or fewer remain the race stops them
    where they stand (App. B, `do_racing`). Every arm walks the same panel, so no pair catches up."""

    gate = "outscored"
    settled = "settled"

    def __init__(
        self,
        knobs: PairedTKnobs,
        *,
        node: str,
        block_size: int,
        n_cells: int,
        round_num: int,
        on_snapshot: Callable[[str, int, int, int, RaceSnapshot], None],
        decisions: list[ResumeCheckpointRecord],
        objective: _Objective,
    ) -> None:
        self.node = node
        self._decisions = decisions
        self._alpha = knobs.alpha
        self._survivors = knobs.survivors
        self._objective = objective
        self._block_size = block_size
        self._n_cells = n_cells
        self._round_num = round_num
        self._on_snapshot = on_snapshot
        self._arms: dict[int, str] = {}
        self._n_arms = 0
        self._racing: set[int] = set()

    @property
    def n_priors(self) -> int:
        # The arms this one races at the next close: every other one still racing.
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

    def close(self, rows: Mapping[int, list[QueryMeasurement]]) -> dict[int, StopSignal]:
        readings = {self._arms[i]: _cell_objectives(r, self._objective) for i, r in rows.items()}
        stops: dict[int, StopSignal] = {}
        for i, arm_rows in rows.items():
            cid = self._arms[i]
            rivals = {pid: reading for pid, reading in readings.items() if pid != cid}
            tested = _readings(readings[cid], rivals)
            if not tested:
                continue
            # The one-sided p that a rival outscores the arm is also P(arm beats it) as a t
            # fiducial, which is what the stream's `p_better` reads.
            breakdown = {
                pid: {"p_better": p, "n_paired": float(width)} for pid, (p, width) in tested.items()
            }
            p_best = min(p for p, _ in tested.values())
            snapshot = nodes.RaceSnapshot(p_best, cid, len(arm_rows), breakdown)
            self._on_snapshot(self.node, self._round_num, i, self._n_arms, snapshot)
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
            # Every arm left is kept, so none walks on; one through its panel completes instead.
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
        results: list[QueryMeasurement],
        labels: dict[str, str],
    ) -> Mapping[str, Any] | None:
        if signal is None or signal.check_name != self.node:
            return None
        cr = signal.check_result

        def walk_order(pids: list[str]) -> list[str]:
            return [label for pid, label in labels.items() if pid in pids]

        context: dict[str, Any] = {
            "gate": cr["gate"],
            "queries_scored": cr["queries_scored"],
            "total_queries": cr["total_samples"],
            # The close that decided the arm, and the arms still racing at it.
            "block": -(-cr["queries_scored"] // self._block_size),
            "blocks": -(-cr["total_samples"] // self._block_size),
            "raced_against": walk_order(cr["raced_against"]),
        }
        if signal.outcome is not ArmOutcome.ELIMINATED:
            return context
        # The rivals are named, not their rows: a replay reads every arm off the rescored round.
        record_decision(
            self._decisions,
            CapoCheckpointKind.PAIRED_T_CUT,
            {
                "candidate_id": candidate_id,
                "round_num": self._round_num,
                "queries_scored": cr["queries_scored"],
                "alpha": self._alpha,
                "survivors": self._survivors,
                "length_penalty": self._objective.length_penalty,
                "length_norm": self._objective.length_norm,
                "raced_against": list(cr["raced_against"]),
            },
            True,
            node=self.node,
            data={"outscored_by": list(cr["outscored_by"])},
            round=self._round_num,
        )
        context["outscored_by"] = walk_order(cr["outscored_by"])
        return context

    def admit(self, candidate_id: str, results: list[QueryMeasurement], sp: JobSearchPoint) -> None:
        return None

    def start_backfill(self, sample: Sample, room: int) -> list[asyncio.Future[Any]]:
        return []

    def owed_backfills(self, sample: Sample) -> int:
        return 0

    def backfills_in_flight(self) -> list[asyncio.Future[Any]]:
        return []

    def backfills_for(self, sample: Sample) -> list[asyncio.Future[Any]]:
        return []

    def commit_backfills(self, sample: Sample) -> None:
        return None

    def bank_backfills(self, samples: Sequence[Sample]) -> None:
        return None

    def discard_backfills(self) -> None:
        return None


class PairedT:
    """CAPO's survival race: a paired t-test between the arms at each block the sampler closes."""

    name: ClassVar[str] = "paired_t"
    kind: ClassVar[NodeKind] = NodeKind.ELIMINATOR
    knobs: ClassVar[type[StrictModel]] = PairedTKnobs
    couplings: ClassVar[tuple[nodes.MemberCoupling, ...]] = ()
    abort_lenses: ClassVar[Mapping[str, frozenset[str]]] = {}

    def race(self, ctx: RoundContext, panel: Panel, catch_up: CatchUpFn) -> PairedTRace:
        return PairedTRace(
            cast("PairedTKnobs", ctx.cycle.optimizer.knobs(self.name)),
            node=self.name,
            block_size=panel.block_size,
            n_cells=len(panel.order),
            round_num=ctx.round_num,
            on_snapshot=ctx.callbacks.on_race_standing,
            decisions=ctx.cycle.pending_decisions,
            objective=_objective(ctx),
        )


class CapoCrossoverKnobs(StrictModel):
    crossovers: Annotated[int, Knob(Scope.POLICY, Estimand.SEARCH)] = Field(
        ge=1,
        description="c, the offspring one round makes — each from two parents drawn at random "
        "from the population, never by score.",
    )


class CapoCrossover:
    """Merges two parents' instructions into a child's, whose shots are drawn from the union of
    theirs. Generates the initial population first when the population is empty."""

    name: ClassVar[str] = "capo_crossover"
    kind: ClassVar[NodeKind] = NodeKind.LLM
    knobs: ClassVar[type[StrictModel]] = CapoCrossoverKnobs
    couplings: ClassVar[tuple[nodes.MemberCoupling, ...]] = ()

    async def propose(
        self, ctx: RoundContext, panel: Panel, population: Population | None
    ) -> Population:
        if population is not None:
            raise ValueError(
                "capo_crossover draws its parents from CAPO's population; a manifest walking it "
                "after another proposer hands it offspring it would discard"
            )
        cycle = ctx.cycle
        state = capo_state(ctx.state)
        if not state.population:
            state.population = await initial_population(
                ctx,
                size=cast("PopulationKnobs", cycle.optimizer.knobs(PopulationSelector.name)).size,
                k_max=cast("FewShotKnobs", cycle.optimizer.knobs(FewShot.name)).k_max,
            )
            demo = cycle.session.scoring.require_partition().demo
            state.length_norm = max(
                len(ind.render_target(cycle.framing, demo=demo)) for ind in state.population
            )
        if (n := len(state.population)) < 2:
            raise ValueError(f"a crossover needs two parents; the population holds {n}")
        crossovers = cast("CapoCrossoverKnobs", cycle.optimizer.knobs(self.name)).crossovers
        rng = walk_rng(cycle, ctx.round_num, self.name)
        pairs = [rng.sample(state.population, 2) for _ in range(crossovers)]
        shots = [cross_shots([a.shot_ids, b.shot_ids], rng=rng) for a, b in pairs]
        described = task_description(cycle)
        answers = await asyncio.gather(
            *(
                ask(
                    ctx,
                    self.name,
                    i,
                    fill(
                        cycle,
                        self.name,
                        task_description=described,
                        mother=a.instruction,
                        father=b.instruction,
                    ),
                )
                for i, (a, b) in enumerate(pairs)
            )
        )
        proposals: list[CandidateProposal] = []
        for (a, b), child_shots, raw in zip(pairs, shots, answers, strict=True):
            text = marked(raw)
            child = OptSearchPoint.derive(
                [a, b],
                source=node_source(CAPO_MANIFEST, self.name),
                changes_description=f"crossover {a.lineage.id[:6]}+{b.lineage.id[:6]}",
                instruction=a.instruction if text is None else text,
                shot_ids=child_shots,
            )
            failures = [] if text is not None else [unmarked(self.name, raw)]
            proposals.append(CandidateProposal(opt_sp=child, validation_failures=failures))
        assert cycle.tracking.current_sp is not None
        base = cycle.tracking.current_sp.pipeline_params
        return nodes.Population(
            proposals=proposals,
            individuals=[p.opt_sp for p in proposals],
            pipeline_params=[base] * len(proposals),
            optimizer_state=state.snapshot(
                cycle.optimizer.prompt_hashes(),
                population=state.population,
                rounds_without_advance=state.rounds_without_advance,
            ),
        )


class CapoMutateKnobs(StrictModel):
    """The rewrite's call config is all it has; it takes no knob of its own."""


class CapoMutate:
    """Rephrases each offspring's instruction. A child its crossover already failed is passed on
    untouched: it will never be measured."""

    name: ClassVar[str] = "capo_mutate"
    kind: ClassVar[NodeKind] = NodeKind.LLM
    knobs: ClassVar[type[StrictModel]] = CapoMutateKnobs
    couplings: ClassVar[tuple[nodes.MemberCoupling, ...]] = ()

    async def propose(
        self, ctx: RoundContext, panel: Panel, population: Population | None
    ) -> Population:
        if population is None:
            raise ValueError(
                "capo_mutate rephrases the offspring a crossover made; a manifest walking it first "
                "hands it none"
            )
        described = task_description(ctx.cycle)
        live = [
            i
            for i, p in enumerate(population.proposals)
            if not fatal_validation_failures(p.validation_failures)
        ]
        answers = await asyncio.gather(
            *(
                ask(
                    ctx,
                    self.name,
                    i,
                    fill(
                        ctx.cycle,
                        self.name,
                        task_description=described,
                        instruction=population.individuals[i].instruction,
                    ),
                )
                for i in live
            )
        )
        proposals = list(population.proposals)
        for i, raw in zip(live, answers, strict=True):
            proposal, child = proposals[i], population.individuals[i]
            text = marked(raw)
            if text is None:
                failures = [*proposal.validation_failures, unmarked(self.name, raw)]
                proposals[i] = proposal.model_copy(update={"validation_failures": failures})
                continue
            lineage = IndividualLineage(
                parent_ids=list(child.lineage.parent_ids),
                source=node_source(CAPO_MANIFEST, self.name),
                changes_description=f"{child.lineage.changes_description}, rephrased",
            )
            mutated = child.model_copy(update={"instruction": text, "lineage": lineage})
            proposals[i] = proposal.model_copy(update={"opt_sp": mutated})
        return replace(population, proposals=proposals, individuals=[p.opt_sp for p in proposals])


class PopulationRejoinKnobs(StrictModel):
    """It moves the population and nothing else; it takes no knob."""


class PopulationRejoin:
    """Puts the population CAPO kept back into the race ahead of the offspring — Alg. 1 line 12
    races `P ∪ P_off` — so its members walk first and every offspring is raced against them. The
    last proposer, so it mints the round's new individuals onto the ledger."""

    name: ClassVar[str] = "population_rejoin"
    kind: ClassVar[NodeKind] = NodeKind.ALGORITHM
    knobs: ClassVar[type[StrictModel]] = PopulationRejoinKnobs
    couplings: ClassVar[tuple[nodes.MemberCoupling, ...]] = ()

    async def propose(
        self, ctx: RoundContext, panel: Panel, population: Population | None
    ) -> Population:
        if population is None:
            raise ValueError(
                "population_rejoin races the population beside offspring a proposer before it "
                "made; a manifest walking it first hands it none"
            )
        cycle = ctx.cycle
        members = [ind.model_copy(deep=True) for ind in capo_state(ctx.state).population]
        assert cycle.tracking.current_sp is not None
        base = cycle.tracking.current_sp.pipeline_params
        raced = replace(
            population,
            proposals=[*(CandidateProposal(opt_sp=m) for m in members), *population.proposals],
            individuals=[*members, *population.individuals],
            pipeline_params=[*([base] * len(members)), *population.pipeline_params],
        )
        # Minted where the race's order is final, once per individual: a member that raced before
        # was minted in the round that first raced it.
        raced_before = {cs.candidate_id for rr in cycle.rounds for cs in rr.candidate_scores}
        if (ledger := cycle.session.state.ledger) is not None:
            for idx, ind in enumerate(raced.individuals):
                if ind.lineage.id in raced_before:
                    continue
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
        return raced


class PopulationKnobs(StrictModel):
    size: Annotated[int, Knob(Scope.POLICY, Estimand.SELECTION)] = Field(
        ge=2,
        description="μ, the individuals CAPO carries into the next round: the race's survivors, "
        "best first, cut to this many.",
    )


class PopulationSelector:
    """Keeps the race's survivors, best first by mean objective on the cells all of them measured,
    as the next round's population. The round advances when that best is not the incumbent."""

    name: ClassVar[str] = "population"
    kind: ClassVar[NodeKind] = NodeKind.SELECTOR
    knobs: ClassVar[type[StrictModel]] = PopulationKnobs
    couplings: ClassVar[tuple[nodes.MemberCoupling, ...]] = ()
    stamps_theta: ClassVar[bool] = False

    def select(self, ctx: RoundContext, measured: Measured, population: Population) -> Selection:
        cycle = ctx.cycle
        state = capo_state(ctx.state)
        size = cast("PopulationKnobs", cycle.optimizer.knobs(self.name)).size
        electable = set(measured.electable)
        survivors = [
            cs.candidate_id
            for cs in measured.scores
            if cs.candidate_id in electable and cs.outcome is not ArmOutcome.ELIMINATED
        ]
        objective = _objective(ctx)
        kept = _rank_survivors(survivors, measured.rows, size=size, objective=objective)
        record_decision(
            cycle.pending_decisions,
            CapoCheckpointKind.POPULATION_KEPT,
            {
                "survivors": survivors,
                "round_num": ctx.round_num,
                "size": size,
                "length_penalty": objective.length_penalty,
                "length_norm": objective.length_norm,
            },
            kept,
            node=self.name,
            round=ctx.round_num,
        )
        by_id = {ind.lineage.id: ind for ind in measured.scored}
        labels = {cs.candidate_id: cs.label for cs in measured.scores}
        best = kept[0] if kept else ""
        selected_id = best if best and best != cycle.opt_sp.lineage.id else ""
        # A round with no survivor replaces nothing: the population it started from stands.
        carried = [by_id[cid] for cid in kept] if kept else state.population
        verdict = (
            f"kept {len(kept)} of {len(survivors)} surviving arms (μ {size}) by mean objective "
            f"(length penalty {objective.length_penalty}) on their shared cells; "
            f"best {labels[best]}"
            if kept
            else f"no arm survived the race; the population of {len(carried)} stands"
        )
        return nodes.Selection(
            selected_id=selected_id,
            scores=list(measured.scores),
            verdict_reason=verdict,
            optimizer_state=state.snapshot(
                population.optimizer_state.prompt_hashes,
                population=carried,
                rounds_without_advance=0 if selected_id else state.rounds_without_advance + 1,
            ),
        )


def _replay_paired_t_cut(
    ctx: ReplayContext, inputs_ref: dict[str, Any], data: dict[str, Any]
) -> bool:
    rows = ctx.round_data.all_candidate_results
    objective = _recorded_objective(inputs_ref)
    arm = _cell_objectives(
        rows[inputs_ref["candidate_id"]][: int(inputs_ref["queries_scored"])], objective
    )
    rivals = {pid: _cell_objectives(rows[pid], objective) for pid in inputs_ref["raced_against"]}
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


CAPO_CHECKPOINT_GATING: dict[ResumeCheckpointKind, GatingMode] = {
    CapoCheckpointKind.PAIRED_T_CUT: GatingMode.REPLAYED,
    CapoCheckpointKind.POPULATION_KEPT: GatingMode.REPLAYED,
}
CAPO_REPLAYERS: dict[str, Replayer] = {
    CapoCheckpointKind.PAIRED_T_CUT: _replay_paired_t_cut,
    CapoCheckpointKind.POPULATION_KEPT: _replay_population_kept,
}


class CapoRuntime:
    """CAPO beyond its nodes: its population state, its prompt identity and its replayed race."""

    name: ClassVar[str] = CAPO_MANIFEST
    own_axes: ClassVar[dict[str, set[str]]] = {}

    def start(
        self, session: Session, config: CampaignConfig, origin_results: list[dict[str, Any]]
    ) -> CapoState:
        return CapoState()

    def prompt_hashes(self, selected: SelectedOptimizer) -> dict[str, str]:
        return dict(selected.node_digests)

    def complete(self) -> None:
        return None

    def source_digest(self, *covered: ModuleType) -> str:
        # AST-normalized, so a comment or a reflow does not move it.
        shaping = [m for m in (paper_templates, operators) if m not in covered]
        tree = "".join(ast.dump(ast.parse(inspect.getsource(m))) for m in shaping)
        return hashlib.sha256(tree.encode("utf-8")).hexdigest()[:16]

    @property
    def checkpoint_gating(self) -> Mapping[ResumeCheckpointKind, GatingMode]:
        return CAPO_CHECKPOINT_GATING

    @property
    def replayers(self) -> Mapping[str, Replayer]:
        return CAPO_REPLAYERS

    def round_packages(self, cycle: Cycle, rounds: list[RoundResult]) -> dict[int, dict[str, str]]:
        # CAPO's nodes read no package off the rounds before them, so a repair drifts none.
        return {}

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


MEMBERS = (
    FewShot(),
    Blocks(),
    PairedT(),
    CapoCrossover(),
    CapoMutate(),
    PopulationRejoin(),
    PopulationSelector(),
)
RUNTIME = CapoRuntime()
