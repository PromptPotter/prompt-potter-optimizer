"""A third-party optimizer as small as the bench allows, shipped outside the package and loaded
through its entry points alone: a random panel, a rephrasing llm node, a keep-the-best selector."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import TYPE_CHECKING, Annotated, Any, ClassVar

from pydantic import Field

from example_optimizer import prompts
from promptpotter.application.campaign_config import Estimand, Knob, Scope
from promptpotter.application.optimizers import nodes, paper_templates
from promptpotter.application.optimizers.paper_templates import RewriteKnobs, child, shown
from promptpotter.domain.optimizer_state import RoundPayload
from promptpotter.domain.pipeline_schema import NodeKind
from promptpotter.domain.strict_model import StrictModel

if TYPE_CHECKING:
    from types import ModuleType

    from promptpotter.application.bench.node_context import NodeContext
    from promptpotter.application.campaign_config import CampaignConfig
    from promptpotter.application.initialization.session import Session
    from promptpotter.application.optimizer_manifest import SelectedOptimizer
    from promptpotter.application.optimizers.nodes import (
        BankedState,
        Measured,
        Panel,
        Proposals,
        Selection,
    )
    from promptpotter.domain.results import DisplayMetric
    from promptpotter.domain.sample import Sample
    from promptpotter.domain.scoring import QueryMeasurement

MANIFEST = "example"


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
    size_knob: ClassVar[str | None] = "size"

    def draws(self, selected: SelectedOptimizer, pool: int) -> int:
        return min(selected.knobs_of(self.name, DrawKnobs).size, pool)

    def draw(self, ctx: NodeContext[DrawKnobs], pool: list[Sample]) -> Panel:
        cells = ctx.rng().sample(pool, min(ctx.knobs.size, len(pool)))
        return nodes.Panel(cells=cells, order=list(cells), block_size=len(cells))


class ProposeKnobs(RewriteKnobs):
    variants: Annotated[int, Knob(Scope.POLICY, Estimand.SEARCH)] = Field(
        ge=1, description="Arms a round proposes."
    )


class Propose:
    name: ClassVar[str] = "propose"
    kind: ClassVar[NodeKind] = NodeKind.LLM
    opens: ClassVar[bool] = True
    knobs: ClassVar[type[StrictModel]] = ProposeKnobs

    async def propose(
        self, ctx: NodeContext[ProposeKnobs], panel: Panel, proposals: Proposals
    ) -> Proposals:
        parent = ctx.parent
        prompt = prompts.rephrase_prompt(ctx, instruction=shown(ctx, parent))
        answers = await ctx.ask_each(dict.fromkeys(range(ctx.knobs.variants), prompt))
        return nodes.Proposals(
            [child(ctx, [parent], raw, f"rephrase {i}") for i, raw in enumerate(answers)]
        )


class KeepKnobs(StrictModel):
    pass


class Keep:
    name: ClassVar[str] = "example_keep"
    kind: ClassVar[NodeKind] = NodeKind.SELECTOR
    knobs: ClassVar[type[StrictModel]] = KeepKnobs
    elects_on: ClassVar[DisplayMetric] = "composite"
    elects_partial: ClassVar[bool] = False

    def parent_cells(
        self,
        ctx: NodeContext[KeepKnobs],
        panel: Panel,
        rows: Mapping[str, Sequence[QueryMeasurement]],
    ) -> list[Sample]:
        return panel.cells

    def select(
        self, ctx: NodeContext[KeepKnobs], measured: Measured, proposals: Proposals
    ) -> Selection:
        fitness = {cs.candidate_id: cs.composite_fitness or 0.0 for cs in measured.scores}
        ranked = sorted((ind.id for ind in measured.electable), key=lambda c: -fitness[c])
        bar = measured.parent.report.composite_fitness or 0.0
        selected_id = ranked[0] if ranked and fitness[ranked[0]] > bar else ""
        ctx.decide("example_kept", {"round_num": ctx.round_num, "ranked": ranked}, selected_id)
        return nodes.Selection(
            selected_id=selected_id,
            leading_id=ranked[0] if ranked else "",
            scores=list(measured.scores),
            verdict_reason=f"kept {selected_id[:8] or 'the parent'}",
        )


class ExampleRuntime(nodes.OptimizerRuntime):
    name: ClassVar[str] = MANIFEST
    manifest_dir: ClassVar[Path] = Path(__file__).parent
    prompt_sources: ClassVar[tuple[ModuleType, ...]] = (paper_templates, prompts)

    def start(
        self, session: Session, config: CampaignConfig, origin_results: list[dict[str, Any]]
    ) -> BankedState[ExampleRoundState]:
        return nodes.BankedState(ExampleRoundState())

    def arms(self, selected: SelectedOptimizer) -> int:
        return selected.knobs_of(Propose.name, ProposeKnobs).variants


DRAW, PROPOSE, KEEP = Draw(), Propose(), Keep()
RUNTIME = ExampleRuntime()
