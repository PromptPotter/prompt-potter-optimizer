"""A third-party optimizer as small as the bench allows, shipped outside the package and loaded
through its entry points alone: a random panel, a rephrasing llm node, a keep-the-best selector."""

from __future__ import annotations

import asyncio
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import TYPE_CHECKING, Annotated, Any, ClassVar, cast

from pydantic import Field

from example_optimizer import operators
from promptpotter.application.bench.resume_and_fork.decisions import GatingMode, record_decision
from promptpotter.application.campaign_config import Estimand, Knob, Scope
from promptpotter.application.optimizers import nodes
from promptpotter.application.optimizers.paper_templates import (
    PaperRuntime,
    ask,
    child,
    walk_rng,
)
from promptpotter.domain.opt_search_point import node_source
from promptpotter.domain.optimizer_state import RoundPayload
from promptpotter.domain.pipeline_schema import NodeKind
from promptpotter.domain.results import OptimizerFact
from promptpotter.domain.run_records import CheckpointKind
from promptpotter.domain.strict_model import StrictModel

if TYPE_CHECKING:
    from types import ModuleType

    from promptpotter.application.bench.resume_and_fork.replayers import Replayer
    from promptpotter.application.campaign_config import CampaignConfig
    from promptpotter.application.initialization.session import Session
    from promptpotter.application.optimizer_manifest import SelectedOptimizer
    from promptpotter.application.optimizers.nodes import (
        BankedState,
        Measured,
        Panel,
        Population,
        RoundContext,
        Selection,
    )
    from promptpotter.domain.results import RoundResult
    from promptpotter.domain.sample import Sample
    from promptpotter.domain.scoring import QueryMeasurement

MANIFEST = "example"


class ExampleCheckpointKind(CheckpointKind):
    KEPT = "example_kept"


class ExampleRoundState(RoundPayload, manifest=MANIFEST):
    """Empty: the round's parent is all this optimizer carries, and the bench holds that."""


class DrawKnobs(StrictModel):
    size: Annotated[int, Knob(Scope.POLICY, Estimand.SELECTION)] = Field(
        ge=1, description="Cells a round draws."
    )


class Draw:
    name: ClassVar[str] = "example_draw"
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


class ProposeKnobs(StrictModel):
    variants: Annotated[int, Knob(Scope.POLICY, Estimand.SEARCH)] = Field(
        ge=1, description="Arms a round proposes."
    )


class Propose:
    name: ClassVar[str] = "propose"
    kind: ClassVar[NodeKind] = NodeKind.LLM
    opens: ClassVar[bool] = True
    knobs: ClassVar[type[StrictModel]] = ProposeKnobs
    couplings: ClassVar[tuple[nodes.MemberCoupling, ...]] = ()

    async def propose(self, ctx: RoundContext, panel: Panel, population: Population) -> Population:
        cycle = ctx.cycle
        n = cast("ProposeKnobs", cycle.optimizer.knobs(self.name)).variants
        parent = cycle.opt_sp
        prompt = operators.rephrase_prompt(cycle, self.name, instruction=parent.instruction)
        answers = await asyncio.gather(*(ask(ctx, self.name, i, prompt) for i in range(n)))
        source = node_source(MANIFEST, self.name)
        return nodes.Population.of(
            [
                child(source, self.name, [parent], raw, f"rephrase {i}")
                for i, raw in enumerate(answers)
            ]
        )


class KeepKnobs(StrictModel):
    pass


class Keep:
    name: ClassVar[str] = "example_keep"
    kind: ClassVar[NodeKind] = NodeKind.SELECTOR
    knobs: ClassVar[type[StrictModel]] = KeepKnobs
    couplings: ClassVar[tuple[nodes.MemberCoupling, ...]] = ()
    stamps_theta: ClassVar[bool] = False
    elects_partial: ClassVar[bool] = False

    def parent_cells(
        self, ctx: RoundContext, panel: Panel, rows: Mapping[str, Sequence[QueryMeasurement]]
    ) -> list[Sample]:
        return panel.cells

    def select(self, ctx: RoundContext, measured: Measured, population: Population) -> Selection:
        fitness = {cs.candidate_id: cs.composite_fitness or 0.0 for cs in measured.scores}
        ranked = sorted((ind.lineage.id for ind in measured.electable), key=lambda c: -fitness[c])
        bar = measured.parent.report.composite_fitness or 0.0
        selected_id = ranked[0] if ranked and fitness[ranked[0]] > bar else ""
        record_decision(
            ctx.cycle.pending_decisions,
            ExampleCheckpointKind.KEPT,
            {"round_num": ctx.round_num, "ranked": ranked},
            selected_id,
            node=self.name,
            round=ctx.round_num,
        )
        return nodes.Selection(
            selected_id=selected_id,
            scores=list(measured.scores),
            verdict_reason=f"kept {selected_id[:8] or 'the parent'}",
            payload=nodes.state_as(ctx, ExampleRoundState).payload,
        )


class ExampleRuntime(PaperRuntime):
    name: ClassVar[str] = MANIFEST
    manifest_dir: ClassVar[Path] = Path(__file__).parent
    operators: ClassVar[ModuleType] = operators
    checkpoint_gating: ClassVar[Mapping[CheckpointKind, GatingMode]] = {
        ExampleCheckpointKind.KEPT: GatingMode.ARCHIVAL
    }
    replayers: ClassVar[Mapping[str, Replayer]] = {}

    def start(
        self, session: Session, config: CampaignConfig, origin_results: list[dict[str, Any]]
    ) -> BankedState[ExampleRoundState]:
        return nodes.BankedState(ExampleRoundState())

    def arms(self, selected: SelectedOptimizer) -> int:
        return cast("ProposeKnobs", selected.knobs(Propose.name)).variants

    def round_cells_ceiling(self, selected: SelectedOptimizer, pool: int) -> int:
        return (self.arms(selected) + 1) * selected.round_cells(pool)

    def round_facts(
        self, selected: SelectedOptimizer, round_result: RoundResult
    ) -> list[OptimizerFact]:
        return []


DRAW, PROPOSE, KEEP = Draw(), Propose(), Keep()
RUNTIME = ExampleRuntime()
