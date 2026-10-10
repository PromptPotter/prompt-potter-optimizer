from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, ClassVar

from promptpotter.application.bench.children import DROPPED_MANDATORY_PLACEHOLDER
from promptpotter.application.bench.node_context import NodeContext
from promptpotter.application.intelligence.adaptive_queue_mechanism import (
    build_round_order,
    parent_grades,
)
from promptpotter.application.intelligence.exploration import (
    build_observations,
    select_round_subset,
)
from promptpotter.application.intelligence.indexes.axis import NOISE_THRESHOLD
from promptpotter.application.optimizers import nodes
from promptpotter.application.optimizers.potter.couplings import ADAPTIVE_QUEUE, POBB
from promptpotter.application.optimizers.potter.dispatch.bundle import injection_registry
from promptpotter.application.optimizers.potter.dispatch.facade import fingerprinted_modules
from promptpotter.application.optimizers.potter.dispatch.injections.registry import injection_table
from promptpotter.application.optimizers.potter.dispatch.layout import LayoutNode
from promptpotter.application.optimizers.potter.dispatch.schemas import (
    L1CritiqueOutput,
    L1GenerateOutput,
)
from promptpotter.application.optimizers.potter.election import elect_on_theta
from promptpotter.application.optimizers.potter.escalation.firing import (
    FORCED_BY_DIAG,
    L2,
    L3,
    escalate_l2,
)
from promptpotter.application.optimizers.potter.escalation.rules import (
    CLIMB_RULES,
    DEFAULT_ESCALATION_RULES,
    HEAL_RULES,
    NextAction,
)
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
    propose_l1_population,
    variants_this_round,
)
from promptpotter.application.optimizers.potter.l1.critique import critique_owed, run_l1_critique
from promptpotter.application.optimizers.potter.l1.stats import review_reading, round_facts
from promptpotter.application.optimizers.potter.pobb.checks import ABORT_LENS_SUPPRESS
from promptpotter.application.optimizers.potter.race import PoBBRace
from promptpotter.application.optimizers.potter.records import POTTER_MANIFEST, PotterRoundState
from promptpotter.application.optimizers.potter.resume import (
    POTTER_REPLAYERS,
    rederive_critiques,
    round_packages,
)
from promptpotter.application.optimizers.potter.state import PotterState, potter_state
from promptpotter.config.paths import optimizers_root
from promptpotter.config.prompt_blocks import block_library, library_identity
from promptpotter.domain.dashboard_rows import OptimizerLimit
from promptpotter.domain.phases import StopLoop
from promptpotter.domain.pipeline_schema import SCHEMA_RENAME_PARAM, NodeKind
from promptpotter.domain.results_health import evidence_starved_node
from promptpotter.domain.strict_model import StrictModel
from promptpotter.shared.errors import graceful
from promptpotter.shared.hashing import stable_hash

if TYPE_CHECKING:
    from pathlib import Path
    from types import ModuleType

    from pydantic import BaseModel

    from promptpotter.application.bench.cycle import Cycle
    from promptpotter.application.bench.resume_and_fork.replayers import Replayer
    from promptpotter.application.campaign_config import CampaignConfig
    from promptpotter.application.initialization.session import Session
    from promptpotter.application.optimizer_manifest import SelectedOptimizer
    from promptpotter.application.optimizers.nodes import (
        CatchUpFn,
        Measured,
        Panel,
        Proposals,
        ReviewReading,
        RoundContext,
        Selection,
    )
    from promptpotter.domain.cycle_paths import CycleHop
    from promptpotter.domain.results import DisplayMetric, OptimizerFact, RoundResult
    from promptpotter.domain.round_audit import RoundAudit
    from promptpotter.domain.sample import Sample
    from promptpotter.domain.scoring import CellSheet
    from promptpotter.infrastructure.store.campaign_store.store import CampaignStore

__all__ = ["MEMBERS", "RUNTIME"]


class L1CritiqueKnobs(StrictModel):
    """The critique's call config is all it has; it takes no knob of its own."""


class AdaptiveQueue:
    """ONE order every arm walks: shared prefixes keep paired stats comparable."""

    name: ClassVar[str] = "adaptive_queue"
    kind: ClassVar[NodeKind] = NodeKind.SAMPLER
    knobs: ClassVar[type[StrictModel]] = AdaptiveQueueKnobs
    size_knob: ClassVar[str | None] = "sp_budget_round"

    def draws(self, selected: SelectedOptimizer, pool: int) -> int:
        # Unclamped: a budget above the pool is the finding preflight reports.
        return potter_knobs(selected).adaptive_queue.sp_budget_round

    def draw(self, ctx: NodeContext[AdaptiveQueueKnobs], pool: list[Sample]) -> Panel:
        view = ctx.difficulty
        knobs = ctx.knobs
        if not knobs.per_round_resubset or view.ruler is None:
            # On a COLD ruler a re-picked subset is difficulty-blind; freezing warms it fastest.
            cells = select_round_subset(pool, [], knobs.sp_budget_round)
        else:
            own = build_observations(list(ctx.rounds))
            cells = select_round_subset(
                pool,
                [*view.observations, *own],
                knobs.sp_budget_round,
                ruler=view.ruler,
                # Enough anchored cells for the next extension to equate against.
                anchor_floor=ctx.config.optimization.elimination_n_min,
                # Only THIS cycle's arms are in the race the panel has to separate.
                leader_ids={o.candidate_id for o in own},
            )
        order = build_round_order(
            parent_grades(ctx.parent_rows), view.ruler, [int(s.id) for s in cells]
        )
        by_id = {int(s.id): s for s in cells}
        # Every cell closes a block: PoBB decides after each one.
        return nodes.Panel(cells=cells, order=[by_id[sid] for sid in order], block_size=1)


class L1Generate(LayoutNode):
    name: ClassVar[str] = "l1_generate"
    opens: ClassVar[bool] = True
    knobs: ClassVar[type[StrictModel]] = L1GenerateKnobs
    response_model: ClassVar[type[BaseModel] | None] = L1GenerateOutput
    # L2 moves this node's panels, so the outer's only lever here is its output schema's names.
    outer_levers: ClassVar[Mapping[str, str]] = {SCHEMA_RENAME_PARAM: "object"}

    async def propose(
        self, ctx: NodeContext[L1GenerateKnobs], panel: Panel, proposals: Proposals
    ) -> Proposals:
        return await propose_l1_population(ctx, potter_state(ctx.state))


class PoBB:
    name: ClassVar[str] = "pobb"
    kind: ClassVar[NodeKind] = NodeKind.ELIMINATOR
    knobs: ClassVar[type[StrictModel]] = PoBBKnobs
    abort_lenses: ClassVar[Mapping[str, frozenset[str]]] = ABORT_LENS_SUPPRESS
    # A PoBB stop ends the buying of an arm; θ still reads what it measured.
    stop_disqualifies: ClassVar[bool] = False

    def race(
        self,
        ctx: NodeContext[PoBBKnobs],
        panel: Panel,
        proposals: Proposals,
        catch_up: CatchUpFn,
    ) -> PoBBRace:
        return PoBBRace(ctx, panel, catch_up, measured_unit=ctx.measured_unit)


class ThetaElection:
    name: ClassVar[str] = "theta_election"
    kind: ClassVar[NodeKind] = NodeKind.SELECTOR
    knobs: ClassVar[type[StrictModel]] = ThetaElectionKnobs
    elects_on: ClassVar[DisplayMetric] = "ability"
    elects_partial: ClassVar[bool] = True

    def parent_cells(
        self,
        ctx: NodeContext[ThetaElectionKnobs],
        panel: Panel,
        rows: Mapping[str, CellSheet],
    ) -> list[Sample]:
        return panel.cells

    def select(
        self, ctx: NodeContext[ThetaElectionKnobs], measured: Measured, proposals: Proposals
    ) -> Selection:
        return elect_on_theta(ctx, measured, proposals)


class L1Critique(LayoutNode):
    """A malformed response is survived: the next round re-sends it (`ensure_prior_critique`)."""

    name: ClassVar[str] = "l1_critique"
    knobs: ClassVar[type[StrictModel]] = L1CritiqueKnobs
    response_model: ClassVar[type[BaseModel] | None] = L1CritiqueOutput

    async def adapt(self, ctx: NodeContext[L1CritiqueKnobs], round_result: RoundResult) -> None:
        state = potter_state(ctx.state)
        failed = (
            "Origin critique failed; round 1 proceeds without seeded feedback"
            if ctx.round_num == 0
            else "L1 critique failed; the next round re-sends it before generating"
        )
        with graceful(failed):
            state.critiqued(await run_l1_critique(ctx, state))


class Escalation:
    name: ClassVar[str] = "escalation"
    kind: ClassVar[NodeKind] = NodeKind.CONTROLLER
    knobs: ClassVar[type[StrictModel]] = EscalationKnobs

    def observe(self, ctx: NodeContext[EscalationKnobs], round_result: RoundResult) -> bool:
        state = potter_state(ctx.state)
        knobs = ctx.knobs
        axes = state.axes(ctx.sample_index)
        axes_with_positive_yield = (
            None
            if axes is None
            else sum(1 for r in axes.axis_rankings() if r.effect_size > NOISE_THRESHOLD)
        )
        if axes is not None:
            axes.record_flips_from_rounds(list(ctx.rounds), ctx.round_num)
        # Structural, not a stall: heals L2 now instead of burning l1_patience rounds.
        l1_mandatory_breach = any(
            vf.reason == DROPPED_MANDATORY_PLACEHOLDER
            for sc in round_result.candidate_scores
            for vf in sc.validation_failures
        )
        # The same structural fault with empty `candidate_scores`, so it rides `l1_parse_failure`.
        l1_zero_candidates = (
            round_result.optimizer_state.payload_as(PotterRoundState).l1_parse_failure is not None
        )
        # The stamped grade's own rates, so routing and verdict cannot diverge.
        health = round_result.health
        evidence_starved = (
            health is not None and evidence_starved_node(health.node_failure_rates) is not None
        )
        event = state.escalation.observe_round(
            advance=round_result.overlap.advance,
            l1_patience=knobs.l1_patience,
            escalation_ladder=knobs.escalation_ladder,
            axes_with_positive_yield=axes_with_positive_yield,
            l1_mandatory_breach=l1_mandatory_breach,
            l1_zero_candidates=l1_zero_candidates,
            evidence_starved=evidence_starved,
        )
        return event.next_action == NextAction.FIRE_L2

    async def act(self, ctx: NodeContext[EscalationKnobs]) -> None:
        await self._fire(ctx, cause=potter_state(ctx.state).escalation.matched_rule)

    async def _fire(self, ctx: NodeContext[EscalationKnobs], *, cause: str) -> None:
        if stop := await escalate_l2(ctx, potter_state(ctx.state), cause=cause):
            raise StopLoop(stop)

    async def diagnose(self, ctx: NodeContext[EscalationKnobs]) -> None:
        await self._fire(ctx, cause=FORCED_BY_DIAG)
        await ctx.show_next_round()


class PotterRuntime(nodes.OptimizerRuntime):
    name: ClassVar[str] = POTTER_MANIFEST
    manifest_dir: ClassVar[Path] = optimizers_root() / POTTER_MANIFEST
    prompt_sources: ClassVar[tuple[ModuleType, ...]] = fingerprinted_modules()
    # Order holds: `prompt_sources` above fills the registry this counts.
    priced_surface: ClassVar[Mapping[str, int]] = {
        "injections": len(injection_registry()),
        "escalation_rules": len(DEFAULT_ESCALATION_RULES) + len(CLIMB_RULES) + len(HEAL_RULES),
    }
    replayers: ClassVar[Mapping[str, Replayer]] = POTTER_REPLAYERS
    couplings: ClassVar[Mapping[str, tuple[nodes.MemberCoupling, ...]]] = {
        AdaptiveQueue.name: ADAPTIVE_QUEUE,
        PoBB.name: POBB,
    }

    def start(
        self, session: Session, config: CampaignConfig, origin_results: CellSheet
    ) -> PotterState:
        return PotterState.start(session, config, origin_results)

    def arms(self, selected: SelectedOptimizer) -> int:
        return potter_knobs(selected).l1_generate.n_variants

    def round_cells_ceiling(self, selected: SelectedOptimizer, pool: int) -> int:
        # The sampler's draw is unclamped where the budget exceeds the pool; a round's is not.
        knobs = potter_knobs(selected)
        return (self.arms(selected) + 1) * min(knobs.adaptive_queue.sp_budget_round, pool)

    def limits(self, selected: SelectedOptimizer) -> tuple[OptimizerLimit, ...]:
        knobs = potter_knobs(selected)
        esc = knobs.escalation
        patiences = [
            ("l1_patience", "L1 patience", esc.l1_patience),
            ("l2_patience", "L2 patience", esc.l2_patience),
            ("l3_patience", "L3 patience", esc.l3_patience),
        ]
        return (
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
        )

    def opening(self, ctx: RoundContext) -> nodes.RoundOpening:
        cycle = ctx.cycle
        state = potter_state(cycle.working_state)
        knobs = potter_knobs(cycle.optimizer)
        replayed = NodeContext[Any](ctx, L1Generate.name).banked_proposals() is not None
        # At patience 0 L2 fires every round, which "stall 1/0" would state as a riddle.
        patience = knobs.escalation.l1_patience
        stall = state.escalation.ladder.l1_stall_count
        standing = "L2 every round" if patience == 0 else f"stall {stall}/{patience} → L2"
        prior = (
            cycle.rounds[-1].optimizer_state.payload_as(PotterRoundState) if cycle.rounds else None
        )
        handed = (prior is not None and bool(prior.critique)) or (
            not replayed and critique_owed(cycle.rounds)
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
            arms=variants_this_round(knobs.l1_generate.n_variants, state),
            proposer="L1",
            replayed=replayed,
        )

    def complete(self) -> None:
        injection_table()

    def source_digest(self, *covered: ModuleType) -> str:
        # The block library is prompt MATERIAL stored as data, which no module digest reads.
        library = library_identity(block_library())
        return stable_hash([super().source_digest(*covered), library])

    def round_packages(self, cycle: Cycle, rounds: list[RoundResult]) -> dict[int, dict[str, str]]:
        return round_packages(cycle, rounds)

    async def rederive(
        self,
        campaign_store: CampaignStore,
        hop: CycleHop,
        cycle: Cycle,
        rounds: list[RoundResult],
        drifted: list[int],
    ) -> list[RoundResult]:
        return await rederive_critiques(campaign_store, hop, cycle, rounds, drifted)

    async def show_round(self, ctx: RoundContext) -> None:
        await run_generation_only_round(ctx)

    def review(
        self,
        selected: SelectedOptimizer,
        rounds: list[RoundResult],
        audits: list[RoundAudit | None],
        *,
        context_object: list[str],
    ) -> ReviewReading:
        return review_reading(rounds, audits, context_object=context_object)

    def round_facts(
        self, selected: SelectedOptimizer, round_result: RoundResult
    ) -> list[OptimizerFact]:
        return round_facts(round_result)


MEMBERS = (
    AdaptiveQueue(),
    L1Generate(),
    PoBB(),
    ThetaElection(),
    L1Critique(),
    Escalation(),
    L2,
    L3,
)
RUNTIME = PotterRuntime()
