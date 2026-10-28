"""A third-party optimizer as small as the bench allows, shipped outside the package and loaded
through its entry points alone: a random panel, a rephrasing llm node, a keep-the-best selector."""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from typing import TYPE_CHECKING, Annotated, ClassVar

from pydantic import Field

from example_optimizer import prompts
from promptpotter.application.bench.node_context import Lineage
from promptpotter.application.optimizers import nodes, paper_templates
from promptpotter.application.optimizers.nodes import RewriteKnobs
from promptpotter.application.optimizers.paper_templates import child, shown
from promptpotter.domain.campaign_config import Estimand, Knob, Scope
from promptpotter.domain.pipeline_schema import NodeKind
from promptpotter.domain.strict_model import StrictModel

if TYPE_CHECKING:
    from types import ModuleType

    from promptpotter.application.bench.node_context import NodeContext
    from promptpotter.application.optimizer_manifest import SelectedOptimizer
    from promptpotter.application.optimizers.nodes import (
        MeasuredRound,
        Panel,
        PromptArity,
        Proposals,
        Selection,
    )
    from promptpotter.domain.opt_search_point import VariationMode
    from promptpotter.domain.results import DisplayMetric
    from promptpotter.domain.sample import Sample
    from promptpotter.domain.scoring import CellSheet

MANIFEST = "example"


class DrawKnobs(StrictModel):
    size: Annotated[int, Knob(Scope.POLICY, Estimand.SELECTION)] = Field(
        ge=1, description="Cells a round draws."
    )


class RandomDraw(nodes.Member):
    name: ClassVar[str] = "example_draw"
    kind: ClassVar[NodeKind] = NodeKind.SAMPLER
    knobs: ClassVar[type[StrictModel]] = DrawKnobs
    size_knob: ClassVar[str | None] = "size"

    def round_cells(self, knobs: DrawKnobs, search: int) -> int:
        return min(knobs.size, search)

    def draw(self, ctx: NodeContext[DrawKnobs], pool: list[Sample]) -> Panel:
        cells = ctx.rng().sample(pool, min(ctx.knobs.size, len(pool)))
        return nodes.Panel(cells=cells, order=list(cells), block_size=len(cells))


class ProposeKnobs(RewriteKnobs):
    variants: Annotated[int, Knob(Scope.POLICY, Estimand.SEARCH)] = Field(
        ge=1, description="Arms a round proposes."
    )


class Propose(nodes.Member):
    name: ClassVar[str] = "propose"
    kind: ClassVar[NodeKind] = NodeKind.LLM
    prompt_arity: ClassVar[PromptArity] = "one"
    opens: ClassVar[bool] = True
    variation_mode: ClassVar[VariationMode] = "llm"
    knobs: ClassVar[type[StrictModel]] = ProposeKnobs
    sees = frozenset({Lineage})

    async def propose(
        self, ctx: NodeContext[ProposeKnobs], panel: Panel, proposals: Proposals
    ) -> Proposals:
        parent, node = ctx.read(Lineage).parent, ctx.prompt_node
        prompt = prompts.rephrase_prompt(ctx, instruction=shown(ctx, parent, node))
        answers = await ctx.ask_each(dict.fromkeys(range(ctx.knobs.variants), prompt))
        return nodes.Proposals(
            [child(ctx, node, [parent], raw, f"rephrase {i}") for i, raw in enumerate(answers)]
        )


class Keep(nodes.Member):
    name: ClassVar[str] = "example_keep"
    kind: ClassVar[NodeKind] = NodeKind.SELECTOR
    elects_on: ClassVar[DisplayMetric] = "composite"
    elects_partial: ClassVar[bool] = False

    def parent_cells(
        self,
        ctx: NodeContext[StrictModel],
        panel: Panel,
        rows: Mapping[str, CellSheet],
    ) -> list[Sample]:
        return panel.cells

    def select(
        self, ctx: NodeContext[StrictModel], measured: MeasuredRound, proposals: Proposals
    ) -> Selection:
        folds = measured.folds
        fitness = {key: folds[key].composite_fitness or 0.0 for key in measured.electable}
        ranked = sorted(measured.electable, key=lambda key: -fitness[key])
        bar = folds[measured.parent.key].composite_fitness or 0.0
        selected = ranked[0] if ranked and fitness[ranked[0]] > bar else None
        ctx.decide("example_kept", {"round_num": ctx.round_num, "ranked": ranked}, selected or "")
        return nodes.Selection(
            selected=selected,
            leading=ranked[0] if ranked else None,
            verdict_reason=f"kept {selected[:8] if selected else 'the parent'}",
        )


class ExampleRuntime(nodes.OptimizerRuntime):
    name: ClassVar[str] = MANIFEST
    manifest_dir: ClassVar[Path] = Path(__file__).parent
    prompt_sources: ClassVar[tuple[ModuleType, ...]] = (paper_templates, prompts)

    def arms(self, selected: SelectedOptimizer) -> int:
        return selected.knobs_of(Propose.name, ProposeKnobs).variants


DRAW, PROPOSE, KEEP = RandomDraw(), Propose(), Keep()
RUNTIME = ExampleRuntime()
