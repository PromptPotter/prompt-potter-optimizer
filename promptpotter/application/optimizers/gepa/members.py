"""GEPA's node members and runtime; every ``Alg.`` reference is arXiv 2507.19457's."""

from __future__ import annotations

import random
from collections.abc import Mapping, Sequence
from typing import TYPE_CHECKING, Annotated, Any, ClassVar

from pydantic import Field

from promptpotter.application.bench.node_context import (
    Calls,
    Children,
    Frontier,
    Lineage,
    Replay,
    Target,
)
from promptpotter.application.optimizers import nodes, paper_templates
from promptpotter.application.optimizers.descriptors import cell_objectives
from promptpotter.application.optimizers.gepa import prompts
from promptpotter.application.optimizers.gepa.state import (
    GEPA_MANIFEST,
    MinibatchBar,
    MinibatchBars,
    ParetoScores,
    ParetoSet,
    ReflectionModules,
)
from promptpotter.application.optimizers.nodes import RewriteKnobs
from promptpotter.application.optimizers.paper_templates import child, shown
from promptpotter.config.paths import optimizers_root
from promptpotter.domain.campaign_config import Estimand, Knob, Scope
from promptpotter.domain.paired_reading import ArmKey
from promptpotter.domain.pipeline_schema import NodeKind
from promptpotter.domain.results import Cut, Elimination, Measured, OptimizerFact
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
    from promptpotter.application.optimizer_manifest import SelectedOptimizer
    from promptpotter.application.optimizers.nodes import (
        CatchUpFn,
        CycleRead,
        MeasuredRound,
        Panel,
        PromptArity,
        Proposals,
        Selection,
    )
    from promptpotter.application.scoring.query_loop import Walk
    from promptpotter.domain.opt_search_point import VariationMode
    from promptpotter.domain.optimizer_state import StatePart
    from promptpotter.domain.results import DisplayMetric, RoundResult
    from promptpotter.domain.sample import Sample
    from promptpotter.domain.scoring import CellSheet, GradedCell
    from promptpotter.domain.search_point import JobSearchPoint


class GepaCheckpointKind(CheckpointKind):
    MINIBATCH_GATE = "minibatch_gate"
    CANDIDATE_REFUSED = "candidate_refused"


class MinibatchKnobs(StrictModel):
    size: Annotated[int, Knob(Scope.POLICY, Estimand.SEARCH, Estimand.STOPPING)] = Field(
        ge=1,
        description="b: the feedback-set cells a round runs its parent on for the reflection to "
        "read, and tests the child on before the Pareto set scores it.",
    )
    pareto_size: Annotated[int, Knob(Scope.POLICY, Estimand.SELECTION)] = Field(
        ge=1,
        description="The Pareto set: the search-split cells, first in bank order, every accepted "
        "child is scored on and the candidates are ranked by; minibatches are drawn from the "
        "rest, the feedback set.",
    )


class Minibatch(nodes.Member):
    name: ClassVar[str] = "minibatch"
    kind: ClassVar[NodeKind] = NodeKind.SAMPLER
    knobs: ClassVar[type[StrictModel]] = MinibatchKnobs
    reads: ClassVar[frozenset[type[StatePart]]] = frozenset({ParetoSet})
    writes: ClassVar[frozenset[type[StatePart]]] = frozenset({ParetoSet})
    sees: ClassVar[frozenset[type[CycleRead]]] = frozenset({Replay})
    size_knob: ClassVar[str | None] = None

    def round_cells(self, knobs: MinibatchKnobs, search: int) -> int:
        if search - knobs.pareto_size < knobs.size:
            return 0
        return knobs.size + knobs.pareto_size

    def draw(self, ctx: NodeContext, pool: list[Sample]) -> Panel:
        knobs = ctx.knobs(MinibatchKnobs)
        banked = ctx.slice(ParetoSet).keys
        by_key = {s.key: s for s in pool}
        if missing := [key for key in banked if key not in by_key]:
            raise ValueError(f"minibatch: {len(missing)} Pareto-set cells left the search split")
        pareto = [by_key[key] for key in banked] if banked else pool[: knobs.pareto_size]
        held = {s.key for s in pareto}
        feedback = [s for s in pool if s.key not in held]
        if not pareto or len(feedback) < knobs.size:
            raise ValueError(
                f"minibatch: the search split's {len(pool)} rows split into a Pareto set of "
                f"{len(pareto)} and a feedback set of {len(feedback)}, which holds no minibatch "
                f"of {knobs.size}"
            )
        ctx.put(ParetoSet(keys=[s.key for s in pareto]))
        batch = ctx.read(Replay).rng().sample(feedback, knobs.size)
        cells = [*batch, *pareto]
        return nodes.Panel(cells=cells, order=list(cells), block_size=knobs.size)


class GepaReflect(nodes.Member):
    """Alg. 1 lines 7-12; its module is the parent's next prompt node, round-robin per lineage (the reference's default selector)."""

    name: ClassVar[str] = "gepa_reflect"
    kind: ClassVar[NodeKind] = NodeKind.LLM
    prompt_arity: ClassVar[PromptArity] = "each"
    opens: ClassVar[bool] = True
    variation_mode: ClassVar[VariationMode] = "llm"
    knobs: ClassVar[type[StrictModel]] = RewriteKnobs
    reads: ClassVar[frozenset[type[StatePart]]] = frozenset({ParetoScores, ReflectionModules})
    writes: ClassVar[frozenset[type[StatePart]]] = frozenset({ReflectionModules, MinibatchBars})
    sees: ClassVar[frozenset[type[CycleRead]]] = frozenset(
        {Calls, Children, Frontier, Lineage, Target}
    )

    async def propose(self, ctx: NodeContext, panel: Panel, proposals: Proposals) -> Proposals:
        candidates = ctx.slice(ParetoScores)
        parent_id = candidates.parent_id
        # A lineage no longer scored keeps no pointer.
        held = {
            cid: at
            for cid, at in ctx.slice(ReflectionModules).modules.items()
            if cid in candidates.scores
        }
        lineage = ctx.read(Lineage)
        parent = (
            lineage.parent
            if parent_id is None
            else next(ind for ind in lineage.population if ind.id == parent_id)
        )
        modules = ctx.read(Target).schema.prompt_nodes()
        at = held.get(parent.id, 0) % len(modules)
        node = modules[at]
        rows = await ctx.read(Frontier).measure_parent(parent.id, panel.order[: panel.block_size])
        prompt = prompts.reflection_prompt(ctx, instruction=shown(ctx, parent, node), rows=rows)
        proposal = child(
            ctx,
            node,
            [parent],
            await ctx.read(Calls).ask(prompt, 0),
            f"reflect on {parent.id[:6]}'s {node.name}",
            parse=prompts.fenced,
        )
        advanced = (at + 1) % len(modules)
        ctx.put(
            ReflectionModules(
                modules={**held, parent.id: advanced, proposal.individual.id: advanced}
            )
        )
        bar = MinibatchBar(parent_id=parent.id, objectives=cell_objectives(rows))
        ctx.emit(MinibatchBars(bars={proposal.individual.id: bar}))
        return nodes.Proposals(proposals=[proposal])


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


class _GateRace(nodes.NoCatchUps):
    gate = "not_improved"

    def __init__(
        self,
        ctx: NodeContext,
        *,
        bars: MinibatchBars,
        block_size: int,
        n_cells: int,
    ) -> None:
        self._ctx = ctx
        self._replay = ctx.read(Replay)
        self.node = ctx.node
        self._bars = bars.bars
        self._block_size = block_size
        self._n_cells = n_cells
        self._tested: set[ArmKey] = set()

    @property
    def n_priors(self) -> int:
        return 1

    @property
    def blocks(self) -> _GateRace:
        return self

    @property
    def block_size(self) -> int:
        return self._block_size

    def rule(self, ahead: Sequence[tuple[ArmKey, Walk | None]]) -> None:
        return None

    def open_turn(self, key: ArmKey) -> None:
        return None

    def close(self, rows: Mapping[ArmKey, Sequence[GradedCell]]) -> dict[ArmKey, StopSignal]:
        stops: dict[ArmKey, StopSignal] = {}
        for cid, arm_rows in rows.items():
            if cid in self._tested:
                continue
            self._tested.add(cid)
            if (held := self._bars.get(cid)) is None:
                raise KeyError(
                    f"{self.node} tests arm {cid[:8]}, for which no proposer this round banked a "
                    "bar: the gate reads each arm against its own parent"
                )
            parent_id, bar = held.parent_id, held.objectives
            graded = cell_objectives(arm_rows)
            accepted, sigma, sigma_prime = minibatch_improves(bar, graded)
            self._replay.decide(
                GepaCheckpointKind.MINIBATCH_GATE,
                {
                    "arm": cid,
                    "parent_id": parent_id,
                    "round_num": self._ctx.round_num,
                    "parent_scores": dict(bar),
                },
                accepted,
                data={"sigma": sigma, "sigma_prime": sigma_prime},
            )
            if not accepted:
                stops[cid] = StopSignal(
                    self.node,
                    "eliminated",
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
        key: ArmKey,
        results: Sequence[GradedCell],
        labels: Mapping[ArmKey, str],
    ) -> Elimination | None:
        if signal is None or signal.check_name != self.node:
            return None
        cr = signal.check_result
        mean, bar = cr["sigma_prime"], cr["sigma"]
        reading = f"{mean:.3f} ≤ parent's {bar:.3f}" if mean is not None else "no shared cell"
        return Elimination(
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

    def admit(self, key: ArmKey, results: Sequence[GradedCell], sp: JobSearchPoint) -> None:
        return None


class MinibatchGate(nodes.Member):
    name: ClassVar[str] = "minibatch_gate"
    kind: ClassVar[NodeKind] = NodeKind.ELIMINATOR
    reads: ClassVar[frozenset[type[StatePart]]] = frozenset({MinibatchBars})
    sees: ClassVar[frozenset[type[CycleRead]]] = frozenset({Replay})
    abort_lenses: ClassVar[Mapping[str, frozenset[str]]] = {}
    stop_disqualifies: ClassVar[bool] = True

    def race(
        self,
        ctx: NodeContext,
        panel: Panel,
        proposals: Proposals,
        catch_up: CatchUpFn,
    ) -> _GateRace:
        return _GateRace(
            ctx,
            bars=ctx.recall(MinibatchBars, ctx.round_num),
            block_size=panel.block_size,
            n_cells=len(panel.order),
        )


def _aggregate(scores: Mapping[str, float]) -> float:
    return sum(scores.values()) / len(scores)


def pareto_frequencies(
    candidates: Mapping[str, Mapping[str, float]], pareto_set: Sequence[str]
) -> dict[str, int]:
    """Alg. 2 lines 2-13; dominated as the REFERENCE IMPLEMENTATION reads it: every cell it leads, another survivor leads too."""
    cell_tops: list[set[str]] = []
    for key in pareto_set:
        scored = {cid: scores[key] for cid, scores in candidates.items() if key in scores}
        if scored:
            top = max(scored.values())
            cell_tops.append({cid for cid, score in scored.items() if score == top})
    aggregate = {cid: _aggregate(scores) for cid, scores in candidates.items()}
    leading = [cid for cid in candidates if any(cid in tops for tops in cell_tops)]
    ranked = sorted(leading, key=lambda cid: aggregate[cid])
    dominated: set[str] = set()
    removed = True
    while removed:
        removed = False
        for y in ranked:
            if y in dominated:
                continue
            others = set(ranked) - dominated - {y}
            if all(tops & others for tops in cell_tops if y in tops):
                dominated.add(y)
                removed = True
                break
    return {cid: sum(cid in tops for tops in cell_tops) for cid in leading if cid not in dominated}


def draw_parent(
    candidates: Mapping[str, Mapping[str, float]], pareto_set: Sequence[str], rng: random.Random
) -> str:
    frequency = pareto_frequencies(candidates, pareto_set)
    ids = list(frequency)
    return rng.choices(ids, weights=[frequency[cid] for cid in ids])[0]


class Pareto(nodes.Member):
    name: ClassVar[str] = "pareto"
    kind: ClassVar[NodeKind] = NodeKind.SELECTOR
    reads: ClassVar[frozenset[type[StatePart]]] = frozenset({ParetoSet, ParetoScores})
    writes: ClassVar[frozenset[type[StatePart]]] = frozenset({ParetoScores})
    sees: ClassVar[frozenset[type[CycleRead]]] = frozenset({Lineage, Replay})
    elects_on: ClassVar[DisplayMetric] = "composite"
    elects_partial: ClassVar[bool] = False

    def parent_cells(
        self,
        ctx: NodeContext,
        panel: Panel,
        rows: Mapping[ArmKey, CellSheet],
    ) -> list[Sample]:
        return panel.cells[panel.block_size :]

    def select(self, ctx: NodeContext, measured: MeasuredRound, proposals: Proposals) -> Selection:
        pareto_set = ctx.slice(ParetoSet).keys
        candidates = dict(ctx.slice(ParetoScores).scores)
        lineage, replay = ctx.read(Lineage), ctx.read(Replay)
        kept = [ind for ind in lineage.population if ind.id in candidates]
        offered = [
            (measured.arms[key].individual, measured.rows[key]) for key in measured.electable
        ]
        if not candidates:
            offered.insert(0, (measured.parent.individual, measured.parent.rows))
        admitted: list[str] = []
        for ind, sheet in offered:
            graded = cell_objectives(sheet)
            # An aggregate over fewer cells is no aggregate, for the seat as for every child.
            if ungraded := [key for key in pareto_set if key not in graded]:
                replay.decide(
                    GepaCheckpointKind.CANDIDATE_REFUSED,
                    {"arm": ind.id, "round_num": ctx.round_num},
                    False,
                    data={"ungraded": ungraded},
                )
                continue
            if ind.id != measured.parent.individual.id:
                admitted.append(ind.id)
            if ind.id not in candidates:
                kept.append(ind)
            candidates[ind.id] = {key: graded[key] for key in pareto_set}
        if not candidates:
            ctx.put(ParetoScores())
            return nodes.Selection(
                selected=None,
                leading=None,
                verdict_reason="no candidate carries a verdict on every Pareto-set cell; none "
                "is admitted",
                population=kept,
            )
        aggregates = {cid: _aggregate(scores) for cid, scores in candidates.items()}
        top = max(aggregates.values())
        leaders = [cid for cid, aggregate in aggregates.items() if aggregate == top]
        incumbent = lineage.parent.id
        # A tie never advances: the incumbent holds against an equal aggregate.
        selected_id = "" if incumbent in leaders else leaders[0]
        if selected_id and selected_id not in admitted:
            raise ValueError("pareto: the best aggregate is neither the incumbent nor this round's")
        parent_id = draw_parent(candidates, pareto_set, replay.rng())
        verdict = (
            f"{len(admitted)} of {len(measured.arms)} arms passed the minibatch, joining "
            f"{len(candidates)} candidates; best aggregate {top:.3f} on {len(pareto_set)} "
            f"Pareto-set cells; next parent {parent_id[:8]}"
        )
        ctx.put(ParetoScores(scores=candidates, parent_id=parent_id))
        leading = selected_id or max(admitted, key=aggregates.__getitem__, default="")
        return nodes.Selection(
            selected=ArmKey(selected_id) if selected_id else None,
            leading=ArmKey(leading) if leading else None,
            verdict_reason=verdict,
            population=kept,
        )


def _replay_minibatch_gate(
    ctx: ReplayContext, inputs_ref: dict[str, Any], data: dict[str, Any]
) -> bool:
    rows = ctx.round_data.rows(ArmKey(inputs_ref["arm"]))
    accepted, _, _ = minibatch_improves(inputs_ref["parent_scores"], cell_objectives(rows))
    return accepted


class GepaRuntime(nodes.OptimizerRuntime):
    name: ClassVar[str] = GEPA_MANIFEST
    manifest_dir: ClassVar[Path] = optimizers_root() / GEPA_MANIFEST
    prompt_sources: ClassVar[tuple[ModuleType, ...]] = (paper_templates, prompts)
    replayers: ClassVar[Mapping[str, Replayer]] = {
        GepaCheckpointKind.MINIBATCH_GATE: _replay_minibatch_gate,
    }

    def round_packages(self, cycle: Cycle, rounds: list[RoundResult]) -> dict[int, dict[str, str]]:
        # The parent a round reflects on is drawn off every earlier round's Pareto-set scores.
        return nodes.rows_read_packages(rounds, [GepaReflect.name])

    def arms(self, selected: SelectedOptimizer) -> int:
        return 1

    def round_facts(
        self, selected: SelectedOptimizer, round_result: RoundResult
    ) -> list[OptimizerFact]:
        banked = round_result.closed_state
        if not (scores := banked.slice(ParetoScores).scores):
            return []
        arms = round_result.panel.values()
        accepted = sum(1 for arm in arms if isinstance(arm, Measured))
        rejected = sum(1 for arm in arms if isinstance(arm, Cut) and arm.kind == "eliminated")
        leading = len(pareto_frequencies(scores, banked.slice(ParetoSet).keys))
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
                text=f"{leading} of {len(scores)} scored",
                value=leading,
                kind="stat",
            ),
        ]


MEMBERS = (Minibatch(), GepaReflect(), MinibatchGate(), Pareto())
RUNTIME = GepaRuntime()
