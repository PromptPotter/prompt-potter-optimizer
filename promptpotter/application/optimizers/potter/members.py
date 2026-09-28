"""Potter's node implementations, each registered under the node name its manifest uses, and its
runtime, registered under the manifest's."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import replace
from typing import TYPE_CHECKING, Any, ClassVar

from promptpotter.application.intelligence.adaptive_queue_mechanism import build_round_order
from promptpotter.application.intelligence.exploration import (
    build_observations,
    select_round_subset,
)
from promptpotter.application.intelligence.indexes.axis import NOISE_THRESHOLD
from promptpotter.application.optimizer_manifest import bound_inner_optimizer
from promptpotter.application.optimizers import nodes
from promptpotter.application.optimizers.potter import couplings
from promptpotter.application.optimizers.potter.dispatch.facade import injection_source_digest
from promptpotter.application.optimizers.potter.dispatch.injections.registry import injection_table
from promptpotter.application.optimizers.potter.dispatch.layout import NODE_LAYOUTS, layout_levers
from promptpotter.application.optimizers.potter.dispatch.schemas import (
    L2_NODE_AXES,
    OPTIMIZER_RESPONSE_MODELS,
)
from promptpotter.application.optimizers.potter.election import elect_on_theta
from promptpotter.application.optimizers.potter.escalation.firing import L2, L3, escalate_l2
from promptpotter.application.optimizers.potter.escalation.rules import DEFAULT_ESCALATION_RULES
from promptpotter.application.optimizers.potter.escalation.state import NextAction
from promptpotter.application.optimizers.potter.generation_only import run_generation_only_round
from promptpotter.application.optimizers.potter.knobs import (
    AdaptiveQueueKnobs,
    EscalationKnobs,
    L1GenerateKnobs,
    PoBBKnobs,
    ThetaElectionKnobs,
    potter_knobs,
)
from promptpotter.application.optimizers.potter.l1.candidate_source import (
    generate_or_load_candidates,
    replayed_candidates,
    variants_this_round,
)
from promptpotter.application.optimizers.potter.l1.critique import critique_owed, run_l1_critique
from promptpotter.application.optimizers.potter.l1.population import parse_population
from promptpotter.application.optimizers.potter.l1.stats import review_reading, round_facts
from promptpotter.application.optimizers.potter.pobb.checks import ABORT_LENS_SUPPRESS
from promptpotter.application.optimizers.potter.race import PoBBRace
from promptpotter.application.optimizers.potter.records import POTTER_MANIFEST, PotterRoundState
from promptpotter.application.optimizers.potter.resume import (
    POTTER_CHECKPOINT_GATING,
    POTTER_REPLAYERS,
    rederive_critiques,
    round_packages,
)
from promptpotter.application.optimizers.potter.state import PotterState, potter_state
from promptpotter.application.optimizers.potter.validators.l1_invariants import L1YieldStats
from promptpotter.application.optimizers.potter.validators.l1_strict import (
    DROPPED_MANDATORY_PLACEHOLDER,
)
from promptpotter.application.scoring.candidate_report import fatal_validation_failures
from promptpotter.config.paths import optimizers_root
from promptpotter.config.prompt_blocks import block_library
from promptpotter.domain.dashboard_rows import OptimizerLimit
from promptpotter.domain.phases import StopLoop
from promptpotter.domain.pipeline_schema import SCHEMA_RENAME_PARAM, NodeKind, stable_hash
from promptpotter.domain.results import CandidateProposal
from promptpotter.domain.results_health import compute_node_failure_rates, evidence_starved_node
from promptpotter.domain.scoring import is_graded
from promptpotter.domain.strict_model import StrictModel
from promptpotter.infrastructure.tracing.bridge import observed_node
from promptpotter.shared.errors import graceful

if TYPE_CHECKING:
    from pathlib import Path
    from types import ModuleType

    from pydantic import BaseModel

    from promptpotter.application.bench.cycle import Cycle
    from promptpotter.application.bench.resume_and_fork.decisions import GatingMode
    from promptpotter.application.bench.resume_and_fork.replayers import Replayer
    from promptpotter.application.campaign_config import CampaignConfig
    from promptpotter.application.initialization.session import Session
    from promptpotter.application.optimizer_manifest import SelectedOptimizer
    from promptpotter.application.optimizers.nodes import (
        Boundary,
        CatchUpFn,
        Measured,
        Panel,
        Population,
        ReviewReading,
        RoundContext,
        Selection,
    )
    from promptpotter.domain.cycle_paths import CycleHop
    from promptpotter.domain.results import OptimizerFact, RoundResult
    from promptpotter.domain.run_records import CheckpointKind
    from promptpotter.domain.sample import Sample
    from promptpotter.infrastructure.store.campaign_store.store import CampaignStore

__all__ = ["MEMBERS", "RUNTIME"]


class L1CritiqueKnobs(StrictModel):
    """The critique's call config is all it has; it takes no knob of its own."""


class AdaptiveQueue:
    """The round's panel, drawn off the search pool, and ONE order every arm walks it in —
    parent-miss samples front-loaded, a parent-hit regression probe every 4th slot, cells the
    parent never answered ordered by discrimination — so the eliminator sees discriminating
    evidence immediately, and shared prefixes keep paired stats comparable."""

    name: ClassVar[str] = "adaptive_queue"
    kind: ClassVar[NodeKind] = NodeKind.SAMPLER
    knobs: ClassVar[type[StrictModel]] = AdaptiveQueueKnobs
    couplings: ClassVar[tuple[nodes.MemberCoupling, ...]] = couplings.ADAPTIVE_QUEUE
    size_knob: ClassVar[str | None] = "sp_budget_round"

    def draws(self, selected: SelectedOptimizer, pool: int) -> int:
        # Unclamped: a budget above the pool is the finding preflight reports.
        return potter_knobs(selected).adaptive_queue.sp_budget_round

    def draw(self, ctx: RoundContext, pool: list[Sample]) -> Panel:
        cycle = ctx.cycle
        config = cycle.config
        knobs = potter_knobs(cycle.optimizer).adaptive_queue
        if not knobs.per_round_resubset or cycle.ruler is None:
            # The campaign-start prefix: on a COLD ruler a re-picked subset is difficulty-blind, and
            # freezing concentrates measurements so the ruler warms and locks fastest.
            cells = select_round_subset(pool, [], knobs.sp_budget_round)
        else:
            # Archive obs are dataset-scoped + abort-residue-free → cross-cycle evidence.
            own = build_observations(cycle.rounds)
            cells = select_round_subset(
                pool,
                [*cycle.archive_observations, *own],
                knobs.sp_budget_round,
                ruler=cycle.ruler,
                # Enough already-anchored cells for the next extension to equate against: the
                # acquisition prefers unmeasured cells, whose δ SE is widest.
                anchor_floor=config.optimization.elimination_n_min,
                # The archive fits the θ scale; only THIS cycle's arms are in the race the panel
                # has to separate.
                leader_ids={o.candidate_id for o in own},
            )
        # CORRECTNESS, not the composite: `build_round_order` thresholds these with `is_hit`, and
        # under a `per_cell` composite below 1.0 every cell would read as a miss.
        parent_results = cycle.tracking.current_results
        parent_grades: dict[int, float] = {}
        if parent_results and cycle.tracking.current_sp is not None:
            parent_grades = {
                int(sid): float(r["fitness"])
                for r in parent_results
                if (sid := r.get("sample_id")) is not None and is_graded(r)
            }
        order = build_round_order(parent_grades, cycle.ruler, [int(s.id) for s in cells])
        by_id = {int(s.id): s for s in cells}
        # Every cell closes a block: PoBB decides after each one.
        return nodes.Panel(cells=cells, order=[by_id[sid] for sid in order], block_size=1)


def _fold_strict_rejections(
    stats: L1YieldStats, proposals: list[CandidateProposal]
) -> L1YieldStats:
    """Re-derive ``l1_yield`` as the share of proposals that can still MEASURE.

    ``detect_invariants`` counts only the three round-local collapses, and the strict validators
    run later, in ``parse_population``. Both land on the same wound channel, so ONE predicate spans
    them — subtracting the collapse counters as well would charge those candidates twice. The
    counters stay as they are; they name three specific shapes, and a strict rejection is none."""
    if not proposals:
        return stats
    live = sum(1 for cp in proposals if not fatal_validation_failures(cp.validation_failures))
    return replace(stats, l1_yield=live / len(proposals))


class L1Generate:
    name: ClassVar[str] = "l1_generate"
    kind: ClassVar[NodeKind] = NodeKind.LLM
    knobs: ClassVar[type[StrictModel]] = L1GenerateKnobs
    couplings: ClassVar[tuple[nodes.MemberCoupling, ...]] = ()

    async def propose(
        self, ctx: RoundContext, panel: Panel, population: Population | None
    ) -> Population:
        if population is not None:
            raise ValueError(
                "l1_generate proposes from the round's parent; a manifest walking it after "
                "another proposer hands it a population it would discard"
            )
        cycle = ctx.cycle
        state = potter_state(ctx.state)
        schema = cycle.session.pipeline_schema
        assert schema is not None and cycle.tracking.current_sp is not None
        proposals, yield_stats = await generate_or_load_candidates(
            ctx.round_num, cycle, state, obs=cycle.session.state.obs
        )
        knobs = potter_knobs(cycle.optimizer).l1_generate
        individuals, params = parse_population(
            proposals,
            cycle.opt_sp,
            cycle.tracking.current_sp.pipeline_params,
            schema,
            runtime_failures=state.memory.wounds.runtime_failures,
            demo_ids=frozenset(s.id for s in cycle.session.scoring.require_partition().demo),
            shot_k_max=knobs.k_max,
            inner_optimizer=bound_inner_optimizer(),
            prompt_block_catalogue=knobs.prompt_block_catalogue,
        )
        yield_stats = _fold_strict_rejections(yield_stats, proposals)
        return nodes.Population(
            proposals=proposals,
            individuals=individuals,
            pipeline_params=params,
            optimizer_state=state.snapshot(
                l1_yield=yield_stats.l1_yield,
                l1_parse_failure=yield_stats.l1_parse_failure,
                # Stamped with the round rather than at save time: a re-save (a repair, a rescore)
                # must not restamp a round with the optimizer running NOW.
                prompt_hashes=cycle.optimizer.prompt_hashes(),
                axis_memory_peaked=sorted(cycle.axes.peaked_axes()) if cycle.axes else [],
            ),
        )


class PoBB:
    name: ClassVar[str] = "pobb"
    kind: ClassVar[NodeKind] = NodeKind.ELIMINATOR
    knobs: ClassVar[type[StrictModel]] = PoBBKnobs
    couplings: ClassVar[tuple[nodes.MemberCoupling, ...]] = couplings.POBB
    abort_lenses: ClassVar[Mapping[str, frozenset[str]]] = ABORT_LENS_SUPPRESS

    def race(self, ctx: RoundContext, panel: Panel, catch_up: CatchUpFn) -> PoBBRace:
        return PoBBRace(ctx, panel, catch_up, node=self.name)


class ThetaElection:
    name: ClassVar[str] = "theta_election"
    kind: ClassVar[NodeKind] = NodeKind.SELECTOR
    knobs: ClassVar[type[StrictModel]] = ThetaElectionKnobs
    couplings: ClassVar[tuple[nodes.MemberCoupling, ...]] = ()
    stamps_theta: ClassVar[bool] = True
    reads_parent: ClassVar[bool] = True

    def select(self, ctx: RoundContext, measured: Measured, population: Population) -> Selection:
        return elect_on_theta(ctx, measured, population, node=self.name)


class L1Critique:
    """Feedback for the NEXT round's generator — nothing else reads it. A malformed response is
    survived: the next round re-sends it before generating (`critique.py::ensure_prior_critique`)."""

    name: ClassVar[str] = "l1_critique"
    kind: ClassVar[NodeKind] = NodeKind.LLM
    knobs: ClassVar[type[StrictModel]] = L1CritiqueKnobs
    couplings: ClassVar[tuple[nodes.MemberCoupling, ...]] = ()

    async def adapt(self, ctx: RoundContext, round_result: RoundResult) -> None:
        session = ctx.cycle.session
        failed = (
            "Origin critique failed; round 1 proceeds without seeded feedback"
            if ctx.round_num == 0
            else "L1 critique failed; the next round re-sends it before generating"
        )
        with graceful(failed):
            async with observed_node(
                f"l1_critique_r{ctx.round_num}",
                "llm",
                obs=session.state.obs,
                campaign_id=session.state.tracing_campaign_id,
                round_num=ctx.round_num,
            ):
                critique = await run_l1_critique(
                    ctx.cycle,
                    potter_state(ctx.state),
                    round_result,
                    round_num=ctx.round_num,
                    ledger=session.state.ledger,
                )
            round_result.optimizer_state.payload_as(PotterRoundState).critique = critique


class Escalation:
    """The stall ladder: at each boundary it banks the round's verdict, and on a stall fires L2 —
    which may climb to L3 — before the next round walks `default` under what they wrote."""

    name: ClassVar[str] = "escalation"
    kind: ClassVar[NodeKind] = NodeKind.CONTROLLER
    knobs: ClassVar[type[StrictModel]] = EscalationKnobs
    couplings: ClassVar[tuple[nodes.MemberCoupling, ...]] = couplings.ESCALATION

    def stops_after(self, ctx: RoundContext, round_result: RoundResult) -> bool:
        # Asked through the FSM so the lookahead can never disagree with the banking `observe`
        # is about to do.
        return potter_state(ctx.state).escalation.would_exhaust_lives(
            round_result.improved,
            potter_knobs(ctx.cycle.optimizer).escalation.lives,
            compared=round_result.electable_count > 0,
        )

    def observe(self, ctx: RoundContext, round_result: RoundResult) -> Boundary:
        cycle = ctx.cycle
        knobs = potter_knobs(cycle.optimizer).escalation
        axes_with_positive_yield = (
            None
            if cycle.axes is None
            else sum(1 for r in cycle.axes.axis_rankings() if r.effect_size > NOISE_THRESHOLD)
        )
        # A dropped mandatory backend placeholder is structural, not a stall — heal L2 now
        # (patience 0) instead of burning l1_patience rounds while L1 re-drops it.
        l1_mandatory_breach = any(
            vf.reason == DROPPED_MANDATORY_PLACEHOLDER
            for sc in round_result.candidate_scores
            for vf in sc.validation_failures
        )
        # The same structural l1_generate fault, which the identical prompt reproduces; its
        # `candidate_scores` are empty, so the round carries it on `l1_parse_failure`.
        l1_zero_candidates = (
            round_result.optimizer_state.payload_as(PotterRoundState).l1_parse_failure is not None
        )
        # Derived from the SAME helper the degradation grade reads, so routing and verdict can't
        # diverge. Health is stamped only at the close, so the rates are read directly here.
        evidence_starved = (
            evidence_starved_node(compute_node_failure_rates(round_result.results)) is not None
        )
        event = potter_state(ctx.state).escalation.observe_round(
            improved=round_result.improved,
            compared=round_result.electable_count > 0,
            separable=round_result.separable,
            # The round was elected on the composite, so the stop that ends the campaign asks it too.
            current_objective=cycle.tracking.current_composite_fitness,
            l1_patience=knobs.l1_patience,
            escalation_ladder=knobs.escalation_ladder,
            lives=knobs.lives,
            axes_with_positive_yield=axes_with_positive_yield,
            l1_mandatory_breach=l1_mandatory_breach,
            l1_zero_candidates=l1_zero_candidates,
            evidence_starved=evidence_starved,
        )
        return nodes.Boundary(stop=event.stop_reason, act=event.next_action == NextAction.FIRE_L2)

    async def act(self, ctx: RoundContext) -> None:
        session = ctx.cycle.session
        stop = await escalate_l2(
            ctx.cycle,
            potter_state(ctx.state),
            session.pipeline_schema,
            ctx.round_num,
            ctx.callbacks.on_phase,
            obs=session.state.obs,
            tracing_campaign_id=session.state.tracing_campaign_id,
            node=self.name,
        )
        if stop:
            raise StopLoop(stop)

    async def diagnose(self, ctx: RoundContext) -> None:
        # Force L2 (bypass the stall counter) on this round's evidence, then peek the next
        # round's proposals under its overrides.
        await self.act(ctx)
        await run_generation_only_round(
            ctx.cycle, potter_state(ctx.state), ctx.cycle.session, ctx.callbacks, ctx.round_num + 1
        )


class PotterRuntime:
    """Potter beyond its nodes: its working state, prompt identity, review, and resume half."""

    name: ClassVar[str] = POTTER_MANIFEST
    manifest_dir: ClassVar[Path] = optimizers_root() / POTTER_MANIFEST
    own_axes: ClassVar[dict[str, set[str]]] = L2_NODE_AXES
    phases: ClassVar[tuple[nodes.OptimizerPhase, ...]] = (L2.declared, L3.declared)
    response_models: ClassVar[Mapping[str, type[BaseModel]]] = OPTIMIZER_RESPONSE_MODELS

    def start(
        self, session: Session, config: CampaignConfig, origin_results: list[dict[str, Any]]
    ) -> PotterState:
        return PotterState.start(session, config, origin_results)

    def complete(self) -> None:
        injection_table()

    @property
    def priced_surface(self) -> Mapping[str, int]:
        return {
            "injections": len(injection_table()),
            "escalation_rules": len(DEFAULT_ESCALATION_RULES),
        }

    def source_digest(self, *covered: ModuleType) -> str:
        # The block library is prompt MATERIAL stored as data, which no module digest reads.
        return stable_hash([injection_source_digest(*covered), block_library()])[:12]

    def override_param_types(self, node: str) -> dict[str, str]:
        spec = NODE_LAYOUTS.get(node)
        if spec is None:
            return {}
        # L2 owns `l1_generate`'s panels, so there the outer's lever is its output schema's names.
        return {"layout": "object"} if spec.editor == "l4" else {SCHEMA_RENAME_PARAM: "object"}

    def override_levers(self, node: str, declared: Mapping[str, Any]) -> dict[str, Any]:
        return layout_levers(node, declared)

    @property
    def checkpoint_gating(self) -> Mapping[CheckpointKind, GatingMode]:
        return POTTER_CHECKPOINT_GATING

    @property
    def replayers(self) -> Mapping[str, Replayer]:
        return POTTER_REPLAYERS

    def round_packages(self, cycle: Cycle, rounds: list[RoundResult]) -> dict[int, dict[str, str]]:
        return round_packages(cycle, rounds)

    async def rederive(
        self,
        campaign_store: CampaignStore,
        hop: CycleHop,
        session: Session,
        cycle: Cycle,
        drifted: list[RoundResult],
    ) -> None:
        await rederive_critiques(campaign_store, hop, session, cycle, drifted)

    def review(
        self,
        selected: SelectedOptimizer,
        rounds: list[RoundResult],
        audits: list[dict[str, Any] | None],
        *,
        context_object: list[str],
        origin_composite_fitness: float | None,
    ) -> ReviewReading:
        return review_reading(
            selected,
            rounds,
            audits,
            context_object=context_object,
            origin_composite_fitness=origin_composite_fitness,
        )

    def round_facts(
        self, selected: SelectedOptimizer, round_result: RoundResult
    ) -> list[OptimizerFact]:
        return round_facts(round_result)

    def opening(self, ctx: RoundContext) -> nodes.RoundOpening:
        cycle = ctx.cycle
        state = potter_state(ctx.state)
        replayed = replayed_candidates(cycle, ctx.round_num) is not None
        # The distance to the next ESCALATION, not the run's remaining life, which hearts own. At 0
        # the `l1_to_l2` fall-through fires L2 every round, which "stall 1/0" states as a riddle.
        patience = potter_knobs(cycle.optimizer).escalation.l1_patience
        stall = state.escalation.l1_stall_count
        standing = "L2 every round" if patience == 0 else f"stall {stall}/{patience} → L2"
        # A generation that calls L1 is handed the prior critique, re-sent if owed, or never runs.
        prior = (
            cycle.rounds[-1].optimizer_state.payload_as(PotterRoundState) if cycle.rounds else None
        )
        handed = (prior is not None and bool(prior.critique)) or (
            not replayed and critique_owed(cycle)
        )
        if handed:
            critique = f"from R{ctx.round_num - 1}"
        elif ctx.round_num <= 1:
            critique = "none yet (first round)"
        else:
            critique = f"none (R{ctx.round_num - 1} produced none)"
        return nodes.RoundOpening(
            standing=standing,
            note=f"Prior critique: {critique}",
            arms=variants_this_round(cycle, state),
            proposer="L1",
            replayed=replayed,
        )

    def pacing(self, selected: SelectedOptimizer) -> nodes.OptimizerPacing:
        knobs = potter_knobs(selected)
        esc = knobs.escalation
        patiences = [
            ("l1_patience", "L1 patience", esc.l1_patience),
            ("l2_patience", "L2 patience", esc.l2_patience),
            ("l3_patience", "L3 patience", esc.l3_patience),
        ]
        return nodes.OptimizerPacing(
            patience=esc.l1_patience,
            stalls_left=None if esc.lives is None else (esc.lives.start, esc.lives.cap),
            arms_per_round=knobs.l1_generate.n_variants,
            limits=(
                *(
                    OptimizerLimit(
                        node=Escalation.name, knob=knob, label=label, value=value, integer=True
                    )
                    for knob, label, value in patiences
                ),
                OptimizerLimit(
                    node=PoBB.name,
                    knob="epsilon",
                    label="PoBB ε",
                    value=knobs.pobb.epsilon,
                    integer=False,
                ),
            ),
        )


MEMBERS = (AdaptiveQueue(), L1Generate(), PoBB(), ThetaElection(), L1Critique(), Escalation())
RUNTIME = PotterRuntime()
