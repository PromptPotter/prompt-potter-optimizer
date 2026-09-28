"""GEPA's node implementations (arXiv 2507.19457), each registered under the node name its
manifest uses, and its runtime, registered under the manifest's."""

from __future__ import annotations

import ast
import hashlib
import inspect
import random
from collections.abc import Mapping, Sequence
from typing import TYPE_CHECKING, Annotated, Any, ClassVar, cast

from pydantic import Field

from promptpotter.application.bench.resume_and_fork.decisions import (
    GatingMode,
    record_decision,
)
from promptpotter.application.campaign_config import Estimand, Knob, Scope
from promptpotter.application.optimizers import fence, nodes, paper_templates
from promptpotter.application.optimizers.descriptors import cell_objectives
from promptpotter.application.optimizers.gepa import operators
from promptpotter.application.optimizers.gepa.state import (
    GepaState,
    gepa_round_state,
    gepa_state,
)
from promptpotter.application.optimizers.paper_templates import ask, unmarked, walk_rng
from promptpotter.application.runner.measurement import measure_as_parent
from promptpotter.application.scoring import row_diagnostics
from promptpotter.domain.opt_search_point import OptSearchPoint, node_source
from promptpotter.domain.optimizer_state import GEPA_MANIFEST, GepaCandidate
from promptpotter.domain.pipeline_schema import NodeKind
from promptpotter.domain.results import ArmOutcome, CandidateProposal, candidate_label
from promptpotter.domain.run_records import CandidateMintedRecord, GepaCheckpointKind
from promptpotter.domain.strict_model import StrictModel
from promptpotter.domain.validators import StopSignal

if TYPE_CHECKING:
    from types import ModuleType

    from promptpotter.application.bench.cycle import Cycle
    from promptpotter.application.bench.resume_and_fork.replayers import (
        ReplayContext,
        Replayer,
    )
    from promptpotter.application.campaign_config import CampaignConfig
    from promptpotter.application.initialization.session import Session
    from promptpotter.application.optimizer_manifest import SelectedOptimizer
    from promptpotter.application.optimizers.nodes import (
        CatchUpFn,
        Measured,
        Panel,
        Population,
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
    "MinibatchKnobs",
    "draw_parent",
    "minibatch_improves",
    "pareto_frequencies",
]


class MinibatchKnobs(StrictModel):
    size: Annotated[int, Knob(Scope.POLICY, Estimand.SEARCH, Estimand.STOPPING)] = Field(
        ge=1,
        description="b: the feedback-set cells a round runs its parent on for the reflection to "
        "read, and tests the child on before the Pareto set scores it.",
    )
    pareto_share: Annotated[float, Knob(Scope.POLICY, Estimand.SELECTION)] = Field(
        gt=0.0,
        lt=1.0,
        description="The share of the search pool held out as the Pareto set, which every "
        "accepted child is scored on and the pool is ranked by; minibatches are drawn from the "
        "rest, the feedback set.",
    )


class Minibatch:
    """GEPA's panel: a fresh minibatch of the feedback set as the first block, where the gate
    reads, then the Pareto set — the pool's first ``pareto_share`` in bank order, fixed per run."""

    name: ClassVar[str] = "minibatch"
    kind: ClassVar[NodeKind] = NodeKind.SAMPLER
    knobs: ClassVar[type[StrictModel]] = MinibatchKnobs
    couplings: ClassVar[tuple[nodes.MemberCoupling, ...]] = ()

    def draw(self, ctx: RoundContext, pool: list[Sample]) -> Panel:
        knobs = cast("MinibatchKnobs", ctx.cycle.optimizer.knobs(self.name))
        banked = gepa_state(ctx.state).pareto_set
        by_key = {s.key: s for s in pool}
        if missing := [key for key in banked if key not in by_key]:
            raise ValueError(f"minibatch: {len(missing)} Pareto-set cells left the search pool")
        pareto = (
            [by_key[key] for key in banked]
            if banked
            else pool[: round(knobs.pareto_share * len(pool))]
        )
        held = {s.key for s in pareto}
        feedback = [s for s in pool if s.key not in held]
        if not pareto or len(feedback) < knobs.size:
            raise ValueError(
                f"minibatch: the search pool's {len(pool)} rows split into a Pareto set of "
                f"{len(pareto)} and a feedback set of {len(feedback)}, which holds no minibatch "
                f"of {knobs.size}"
            )
        batch = walk_rng(ctx.cycle, ctx.round_num, self.name).sample(feedback, knobs.size)
        cells = [*batch, *pareto]
        return nodes.Panel(cells=cells, order=list(cells), block_size=knobs.size)


class GepaReflectKnobs(StrictModel):
    """The reflection's call config is all it has; it takes no knob of its own."""


class GepaReflect:
    """Alg. 1 lines 7-12, on a one-prompt individual whose round-robin module is always its
    instruction. The only proposer, so it mints the child onto the ledger."""

    name: ClassVar[str] = "gepa_reflect"
    kind: ClassVar[NodeKind] = NodeKind.LLM
    knobs: ClassVar[type[StrictModel]] = GepaReflectKnobs
    couplings: ClassVar[tuple[nodes.MemberCoupling, ...]] = ()

    async def propose(
        self, ctx: RoundContext, panel: Panel, population: Population | None
    ) -> Population:
        if population is not None:
            raise ValueError(
                "gepa_reflect opens GEPA's walk; a manifest walking it after another proposer "
                "hands it offspring it would discard"
            )
        cycle = ctx.cycle
        state = gepa_state(ctx.state)
        # Round 1's pool is the individual the run starts from alone (Alg. 1 line 2).
        parent = (
            cycle.opt_sp
            if state.parent_id is None
            else next(
                c.individual for c in state.pool if c.individual.lineage.id == state.parent_id
            )
        )
        assert cycle.tracking.current_sp is not None
        base = cycle.tracking.current_sp.pipeline_params
        session = cycle.session
        # Alg. 1 line 10: the parent's reading of the minibatch.
        rows = await measure_as_parent(
            ctx,
            parent.to_job_search_point(
                base_pipeline_params=base,
                schema=session.pipeline_schema,
                framing=cycle.framing,
                demo=session.scoring.require_partition().demo,
            ),
            parent.lineage.id,
            panel.order[: panel.block_size],
        )
        prompt = operators.reflection_prompt(
            cycle, self.name, instruction=parent.instruction, rows=rows
        )
        raw = await ask(ctx, self.name, 0, prompt)
        text = operators.fenced(raw)
        child = OptSearchPoint.derive(
            [parent],
            source=node_source(GEPA_MANIFEST, self.name),
            changes_description=f"reflect on {parent.lineage.id[:6]}",
            instruction=parent.instruction if text is None else text,
        )
        state.bars = {child.lineage.id: (parent.lineage.id, cell_objectives(rows))}
        if (ledger := cycle.session.state.ledger) is not None:
            ledger.append(
                CandidateMintedRecord(
                    round=ctx.round_num,
                    idx=0,
                    candidate_id=child.lineage.id,
                    parent_ids=list(child.lineage.parent_ids),
                    label=candidate_label(ctx.round_num, 0),
                    changes_description=child.lineage.changes_description,
                    source=child.lineage.source,
                )
            )
        return nodes.Population(
            proposals=[
                CandidateProposal(
                    opt_sp=child,
                    validation_failures=[] if text is not None else [unmarked(self.name, raw)],
                )
            ],
            individuals=[child],
            pipeline_params=[base],
            optimizer_state=state.snapshot(
                cycle.optimizer.prompt_hashes(),
                pareto_set=[s.key for s in panel.cells[panel.block_size :]],
                pool=state.pool,
                parent_id=state.parent_id,
                rounds_without_advance=state.rounds_without_advance,
            ),
        )


def minibatch_improves(
    bar: Mapping[str, float], child: Mapping[str, float]
) -> tuple[bool, float | None, float | None]:
    """Alg. 1 lines 13-14: ``(accepted, σ, σ′)``, the parent's and the child's mean objective over
    the minibatch cells both graded. Accepted only on a strict gain; no shared cell shows none."""
    shared = [key for key in bar if key in child]
    if not shared:
        return False, None, None
    sigma = sum(bar[key] for key in shared) / len(shared)
    sigma_prime = sum(child[key] for key in shared) / len(shared)
    return sigma_prime > sigma, sigma, sigma_prime


class MinibatchGateKnobs(StrictModel):
    """The test takes no parameter: a child must beat its parent on the minibatch."""


class _GateRace(nodes.NoCatchUps):
    """Tests each arm once, at the minibatch's close, against its parent's reading there; a child
    that improved walks on through the Pareto set, one that did not is cut."""

    gate = "not_improved"

    def __init__(
        self,
        *,
        node: str,
        bars: Mapping[str, tuple[str, dict[str, float]]],
        block_size: int,
        n_cells: int,
        round_num: int,
        decisions: list[ResumeCheckpointRecord],
    ) -> None:
        self.node = node
        self._bars = bars
        self._block_size = block_size
        self._n_cells = n_cells
        self._round_num = round_num
        self._decisions = decisions
        self._arms: dict[int, str] = {}
        self._tested: set[str] = set()

    @property
    def n_priors(self) -> int:
        # The one reading an arm is tested against: its parent's, on the minibatch.
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

    def close(self, rows: Mapping[int, list[QueryMeasurement]]) -> dict[int, StopSignal]:
        stops: dict[int, StopSignal] = {}
        for i, arm_rows in rows.items():
            cid = self._arms[i]
            if cid in self._tested:
                continue
            self._tested.add(cid)
            parent_id, bar = self._bars[cid]
            accepted, sigma, sigma_prime = minibatch_improves(bar, cell_objectives(arm_rows))
            record_decision(
                self._decisions,
                GepaCheckpointKind.MINIBATCH_GATE,
                {
                    "candidate_id": cid,
                    "parent_id": parent_id,
                    "round_num": self._round_num,
                    "parent_scores": dict(bar),
                },
                accepted,
                node=self.node,
                data={"sigma": sigma, "sigma_prime": sigma_prime},
                round=self._round_num,
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
        results: list[QueryMeasurement],
        labels: dict[str, str],
    ) -> Mapping[str, Any] | None:
        if signal is None or signal.check_name != self.node:
            return None
        cr = signal.check_result
        return {
            "gate": cr["gate"],
            "queries_scored": cr["queries_scored"],
            "total_queries": cr["total_samples"],
            "n_priors": 1,
            "parent": cr["parent_id"],
            "sigma": cr["sigma"],
            "sigma_prime": cr["sigma_prime"],
        }

    def admit(self, candidate_id: str, results: list[QueryMeasurement], sp: JobSearchPoint) -> None:
        return None


class MinibatchGate:
    """GEPA's acceptance test (Alg. 1 line 14), run where the minibatch block closes."""

    name: ClassVar[str] = "minibatch_gate"
    kind: ClassVar[NodeKind] = NodeKind.ELIMINATOR
    knobs: ClassVar[type[StrictModel]] = MinibatchGateKnobs
    couplings: ClassVar[tuple[nodes.MemberCoupling, ...]] = ()
    abort_lenses: ClassVar[Mapping[str, frozenset[str]]] = {}

    def race(self, ctx: RoundContext, panel: Panel, catch_up: CatchUpFn) -> _GateRace:
        return _GateRace(
            node=self.name,
            bars=gepa_state(ctx.state).bars,
            block_size=panel.block_size,
            n_cells=len(panel.order),
            round_num=ctx.round_num,
            decisions=ctx.cycle.pending_decisions,
        )


def _aggregate(candidate: GepaCandidate) -> float:
    return sum(candidate.scores.values()) / len(candidate.scores)


def pareto_frequencies(pool: Sequence[GepaCandidate], pareto_set: Sequence[str]) -> dict[str, int]:
    """Alg. 2 lines 2-13: each survivor's count of Pareto-set cells it leads. Dominated, as the
    reference implementation reads it: every cell it leads, another survivor leads too."""
    fronts: list[set[str]] = []
    for key in pareto_set:
        scored = {c.individual.lineage.id: c.scores[key] for c in pool if key in c.scores}
        if scored:
            top = max(scored.values())
            fronts.append({cid for cid, score in scored.items() if score == top})
    aggregate = {c.individual.lineage.id: _aggregate(c) for c in pool}
    leading = [
        cid for cid in (c.individual.lineage.id for c in pool) if any(cid in f for f in fronts)
    ]
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
    pool: Sequence[GepaCandidate], pareto_set: Sequence[str], rng: random.Random
) -> str:
    """Alg. 2 line 14: a candidate drawn with probability proportional to the cells it leads."""
    frequency = pareto_frequencies(pool, pareto_set)
    ids = list(frequency)
    return rng.choices(ids, weights=[frequency[cid] for cid in ids])[0]


class ParetoKnobs(StrictModel):
    """Pareto-based selection takes no parameter."""


class Pareto:
    """GEPA's pool (Alg. 1 lines 15-18, 21; Alg. 2): the gate's survivors join with their
    Pareto-set scores, the best aggregate is selected, the next parent is drawn off the front."""

    name: ClassVar[str] = "pareto"
    kind: ClassVar[NodeKind] = NodeKind.SELECTOR
    knobs: ClassVar[type[StrictModel]] = ParetoKnobs
    couplings: ClassVar[tuple[nodes.MemberCoupling, ...]] = ()
    stamps_theta: ClassVar[bool] = False

    def select(self, ctx: RoundContext, measured: Measured, population: Population) -> Selection:
        cycle = ctx.cycle
        state = gepa_state(ctx.state)
        pareto_set = gepa_round_state(population.optimizer_state).pareto_set
        pool = [c.model_copy(deep=True) for c in state.pool]
        if not pool:
            # Alg. 1 lines 3-5: the incumbent, re-scored on this round's panel, is scored on the
            # Pareto set there.
            graded = cell_objectives(measured.parent_rows)
            pool.append(
                GepaCandidate(
                    individual=measured.parent.opt_sp.model_copy(deep=True),
                    scores={key: graded[key] for key in pareto_set if key in graded},
                )
            )
        by_id = {ind.lineage.id: ind for ind in population.individuals}
        admitted: list[str] = []
        for cs in measured.scores:
            graded = cell_objectives(measured.rows[cs.candidate_id])
            if cs.outcome is ArmOutcome.ELIMINATED or any(k not in graded for k in pareto_set):
                continue
            admitted.append(cs.candidate_id)
            pool.append(
                GepaCandidate(
                    individual=by_id[cs.candidate_id].model_copy(deep=True),
                    scores={key: graded[key] for key in pareto_set},
                )
            )
        top = max(_aggregate(c) for c in pool)
        leaders = [c.individual.lineage.id for c in pool if _aggregate(c) == top]
        incumbent = cycle.opt_sp.lineage.id
        # A tie never advances: the incumbent holds against an equal aggregate.
        selected_id = "" if incumbent in leaders else leaders[0]
        if selected_id and selected_id not in admitted:
            raise ValueError("pareto: the best aggregate is neither the incumbent nor this round's")
        parent_id = draw_parent(pool, pareto_set, walk_rng(cycle, ctx.round_num, self.name))
        verdict = (
            f"{len(admitted)} of {len(measured.scores)} arms passed the minibatch into a pool of "
            f"{len(pool)}; best aggregate {top:.3f} on {len(pareto_set)} Pareto-set cells; next "
            f"parent {parent_id[:8]}"
        )
        return nodes.Selection(
            selected_id=selected_id,
            scores=list(measured.scores),
            verdict_reason=verdict,
            optimizer_state=state.snapshot(
                population.optimizer_state.prompt_hashes,
                pareto_set=pareto_set,
                pool=pool,
                parent_id=parent_id,
                rounds_without_advance=0 if selected_id else state.rounds_without_advance + 1,
            ),
        )


def _replay_minibatch_gate(
    ctx: ReplayContext, inputs_ref: dict[str, Any], data: dict[str, Any]
) -> bool:
    rows = ctx.round_data.all_candidate_results[inputs_ref["candidate_id"]]
    accepted, _, _ = minibatch_improves(inputs_ref["parent_scores"], cell_objectives(rows))
    return accepted


GEPA_CHECKPOINT_GATING: dict[ResumeCheckpointKind, GatingMode] = {
    GepaCheckpointKind.MINIBATCH_GATE: GatingMode.REPLAYED,
}
GEPA_REPLAYERS: dict[str, Replayer] = {
    GepaCheckpointKind.MINIBATCH_GATE: _replay_minibatch_gate,
}


class GepaRuntime:
    """GEPA beyond its nodes: its pool state, its prompt identity and its replayed gate."""

    name: ClassVar[str] = GEPA_MANIFEST
    own_axes: ClassVar[dict[str, set[str]]] = {}

    def start(
        self, session: Session, config: CampaignConfig, origin_results: list[dict[str, Any]]
    ) -> GepaState:
        return GepaState()

    def prompt_hashes(self, selected: SelectedOptimizer) -> dict[str, str]:
        return selected.running_digests()

    def complete(self) -> None:
        return None

    def source_digest(self, *covered: ModuleType) -> str:
        # AST-normalized, so a comment or a reflow does not move it.
        shaping = [
            m for m in (fence, paper_templates, row_diagnostics, operators) if m not in covered
        ]
        tree = "".join(ast.dump(ast.parse(inspect.getsource(m))) for m in shaping)
        return hashlib.sha256(tree.encode("utf-8")).hexdigest()[:16]

    def override_levers(self, node: str, declared: Mapping[str, Any]) -> dict[str, Any]:
        return {}

    @property
    def checkpoint_gating(self) -> Mapping[ResumeCheckpointKind, GatingMode]:
        return GEPA_CHECKPOINT_GATING

    @property
    def replayers(self) -> Mapping[str, Replayer]:
        return GEPA_REPLAYERS

    def round_packages(self, cycle: Cycle, rounds: list[RoundResult]) -> dict[int, dict[str, str]]:
        # The parent a round reflects on is drawn off every earlier round's Pareto-set scores.
        return nodes.rows_read_packages(rounds, [GepaReflect.name])

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


MEMBERS = (Minibatch(), GepaReflect(), MinibatchGate(), Pareto())
RUNTIME = GepaRuntime()
