"""The pool initializers, the one roster a manifest's `run_init` walk names: each writes its pool once, before round 0's adapters read it."""

from __future__ import annotations

from typing import TYPE_CHECKING, Annotated, ClassVar

from pydantic import Field

from promptpotter.application.bench.node_context import Target
from promptpotter.application.optimizers import nodes
from promptpotter.config.paths import block_packs_root
from promptpotter.config.prompt_blocks import missing_packs, pack_entries
from promptpotter.domain.campaign_config import Estimand, Knob, Scope
from promptpotter.domain.pipeline_schema import NodeKind
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
from promptpotter.domain.prompt_block import AUTHORED_SOURCE, PackName, Presumption
from promptpotter.domain.strict_model import StrictModel

if TYPE_CHECKING:
    from collections.abc import Mapping

    from promptpotter.application.bench.node_context import NodeContext
    from promptpotter.application.optimizer_manifest import SelectedOptimizer
    from promptpotter.application.optimizers.nodes import CycleRead, MintFacts
    from promptpotter.domain.optimizer_state import StatePart


class PoolKnobs(StrictModel):
    active: Annotated[list[str], Knob(Scope.POLICY, Estimand.SEARCH)] = Field(
        min_length=1,
        description="The entries in play, as paths: `*` for all, an entry's content id, or a "
        "leading run of its group — a block's `field` or `field/source`, a cited block's source "
        f"being its work's arXiv id, an authored one's `{AUTHORED_SOURCE}` and an earned one's "
        f"`{EARNED_SOURCE}`; a shot's sample key. A path keeping nothing is refused before any "
        f"spend, except `field/{EARNED_SOURCE}`, whose blocks are mined at run init.",
    )


class BlockPoolKnobs(PoolKnobs):
    packs: Annotated[list[PackName], Knob(Scope.POLICY, Estimand.SEARCH)] = Field(
        description="The block packs the pool draws from, in draw order: each a directory the "
        "operator installs under the user-data root's `block_packs/`, none shipped. Of their "
        "snippets, the entries `active` keeps are the treatment's starting content, so campaigns drawing "
        "different snippets never share an identity. Empty, the pool holds the earned blocks alone.",
    )


class _PoolInitializer(nodes.Initializer):
    kind: ClassVar[NodeKind] = NodeKind.ALGORITHM
    knobs: ClassVar[type[StrictModel]] = PoolKnobs
    sees: ClassVar[frozenset[type[CycleRead]]] = frozenset({Target})
    pool: ClassVar[type[Pool]]

    def active(self, selected: SelectedOptimizer) -> list[str]:
        return selected.knobs_of(self.name, PoolKnobs).active


class BlockPoolInitializer(_PoolInitializer):
    name: ClassVar[str] = BlockPool.kind
    knobs: ClassVar[type[StrictModel]] = BlockPoolKnobs
    pool: ClassVar[type[Pool]] = BlockPool
    writes: ClassVar[frozenset[type[StatePart]]] = frozenset({BlockPool})

    def starting_content(
        self, selected: SelectedOptimizer, admits: frozenset[Presumption]
    ) -> object:
        # A snippet the run cannot admit is never drawn, so its edit re-keys nothing.
        knobs = selected.knobs_of(self.name, BlockPoolKnobs)
        held = pack_entries(knobs.packs, admits=admits)
        return [
            (list(held[i].group), held[i].text)
            for i in kept(knobs.active, ((i, e.group) for i, e in held.items()))
        ]

    def held(self, selected: SelectedOptimizer, mint: MintFacts | None) -> list[PoolEntry]:
        packs = selected.knobs_of(self.name, BlockPoolKnobs).packs
        # At load no chain is known, so a path is checked against every entry any chain admits.
        admitted = frozenset(Presumption) if mint is None else mint.admits
        return list(pack_entries(packs, admits=admitted).values())

    def refusal(
        self, selected: SelectedOptimizer, closed: bool, mint: MintFacts | None
    ) -> str | None:
        packs = selected.knobs_of(self.name, BlockPoolKnobs).packs
        if absent := missing_packs(packs):
            return f"{self.name}.packs names {absent}, which {block_packs_root()} does not hold"
        paths = self.active(selected)
        held = [(e.id, e.group) for e in self.held(selected, mint)]
        named = [p for p in paths if p.partition("/")[2] != EARNED_SOURCE]
        if bad := unnamed(named, held):
            return (
                f"{self.name}.active names {bad}, which match no entry of {packs} this run admits"
            )
        if closed and not kept(paths, held):
            return (
                f"{self.name}.active keeps no pack entry, and a `closed` reader may write only "
                "an entry's id — earned blocks are mined at run init and may be none"
            )
        return None

    def initialize(self, ctx: NodeContext) -> None:
        target, knobs = ctx.read(Target), ctx.knobs(BlockPoolKnobs)
        earned = (
            PoolEntry(id=entry_id(field, text), group=(field, EARNED_SOURCE), text=text)
            for field, texts in target.earned_blocks().items()
            for text in texts
        )
        entries: Mapping[str, PoolEntry] = {
            **{e.id: e for e in earned},
            **pack_entries(knobs.packs, admits=nodes.admits(target.schema, target.matcher)),
        }
        active = kept(knobs.active, ((i, e.group) for i, e in entries.items()))
        ctx.put(BlockPool(entries=tuple(entries[i] for i in active)))


class DemoPoolInitializer(_PoolInitializer):
    name: ClassVar[str] = DemoPool.kind
    pool: ClassVar[type[Pool]] = DemoPool
    writes: ClassVar[frozenset[type[StatePart]]] = frozenset({DemoPool})

    def starting_content(
        self, _selected: SelectedOptimizer, _admits: frozenset[Presumption]
    ) -> object:
        return None

    def held(self, _selected: SelectedOptimizer, mint: MintFacts | None) -> list[PoolEntry]:
        # The split is drawn at mint, so at load no key is known.
        demo = () if mint is None else mint.demo
        return [PoolEntry(id=s.key, group=(), text=s.query) for s in demo]

    def refusal(
        self, selected: SelectedOptimizer, _closed: bool, mint: MintFacts | None
    ) -> str | None:
        # A partition's size is its split's declaration, so only its names are refused.
        paths = self.active(selected)
        held = [(e.id, e.group) for e in self.held(selected, mint)]
        if mint is not None and (bad := unnamed(paths, held)):
            return f"{self.name}.active names {bad}, which the demo partition lacks"
        return None

    def initialize(self, ctx: NodeContext) -> None:
        demo = ctx.read(Target).demo_split
        ctx.put(DemoPool(keys=kept(ctx.knobs(PoolKnobs).active, ((s.key, ()) for s in demo))))


MEMBERS = (BlockPoolInitializer(), DemoPoolInitializer())
