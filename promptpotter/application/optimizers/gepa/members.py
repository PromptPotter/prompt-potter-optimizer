"""GEPA's node members and runtime; every ``Alg.`` reference is arXiv 2507.19457's."""

from __future__ import annotations

import random
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import TYPE_CHECKING, Annotated, Any, ClassVar

from pydantic import Field

from promptpotter.application.campaign_config import Estimand, Knob, Scope
from promptpotter.application.optimizers import nodes, paper_templates
from promptpotter.application.optimizers.descriptors import cell_objectives
from promptpotter.application.optimizers.gepa import prompts
from promptpotter.application.optimizers.gepa.state import (
    GEPA_MANIFEST,
    GepaRoundState,
)
from promptpotter.application.optimizers.paper_templates import RewriteKnobs, child, shown
from promptpotter.config.paths import optimizers_root
from promptpotter.domain.pipeline_schema import NodeKind
from promptpotter.domain.results import ArmOutcome, OptimizerFact
from promptpotter.domain.run_records import CheckpointKind
from promptpotter.domain.strict_model import StrictModel
from promptpotter.domain.validators import StopSignal

if TYPE_CHECKING:
    from pathlib import Path
    from types import ModuleType

    from promptpotter.application.bench.cycle import Cycle
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
    "GepaCheckpointKind",
    "MinibatchKnobs",
    "draw_parent",
    "minibatch_improves",
    "pareto_frequencies",
]


class GepaCheckpointKind(CheckpointKind):
    MINIBATCH_GATE = "minibatch_gate"
    POOL_REFUSED = "pool_refused"


class MinibatchKnobs(StrictModel):
    size: Annotated[int, Knob(Scope.POLICY, Estimand.SEARCH, Estimand.STOPPING)] = Field(
        ge=1,
        description="b: the feedback-set cells a round runs its parent on for the reflection to "
        "read, and tests the child on before the Pareto set scores it.",
    )
    pareto_size: Annotated[int, Knob(Scope.POLICY, Estimand.SELECTION)] = Field(
        ge=1,
        description="The Pareto set: the search-pool cells, first in bank order, every accepted "
        "child is scored on and the pool is ranked by; minibatches are drawn from the rest, the "
        "feedback set.",
    )


class Minibatch:
    name: ClassVar[str] = "minibatch"
    kind: ClassVar[NodeKind] = NodeKind.SAMPLER
    knobs: ClassVar[type[StrictModel]] = MinibatchKnobs
    size_knob: ClassVar[str | None] = None

    def draws(self, selected: SelectedOptimizer, pool: int) -> int:
        knobs = selected.knobs_of(self.name, MinibatchKnobs)
        if pool - knobs.pareto_size < knobs.size:
            return 0
        return knobs.size + knobs.pareto_size

    def draw(self, ctx: NodeContext[MinibatchKnobs], pool: list[Sample]) -> Panel:
        knobs = ctx.knobs
        working = nodes.state_as(ctx, GepaRoundState)
        banked = working.payload.pareto_set
        by_key = {s.key: s for s in pool}
        if missing := [key for key in banked if key not in by_key]:
            raise ValueError(f"minibatch: {len(missing)} Pareto-set cells left the search pool")
        pareto = [by_key[key] for key in banked] if banked else pool[: knobs.pareto_size]
        held = {s.key for s in pareto}
        feedback = [s for s in pool if s.key not in held]
        if not pareto or len(feedback) < knobs.size:
            raise ValueError(
                f"minibatch: the search pool's {len(pool)} rows split into a Pareto set of "
                f"{len(pareto)} and a feedback set of {len(feedback)}, which holds no minibatch "
                f"of {knobs.size}"
            )
        working.payload = working.payload.model_copy(update={"pareto_set": [s.key for s in pareto]})
        batch = ctx.rng().sample(feedback, knobs.size)
        cells = [*batch, *pareto]
        return nodes.Panel(cells=cells, order=list(cells), block_size=knobs.size)


class GepaReflectKnobs(RewriteKnobs):
    """Beside the reflection's call config, which loci it rewrites."""


@dataclass(frozen=True)
class Reflected(nodes.Proposals):
    bars: Mapping[str, tuple[str, dict[str, float]]]


class GepaReflect:
    """Alg. 1 lines 7-12; the round-robin module of a one-prompt individual is always its instruction."""

    name: ClassVar[str] = "gepa_reflect"
    kind: ClassVar[NodeKind] = NodeKind.LLM
    opens: ClassVar[bool] = True
    knobs: ClassVar[type[StrictModel]] = GepaReflectKnobs

    async def propose(
        self, ctx: NodeContext[GepaReflectKnobs], panel: Panel, proposals: Proposals
    ) -> Reflected:
        state = nodes.state_as(ctx, GepaRoundState).payload
        parent = (
            ctx.parent
            if state.parent_id is None
            else next(ind for ind in ctx.population if ind.id == state.parent_id)
        )
        rows = await ctx.measure_parent(parent.id, panel.order[: panel.block_size])
        prompt = prompts.reflection_prompt(ctx, instruction=shown(ctx, parent), rows=rows)
        proposal = child(
            ctx,
            [parent],
            await ctx.ask(prompt, 0),
            f"reflect on {parent.id[:6]}",
            parse=prompts.fenced,
        )
        return Reflected(
            proposals=[proposal],
            bars={proposal.opt_sp.id: (parent.id, cell_objectives(rows))},
        )


def minibatch_improves(
    bar: Mapping[str, float], child: Mapping[str, float]
) -> tuple[bool, float | None, float | None]:
    """Alg. 1 lines 13-14, over the cells BOTH graded: accepted only on a strict gain, and no shared cell shows none."""
    shared = [key for key in bar if key in child]
    if not shared:
        return False, None, None
    sigma = sum(bar[key] for key in shared) / len(shared)
    sigma_prime = sum(child[key] for key in shared) / len(shared)
    return sigma_prime > sigma, sigma, sigma_prime


class MinibatchGateKnobs(StrictModel):
    """The test takes no parameter: a child must beat its parent on the minibatch."""


class _GateRace(nodes.NoCatchUps):
    gate = "not_improved"

    def __init__(
        self,
        ctx: NodeContext[MinibatchGateKnobs],
        *,
        bars: Mapping[str, tuple[str, dict[str, float]]],
        block_size: int,
        n_cells: int,
    ) -> None:
        self._ctx = ctx
        self.node = ctx.node
        self._bars = bars
        self._block_size = block_size
        self._n_cells = n_cells
        self._arms: dict[int, str] = {}
        self._tested: set[str] = set()

    @property
    def n_priors(self) -> int:
        return 1

    @property
    def blocks(self) -> _GateRace:
        return self

    @property
    def block_size(self) -> int:
        return self._block_size

    def rule(self, ahead: Sequence[tuple[str, Walk | None]]) -> None:
        return None

    def open_turn(self, candidate_id: str, idx: int, n: int) -> None:
        self._arms[idx] = candidate_id

    def close(self, rows: Mapping[int, Sequence[GradedCell]]) -> dict[int, StopSignal]:
        stops: dict[int, StopSignal] = {}
        for i, arm_rows in rows.items():
            cid = self._arms[i]
            if cid in self._tested:
                continue
            self._tested.add(cid)
            parent_id, bar = self._bars[cid]
            graded = cell_objectives(arm_rows)
            accepted, sigma, sigma_prime = minibatch_improves(bar, graded)
            self._ctx.decide(
                GepaCheckpointKind.MINIBATCH_GATE,
                {
                    "candidate_id": cid,
                    "parent_id": parent_id,
                    "round_num": self._ctx.round_num,
                    "parent_scores": dict(bar),
                },
                accepted,
                data={"sigma": sigma, "sigma_prime": sigma_prime},
            )
            if not accepted:
                stops[i] = StopSignal(
                    self.node,
                    ArmOutcome.ELIMINATED,
                    {
                        "gate": self.gate,
                        "queries_scored": len(arm_rows),
                        "total_samples": self._n_cells,
                        "parent_id": parent_id,
                        "sigma": sigma,
                        "sigma_prime": sigma_prime,
                    },
                )
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
        mean, bar = cr["sigma_prime"], cr["sigma"]
        reading = f"{mean:.3f} ≤ parent's {bar:.3f}" if mean is not None else "no shared cell"
        return nodes.EliminationReading(
            reason=(
                f"no gain on the minibatch q{cr['queries_scored']}/{cr['total_samples']}  {reading}"
            ),
            context={
                "gate": cr["gate"],
                "queries_scored": cr["queries_scored"],
                "total_queries": cr["total_samples"],
                "n_priors": 1,
                "parent": cr["parent_id"],
                "sigma": cr["sigma"],
                "sigma_prime": cr["sigma_prime"],
            },
        )

    def admit(self, candidate_id: str, results: Sequence[GradedCell], sp: JobSearchPoint) -> None:
        return None


class MinibatchGate:
    name: ClassVar[str] = "minibatch_gate"
    kind: ClassVar[NodeKind] = NodeKind.ELIMINATOR
    knobs: ClassVar[type[StrictModel]] = MinibatchGateKnobs
    abort_lenses: ClassVar[Mapping[str, frozenset[str]]] = {}
    stop_disqualifies: ClassVar[bool] = True

    def race(
        self,
        ctx: NodeContext[MinibatchGateKnobs],
        panel: Panel,
        proposals: Proposals,
        catch_up: CatchUpFn,
    ) -> _GateRace:
        return _GateRace(
            ctx,
            bars=nodes.proposals_as(proposals, Reflected).bars,
            block_size=panel.block_size,
            n_cells=len(panel.order),
        )


def _aggregate(scores: Mapping[str, float]) -> float:
    return sum(scores.values()) / len(scores)


def pareto_frequencies(
    pool: Mapping[str, Mapping[str, float]], pareto_set: Sequence[str]
) -> dict[str, int]:
    """Alg. 2 lines 2-13; dominated as the REFERENCE IMPLEMENTATION reads it: every cell it leads, another survivor leads too."""
    fronts: list[set[str]] = []
    for key in pareto_set:
        scored = {cid: scores[key] for cid, scores in pool.items() if key in scores}
        if scored:
            top = max(scored.values())
            fronts.append({cid for cid, score in scored.items() if score == top})
    aggregate = {cid: _aggregate(scores) for cid, scores in pool.items()}
    leading = [cid for cid in pool if any(cid in f for f in fronts)]
    ranked = sorted(leading, key=lambda cid: aggregate[cid])
    dominated: set[str] = set()
    removed = True
    while removed:
        removed = False
        for y in ranked:
            if y in dominated:
                continue
            others = set(ranked) - dominated - {y}
            if all(front & others for front in fronts if y in front):
                dominated.add(y)
                removed = True
                break
    return {cid: sum(cid in f for f in fronts) for cid in leading if cid not in dominated}


def draw_parent(
    pool: Mapping[str, Mapping[str, float]], pareto_set: Sequence[str], rng: random.Random
) -> str:
    frequency = pareto_frequencies(pool, pareto_set)
    ids = list(frequency)
    return rng.choices(ids, weights=[frequency[cid] for cid in ids])[0]


class ParetoKnobs(StrictModel):
    """Pareto-based selection takes no parameter."""


class Pareto:
    name: ClassVar[str] = "pareto"
    kind: ClassVar[NodeKind] = NodeKind.SELECTOR
    knobs: ClassVar[type[StrictModel]] = ParetoKnobs
    elects_on: ClassVar[DisplayMetric] = "composite"
    elects_partial: ClassVar[bool] = False

    def parent_cells(
        self,
        ctx: NodeContext[ParetoKnobs],
        panel: Panel,
        rows: Mapping[str, CellSheet],
    ) -> list[Sample]:
        return panel.cells[panel.block_size :]

    def select(
        self, ctx: NodeContext[ParetoKnobs], measured: Measured, proposals: Proposals
    ) -> Selection:
        banked = nodes.state_as(ctx, GepaRoundState)
        state = banked.payload
        pareto_set = state.pareto_set
        pool = dict(state.scores)
        kept = [ind for ind in ctx.population if ind.id in pool]
        offered = [(ind, measured.rows[ind.id]) for ind in measured.electable]
        if not pool:
            offered.insert(0, (measured.parent.opt_sp, measured.parent_rows))
        admitted: list[str] = []
        for ind, sheet in offered:
            graded = cell_objectives(sheet)
            # An aggregate over fewer cells is no aggregate, for the seat as for every child.
            if ungraded := [key for key in pareto_set if key not in graded]:
                ctx.decide(
                    GepaCheckpointKind.POOL_REFUSED,
                    {"candidate_id": ind.id, "round_num": ctx.round_num},
                    False,
                    data={"ungraded": ungraded},
                )
                continue
            if ind.id != measured.parent.opt_sp.id:
                admitted.append(ind.id)
            if ind.id not in pool:
                kept.append(ind)
            pool[ind.id] = {key: graded[key] for key in pareto_set}
        ctx.keep(kept)
        if not pool:
            banked.payload = GepaRoundState(pareto_set=pareto_set, scores={}, parent_id=None)
            return nodes.Selection(
                selected_id="",
                leading_id="",
                scores=list(measured.scores),
                verdict_reason="no candidate carries a verdict on every Pareto-set cell; the "
                "pool stays unseated",
            )
        aggregates = {cid: _aggregate(scores) for cid, scores in pool.items()}
        top = max(aggregates.values())
        leaders = [cid for cid, aggregate in aggregates.items() if aggregate == top]
        incumbent = ctx.parent.id
        # A tie never advances: the incumbent holds against an equal aggregate.
        selected_id = "" if incumbent in leaders else leaders[0]
        if selected_id and selected_id not in admitted:
            raise ValueError("pareto: the best aggregate is neither the incumbent nor this round's")
        parent_id = draw_parent(pool, pareto_set, ctx.rng())
        verdict = (
            f"{len(admitted)} of {len(measured.scores)} arms passed the minibatch into a pool of "
            f"{len(pool)}; best aggregate {top:.3f} on {len(pareto_set)} Pareto-set cells; next "
            f"parent {parent_id[:8]}"
        )
        banked.payload = GepaRoundState(pareto_set=pareto_set, scores=pool, parent_id=parent_id)
        return nodes.Selection(
            selected_id=selected_id,
            leading_id=selected_id or max(admitted, key=aggregates.__getitem__, default=""),
            scores=list(measured.scores),
            verdict_reason=verdict,
        )


def _replay_minibatch_gate(
    ctx: ReplayContext, inputs_ref: dict[str, Any], data: dict[str, Any]
) -> bool:
    rows = ctx.round_data.all_candidate_results[inputs_ref["candidate_id"]]
    accepted, _, _ = minibatch_improves(inputs_ref["parent_scores"], cell_objectives(rows))
    return accepted


class GepaRuntime(nodes.OptimizerRuntime):
    name: ClassVar[str] = GEPA_MANIFEST
    manifest_dir: ClassVar[Path] = optimizers_root() / GEPA_MANIFEST
    prompt_sources: ClassVar[tuple[ModuleType, ...]] = (paper_templates, prompts)
    replayers: ClassVar[Mapping[str, Replayer]] = {
        GepaCheckpointKind.MINIBATCH_GATE: _replay_minibatch_gate,
    }

    def start(
        self, session: Session, config: CampaignConfig, origin_results: CellSheet
    ) -> BankedState[GepaRoundState]:
        return nodes.BankedState(GepaRoundState(pareto_set=[], scores={}, parent_id=None))

    def round_packages(self, cycle: Cycle, rounds: list[RoundResult]) -> dict[int, dict[str, str]]:
        # The parent a round reflects on is drawn off every earlier round's Pareto-set scores.
        return nodes.rows_read_packages(rounds, [GepaReflect.name])

    def arms(self, selected: SelectedOptimizer) -> int:
        return 1

    def round_facts(
        self, selected: SelectedOptimizer, round_result: RoundResult
    ) -> list[OptimizerFact]:
        state = round_result.optimizer_state.payload_as(GepaRoundState)
        if not state.scores:
            return []
        arms = round_result.candidate_scores
        accepted = sum(1 for cs in arms if cs.outcome is ArmOutcome.MEASURED)
        rejected = sum(1 for cs in arms if cs.outcome is ArmOutcome.ELIMINATED)
        front = len(pareto_frequencies(state.scores, state.pareto_set))
        return [
            OptimizerFact(
                key="minibatch",
                label="Minibatch",
                text=f"{accepted} accepted · {rejected} rejected",
                value=accepted,
                kind="stat",
            ),
            OptimizerFact(
                key="pareto_front",
                label="Pareto front",
                text=f"{front} of {len(state.scores)} in the pool",
                value=front,
                kind="stat",
            ),
        ]


MEMBERS = (Minibatch(), GepaReflect(), MinibatchGate(), Pareto())
RUNTIME = GepaRuntime()
