"""The pool initializers, the one roster a manifest's `run_init` walk names: each writes its pool once, before round 0's adapters read it."""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import TYPE_CHECKING, Annotated, ClassVar

from pydantic import Field

from promptpotter.application.bench.node_context import Target
from promptpotter.application.campaign_config import Estimand, Knob, Scope
from promptpotter.application.optimizers import nodes
from promptpotter.application.scoring.formula.matchers import MATCHERS
from promptpotter.config.prompt_blocks import library_entries, library_identity
from promptpotter.domain.pipeline_schema import NodeKind, PipelineSchema
from promptpotter.domain.pool import (
    EARNED_SOURCE,
    BlockPool,
    DemoPool,
    Pool,
    PoolEntry,
    entry_id,
    kept,
    unnamed,
)
from promptpotter.domain.prompt_block import Presumption
from promptpotter.domain.strict_model import StrictModel

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence

    from promptpotter.application.bench.node_context import NodeContext
    from promptpotter.application.optimizer_manifest import SelectedOptimizer
    from promptpotter.application.optimizers.nodes import CycleRead, MintFacts
    from promptpotter.domain.optimizer_state import StatePart


class PoolKnobs(StrictModel):
    active: Annotated[list[str], Knob(Scope.POLICY, Estimand.SEARCH)] = Field(
        min_length=1,
        description="The entries in play, as paths: `*` for all, an entry's content id, or a "
        "leading run of its group — a block's `field` or `field/source`, the earned blocks' "
        f"source being `{EARNED_SOURCE}`; a shot's sample key. A path keeping nothing is refused "
        "before any spend.",
    )


class _PoolInitializer(nodes.Member, ABC):
    kind: ClassVar[NodeKind] = NodeKind.ALGORITHM
    knobs: ClassVar[type[StrictModel]] = PoolKnobs
    sees: ClassVar[frozenset[type[CycleRead]]] = frozenset({Target})
    pool: ClassVar[type[Pool]]

    def active(self, selected: SelectedOptimizer) -> list[str]:
        return selected.knobs_of(self.name, PoolKnobs).active

    def initialize(self, ctx: NodeContext[PoolKnobs]) -> None:
        ctx.put(self.seed(ctx, ctx.knobs.active))

    @abstractmethod
    def seed(self, ctx: NodeContext[PoolKnobs], paths: Sequence[str]) -> Pool: ...


def _admits(schema: PipelineSchema, matcher: str | None) -> frozenset[Presumption]:
    ranking = {Presumption.RANKING} if schema.emits_ranking else set()
    return frozenset(ranking) | (frozenset() if matcher is None else MATCHERS[matcher].admits)


class BlockPoolInitializer(_PoolInitializer):
    name: ClassVar[str] = BlockPool.kind
    pool: ClassVar[type[Pool]] = BlockPool
    writes: ClassVar[frozenset[type[StatePart]]] = frozenset({BlockPool})

    def starting_content(self) -> object:
        return library_identity()

    def refusal(
        self, selected: SelectedOptimizer, *, closed: bool, mint: MintFacts | None
    ) -> str | None:
        paths = self.active(selected)
        # At load no chain is known, so a path is checked against every entry any chain admits.
        admits = frozenset(Presumption) if mint is None else _admits(mint.schema, mint.matcher)
        library = [(e.id, e.group) for e in library_entries(admits=admits).values()]
        fields = dict.fromkeys(group[0] for _, group in library)
        # Earned entries exist only once run init mines them, so their group is all a path can name now.
        earned: list[tuple[str, tuple[str, ...]]] = [
            ("", (field, EARNED_SOURCE)) for field in fields
        ]
        if bad := unnamed(paths, library + earned):
            return (
                f"{self.name}.active names {bad}, which match no prompt block library entry this "
                "run admits"
            )
        if closed and not kept(paths, library):
            return (
                f"{self.name}.active keeps no library entry, and a `closed` reader may write only "
                "an entry's id — earned blocks are mined at run init and may be none"
            )
        return None

    def seed(self, ctx: NodeContext[PoolKnobs], paths: Sequence[str]) -> BlockPool:
        target = ctx.read(Target)
        earned = (
            PoolEntry(id=entry_id(field, text), group=(field, EARNED_SOURCE), text=text)
            for field, texts in target.earned_blocks().items()
            for text in texts
        )
        entries: Mapping[str, PoolEntry] = {
            **{e.id: e for e in earned},
            **library_entries(admits=_admits(ctx.schema, target.matcher)),
        }
        active = kept(paths, ((i, e.group) for i, e in entries.items()))
        return BlockPool(entries=tuple(entries[i] for i in active))


class DemoPoolInitializer(_PoolInitializer):
    name: ClassVar[str] = DemoPool.kind
    pool: ClassVar[type[Pool]] = DemoPool
    writes: ClassVar[frozenset[type[StatePart]]] = frozenset({DemoPool})

    def starting_content(self) -> object:
        return None

    def refusal(
        self, selected: SelectedOptimizer, *, closed: bool, mint: MintFacts | None
    ) -> str | None:
        # A partition's size is its split's declaration, so only its names are refused.
        paths = self.active(selected)
        if mint is not None and (bad := unnamed(paths, [(s.key, ()) for s in mint.demo])):
            return f"{self.name}.active names {bad}, which the demo partition lacks"
        return None

    def seed(self, ctx: NodeContext[PoolKnobs], paths: Sequence[str]) -> DemoPool:
        return DemoPool(keys=kept(paths, ((s.key, ()) for s in ctx.read(Target).demo_split)))


MEMBERS = (BlockPoolInitializer(), DemoPoolInitializer())
