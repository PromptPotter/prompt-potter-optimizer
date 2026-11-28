"""A third-party optimizer as small as the bench allows, shipped outside the package and loaded
through its entry points alone: a random panel, a rephrasing llm node, a keep-the-best selector."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Annotated, ClassVar

from pydantic import Field

from example_optimizer import prompts
from promptpotter.application import optimizer_contract
from promptpotter.application.bench.node_context import Calls, Children, Lineage, Replay, Target
from promptpotter.application.optimizer_contract import ManifestDir, RewriteKnobs
from promptpotter.application.optimizers.paper_templates import child, shown
from promptpotter.domain.campaign_config import Estimand, Knob
from promptpotter.domain.paired_reading import ArmKey
from promptpotter.domain.pipeline_schema import NodeKind
from promptpotter.domain.records.decision import DecisionInputs
from promptpotter.domain.strict_model import StrictModel

if TYPE_CHECKING:
    from promptpotter.application.bench.node_context import NodeContext
    from promptpotter.application.optimizer_contract import (
        MeasuredRound,
        PromptArity,
        Proposals,
        Ranking,
        RoundSamples,
    )
    from promptpotter.application.optimizer_manifest import SelectedOptimizer
    from promptpotter.domain.arm_reading import DisplayMetric
    from promptpotter.domain.opt_search_point import VariationMode
    from promptpotter.domain.sample import Sample

MANIFEST = ManifestDir("example", Path(__file__).parent)


class ExampleKept(DecisionInputs, kind="example_kept"):
    """Archived on the ledger; a ``Replayed`` subclass would have a resume re-derive it."""

    ranked: list[ArmKey]


class DrawKnobs(StrictModel):
    size: Annotated[int, Knob(Estimand.SELECTION)] = Field(ge=1, description="Cells a round draws.")


class RandomDraw(optimizer_contract.Sampler):
    name: ClassVar[str] = "example_draw"
    kind: ClassVar[NodeKind] = NodeKind.SAMPLER
    knobs: ClassVar[type[StrictModel]] = DrawKnobs
    sees = frozenset({Replay})
    size_knob: ClassVar[str | None] = "size"

    def drawn(self, knobs: DrawKnobs, search: int, _pipeline: str) -> optimizer_contract.DrawSize:
        cells = min(knobs.size, search)
        return optimizer_contract.DrawSize(cells=cells, block_size=cells)

    def draw(self, ctx: NodeContext, pool: list[Sample]) -> RoundSamples:
        cells = ctx.read(Replay).rng().sample(pool, min(ctx.knobs(DrawKnobs).size, len(pool)))
        return optimizer_contract.RoundSamples(
            cells=cells, order=list(cells), block_size=len(cells)
        )


class ProposeKnobs(RewriteKnobs):
    variants: Annotated[int, Knob(Estimand.SEARCH)] = Field(
        ge=1, description="Arms a round proposes."
    )


class Propose(optimizer_contract.Proposer):
    name: ClassVar[str] = "propose"
    kind: ClassVar[NodeKind] = NodeKind.LLM
    prompt_arity: ClassVar[PromptArity] = "one"
    proposes: ClassVar[optimizer_contract.Proposing] = "opens"
    variation_mode: ClassVar[VariationMode] = "llm"
    knobs: ClassVar[type[StrictModel]] = ProposeKnobs
    sees = frozenset({Calls, Children, Lineage, Target})

    def arms(self, selected: SelectedOptimizer) -> int:
        return selected.knobs_of(self.name, ProposeKnobs).variants

    async def propose(self, ctx: NodeContext, _proposals: Proposals) -> Proposals:
        parent, node = ctx.read(Lineage).parent, ctx.read(Target).prompt_node
        prompt = prompts.rephrase_prompt(ctx, instruction=shown(ctx, parent, node))
        variants = ctx.knobs(ProposeKnobs).variants
        answers = await ctx.read(Calls).ask_each(dict.fromkeys(range(variants), prompt))
        return optimizer_contract.Proposals(
            [child(ctx, node, [parent], raw, f"rephrase {i}") for i, raw in enumerate(answers)]
        )


class Keep(optimizer_contract.RankingSelector):
    """Ranks alone: the bench crowns the best where it beats the parent, and holds otherwise."""

    name: ClassVar[str] = "example_keep"
    kind: ClassVar[NodeKind] = NodeKind.SELECTOR
    sees = frozenset({Lineage, Replay})
    elects_partial: ClassVar[bool] = False
    elects_on: ClassVar[DisplayMetric] = "composite"

    def rank(self, ctx: NodeContext, measured: MeasuredRound) -> Ranking:
        folds = measured.folds
        # An arm with no composite is unread, never ranked; a parent with none sets no bar.
        fitness = {
            key: value
            for key in measured.electable
            if (value := folds[key].composite_fitness) is not None
        }
        ranked = sorted(fitness, key=lambda key: -fitness[key])
        ctx.read(Replay).decide(ExampleKept(ranked=ranked), ranked)
        bar = folds[measured.parent.key].composite_fitness
        return optimizer_contract.Ranking(
            standing=fitness if bar is None else {measured.parent.key: bar, **fitness},
            arms=fitness,
            verdict_reason=f"ranked {len(ranked)} arms on composite fitness",
            ranked=list(ctx.read(Lineage).population),
        )


DRAW, PROPOSE, KEEP = RandomDraw(), Propose(), Keep()
