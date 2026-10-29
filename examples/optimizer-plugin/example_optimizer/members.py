"""A third-party optimizer as small as the bench allows, shipped outside the package and loaded
through its entry points alone: a random panel, a rephrasing llm node, a keep-the-best selector."""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from typing import TYPE_CHECKING, Annotated, ClassVar

from pydantic import Field

from example_optimizer import prompts
from promptpotter.application.bench.node_context import Calls, Children, Lineage, Replay, Target
from promptpotter.application.optimizers import ManifestDir, nodes, paper_templates
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
    from promptpotter.domain.arm_reading import DisplayMetric
    from promptpotter.domain.opt_search_point import VariationMode
    from promptpotter.domain.paired_reading import ArmKey
    from promptpotter.domain.sample import Sample
    from promptpotter.domain.scoring import CellSheet

MANIFEST = ManifestDir("example", Path(__file__).parent)


class DrawKnobs(StrictModel):
    size: Annotated[int, Knob(Scope.POLICY, Estimand.SELECTION)] = Field(
        ge=1, description="Cells a round draws."
    )


class RandomDraw(nodes.Sampler):
    name: ClassVar[str] = "example_draw"
    kind: ClassVar[NodeKind] = NodeKind.SAMPLER
    knobs: ClassVar[type[StrictModel]] = DrawKnobs
    sees = frozenset({Replay})
    size_knob: ClassVar[str | None] = "size"

    def round_cells(self, knobs: DrawKnobs, search: int) -> int:
        return min(knobs.size, search)

    def draw(self, ctx: NodeContext, pool: list[Sample]) -> Panel:
        cells = ctx.read(Replay).rng().sample(pool, min(ctx.knobs(DrawKnobs).size, len(pool)))
        return nodes.Panel(cells=cells, order=list(cells), block_size=len(cells))


class ProposeKnobs(RewriteKnobs):
    variants: Annotated[int, Knob(Scope.POLICY, Estimand.SEARCH)] = Field(
        ge=1, description="Arms a round proposes."
    )


class Propose(nodes.Proposer):
    name: ClassVar[str] = "propose"
    kind: ClassVar[NodeKind] = NodeKind.LLM
    prompt_arity: ClassVar[PromptArity] = "one"
    opens: ClassVar[bool] = True
    variation_mode: ClassVar[VariationMode] = "llm"
    knobs: ClassVar[type[StrictModel]] = ProposeKnobs
    sees = frozenset({Calls, Children, Lineage, Target})
    prompt_sources: ClassVar[tuple[ModuleType, ...]] = (paper_templates, prompts)

    def arms(self, selected: SelectedOptimizer) -> int:
        return selected.knobs_of(self.name, ProposeKnobs).variants

    async def propose(self, ctx: NodeContext, _panel: Panel, _proposals: Proposals) -> Proposals:
        parent, node = ctx.read(Lineage).parent, ctx.read(Target).prompt_node
        prompt = prompts.rephrase_prompt(ctx, instruction=shown(ctx, parent, node))
        variants = ctx.knobs(ProposeKnobs).variants
        answers = await ctx.read(Calls).ask_each(dict.fromkeys(range(variants), prompt))
        return nodes.Proposals(
            [child(ctx, node, [parent], raw, f"rephrase {i}") for i, raw in enumerate(answers)]
        )


class Keep(nodes.Selector):
    name: ClassVar[str] = "example_keep"
    kind: ClassVar[NodeKind] = NodeKind.SELECTOR
    sees = frozenset({Lineage, Replay})
    elects_on: ClassVar[DisplayMetric] = "composite"
    elects_partial: ClassVar[bool] = False

    def parent_cells(
        self,
        _ctx: NodeContext,
        panel: Panel,
        _rows: Mapping[ArmKey, CellSheet],
    ) -> list[Sample]:
        return panel.cells

    def select(self, ctx: NodeContext, measured: MeasuredRound) -> Selection:
        folds = measured.folds
        fitness = {key: folds[key].composite_fitness or 0.0 for key in measured.electable}
        ranked = sorted(measured.electable, key=lambda key: -fitness[key])
        bar = folds[measured.parent.key].composite_fitness or 0.0
        selected = ranked[0] if ranked and fitness[ranked[0]] > bar else None
        ctx.read(Replay).decide(
            "example_kept", {"round_num": ctx.round_num, "ranked": ranked}, selected or ""
        )
        return nodes.Selection(
            selected=selected,
            leading=ranked[0] if ranked else None,
            verdict_reason=f"kept {selected[:8] if selected else 'the parent'}",
            population=list(ctx.read(Lineage).population),
        )


DRAW, PROPOSE, KEEP = RandomDraw(), Propose(), Keep()
