"""A third-party optimizer as small as the bench allows, shipped outside the package and loaded
through its entry points alone: a random panel, a rephrasing llm node, a keep-the-best selector."""

from __future__ import annotations

import asyncio
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Annotated, Any, ClassVar, cast

from pydantic import Field

from fixture_optimizer import operators
from promptpotter.application.bench.resume_and_fork.decisions import GatingMode, record_decision
from promptpotter.application.campaign_config import Estimand, Knob, Scope
from promptpotter.application.optimizers import nodes
from promptpotter.application.optimizers.paper_templates import (
    ask,
    marked,
    preset_source_digest,
    unmarked,
    walk_rng,
)
from promptpotter.domain.opt_search_point import OptSearchPoint, node_source
from promptpotter.domain.optimizer_state import OptimizerState, RoundPayload
from promptpotter.domain.pipeline_schema import NodeKind
from promptpotter.domain.results import CandidateProposal, OptimizerFact, candidate_label
from promptpotter.domain.run_records import CandidateMintedRecord, CheckpointKind
from promptpotter.domain.strict_model import StrictModel

if TYPE_CHECKING:
    from types import ModuleType

    from pydantic import BaseModel

    from promptpotter.application.bench.cycle import Cycle
    from promptpotter.application.bench.resume_and_fork.replayers import Replayer
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
        WorkingState,
    )
    from promptpotter.domain.cycle_paths import CycleHop
    from promptpotter.domain.results import RoundResult
    from promptpotter.domain.sample import Sample
    from promptpotter.infrastructure.ledger import CycleEventLog
    from promptpotter.infrastructure.store.campaign_store.store import CampaignStore

MANIFEST = "fixture"


class FixtureCheckpointKind(CheckpointKind):
    KEPT = "fixture_kept"


class FixtureRoundState(RoundPayload, manifest=MANIFEST):
    rounds_without_advance: int


@dataclass
class FixtureState:
    rounds_without_advance: int = 0

    def snapshot(self, prompt_hashes: dict[str, str], stall: int) -> OptimizerState:
        return OptimizerState(
            manifest=MANIFEST,
            prompt_hashes=prompt_hashes,
            payload=FixtureRoundState(rounds_without_advance=stall),
        )

    def origin_state(self, selected: SelectedOptimizer) -> OptimizerState:
        return self.snapshot(selected.prompt_hashes(), self.rounds_without_advance)

    def replay(self, last: RoundResult) -> None:
        self.absorb(last)

    def resume(
        self, ledger: CycleEventLog | None, selected: SelectedOptimizer, *, before_round: int
    ) -> None:
        return None

    def absorb(self, round_result: RoundResult) -> None:
        payload = round_result.optimizer_state.payload_as(FixtureRoundState)
        self.rounds_without_advance = payload.rounds_without_advance

    def standing(self) -> tuple[int, int | None]:
        return self.rounds_without_advance, None


def _state(state: WorkingState) -> FixtureState:
    assert isinstance(state, FixtureState)
    return state


class DrawKnobs(StrictModel):
    size: Annotated[int, Knob(Scope.POLICY, Estimand.SELECTION)] = Field(
        ge=1, description="Cells a round draws."
    )


class Draw:
    name: ClassVar[str] = "fixture_draw"
    kind: ClassVar[NodeKind] = NodeKind.SAMPLER
    knobs: ClassVar[type[StrictModel]] = DrawKnobs
    couplings: ClassVar[tuple[nodes.MemberCoupling, ...]] = ()
    size_knob: ClassVar[str | None] = "size"

    def draws(self, selected: SelectedOptimizer, pool: int) -> int:
        return min(cast("DrawKnobs", selected.knobs(self.name)).size, pool)

    def draw(self, ctx: RoundContext, pool: list[Sample]) -> Panel:
        size = cast("DrawKnobs", ctx.cycle.optimizer.knobs(self.name)).size
        cells = walk_rng(ctx.cycle, ctx.round_num, self.name).sample(pool, min(size, len(pool)))
        return nodes.Panel(cells=cells, order=list(cells), block_size=len(cells))


class RephraseKnobs(StrictModel):
    variants: Annotated[int, Knob(Scope.POLICY, Estimand.SEARCH)] = Field(
        ge=1, description="Rephrasings a round proposes."
    )


class Rephrase:
    name: ClassVar[str] = "fixture_rephrase"
    kind: ClassVar[NodeKind] = NodeKind.LLM
    knobs: ClassVar[type[StrictModel]] = RephraseKnobs
    couplings: ClassVar[tuple[nodes.MemberCoupling, ...]] = ()

    async def propose(
        self, ctx: RoundContext, panel: Panel, population: Population | None
    ) -> Population:
        cycle = ctx.cycle
        n = cast("RephraseKnobs", cycle.optimizer.knobs(self.name)).variants
        parent = cycle.opt_sp
        prompt = operators.rephrase_prompt(cycle, self.name, instruction=parent.instruction)
        answers = await asyncio.gather(*(ask(ctx, self.name, i, prompt) for i in range(n)))
        proposals = []
        for i, raw in enumerate(answers):
            text = marked(raw)
            child = OptSearchPoint.derive(
                [parent],
                source=node_source(MANIFEST, self.name),
                changes_description=f"rephrase {i}",
                instruction=parent.instruction if text is None else text,
            )
            failures = [] if text is not None else [unmarked(self.name, raw)]
            proposals.append(CandidateProposal(opt_sp=child, validation_failures=failures))
            if (ledger := cycle.session.state.ledger) is not None:
                ledger.append(
                    CandidateMintedRecord(
                        round=ctx.round_num,
                        idx=i,
                        candidate_id=child.lineage.id,
                        parent_ids=list(child.lineage.parent_ids),
                        label=candidate_label(ctx.round_num, i),
                        changes_description=child.lineage.changes_description,
                        source=child.lineage.source,
                    )
                )
        assert cycle.tracking.current_sp is not None
        state = _state(ctx.state)
        return nodes.Population(
            proposals=proposals,
            individuals=[p.opt_sp for p in proposals],
            pipeline_params=[cycle.tracking.current_sp.pipeline_params] * n,
            optimizer_state=state.snapshot(
                cycle.optimizer.prompt_hashes(), state.rounds_without_advance
            ),
        )


class KeepKnobs(StrictModel):
    pass


class Keep:
    name: ClassVar[str] = "fixture_keep"
    kind: ClassVar[NodeKind] = NodeKind.SELECTOR
    knobs: ClassVar[type[StrictModel]] = KeepKnobs
    couplings: ClassVar[tuple[nodes.MemberCoupling, ...]] = ()
    stamps_theta: ClassVar[bool] = False
    reads_parent: ClassVar[bool] = True

    def select(self, ctx: RoundContext, measured: Measured, population: Population) -> Selection:
        state = _state(ctx.state)
        fitness = {cs.candidate_id: cs.composite_fitness or 0.0 for cs in measured.scores}
        ranked = sorted((ind.lineage.id for ind in measured.electable), key=lambda c: -fitness[c])
        bar = measured.parent.report.composite_fitness or 0.0
        selected_id = ranked[0] if ranked and fitness[ranked[0]] > bar else ""
        record_decision(
            ctx.cycle.pending_decisions,
            FixtureCheckpointKind.KEPT,
            {"round_num": ctx.round_num, "ranked": ranked},
            selected_id,
            node=self.name,
            round=ctx.round_num,
        )
        return nodes.Selection(
            selected_id=selected_id,
            scores=list(measured.scores),
            verdict_reason=f"kept {selected_id[:8] or 'the parent'}",
            optimizer_state=state.snapshot(
                population.optimizer_state.prompt_hashes,
                0 if selected_id else state.rounds_without_advance + 1,
            ),
        )


class FixtureRuntime:
    name: ClassVar[str] = MANIFEST
    manifest_dir: ClassVar[Path] = Path(__file__).parent
    own_axes: ClassVar[dict[str, set[str]]] = {}
    priced_surface: ClassVar[Mapping[str, int]] = {}
    phases: ClassVar[tuple[nodes.OptimizerPhase, ...]] = ()
    response_models: ClassVar[Mapping[str, type[BaseModel]]] = {}
    checkpoint_gating: ClassVar[Mapping[CheckpointKind, GatingMode]] = {
        FixtureCheckpointKind.KEPT: GatingMode.ARCHIVAL
    }
    replayers: ClassVar[Mapping[str, Replayer]] = {}

    def start(
        self, session: Session, config: CampaignConfig, origin_results: list[dict[str, Any]]
    ) -> FixtureState:
        return FixtureState()

    def complete(self) -> None:
        return None

    def source_digest(self, *covered: ModuleType) -> str:
        return preset_source_digest(operators, *covered)

    def override_param_types(self, node: str) -> dict[str, str]:
        return {}

    def override_levers(self, node: str, declared: Mapping[str, Any]) -> dict[str, Any]:
        return {}

    def round_packages(self, cycle: Cycle, rounds: list[RoundResult]) -> dict[int, dict[str, str]]:
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

    def pacing(self, selected: SelectedOptimizer) -> nodes.OptimizerPacing:
        n = cast("RephraseKnobs", selected.knobs(Rephrase.name)).variants
        return nodes.OptimizerPacing(patience=None, stalls_left=None, arms_per_round=n, limits=())

    def round_cells_ceiling(self, selected: SelectedOptimizer, pool: int) -> int:
        n = cast("RephraseKnobs", selected.knobs(Rephrase.name)).variants
        return (n + 1) * selected.round_cells(pool)

    def opening(self, ctx: RoundContext) -> nodes.RoundOpening:
        return nodes.standing_opening(ctx)

    def round_facts(
        self, selected: SelectedOptimizer, round_result: RoundResult
    ) -> list[OptimizerFact]:
        return []


DRAW, REPHRASE, KEEP = Draw(), Rephrase(), Keep()
RUNTIME = FixtureRuntime()
