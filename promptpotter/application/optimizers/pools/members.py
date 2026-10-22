"""The pool initializers, the one roster a manifest's `run_init` walk names: each writes its pool once, before round 0's adapters read it."""

from __future__ import annotations

from typing import TYPE_CHECKING, Annotated, ClassVar, Protocol, assert_never

from pydantic import Field

from promptpotter.application.campaign_config import Estimand, Knob, Scope
from promptpotter.application.optimizers import nodes
from promptpotter.config.prompt_blocks import library_entries, library_identity
from promptpotter.domain.pipeline_schema import NodeKind
from promptpotter.domain.pool import (
    EARNED_SOURCE,
    BlockPool,
    DemoPool,
    Pool,
    PoolEntry,
    PoolSource,
    entry_id,
    kept,
    unnamed,
)
from promptpotter.domain.strict_model import StrictModel

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence

    from promptpotter.application.bench.node_context import NodeContext
    from promptpotter.application.optimizer_manifest import SelectedOptimizer
    from promptpotter.application.optimizers.nodes import Draw, PromptArity
    from promptpotter.domain.optimizer_state import StatePart
    from promptpotter.domain.sample import Sample

__all__ = ["MEMBERS", "PoolInitializer", "PoolKnobs"]


class PoolKnobs(StrictModel):
    active: Annotated[list[str], Knob(Scope.POLICY, Estimand.SEARCH)] = Field(
        min_length=1,
        description="The entries in play, as paths: `*` for all, an entry's content id, or a "
        "leading run of its group — a block's `field` or `field/source`, the earned blocks' "
        f"source being `{EARNED_SOURCE}`; a shot's sample key. A path keeping nothing is refused "
        "before any spend.",
    )


class _Source(Protocol):
    def identity(self) -> object: ...

    def refusal(
        self, name: str, paths: Sequence[str], *, closed: bool, demo: Sequence[Sample] | None
    ) -> str | None: ...

    def seed(self, ctx: NodeContext[PoolKnobs], paths: Sequence[str]) -> Pool: ...


class _Library:
    def identity(self) -> object:
        return library_identity()

    def refusal(
        self, name: str, paths: Sequence[str], *, closed: bool, demo: Sequence[Sample] | None
    ) -> str | None:
        library = [(e.id, e.group) for e in library_entries().values()]
        fields = dict.fromkeys(group[0] for _, group in library)
        # Earned entries exist only once run init mines them, so their group is all a path can name now.
        earned: list[tuple[str, tuple[str, ...]]] = [
            ("", (field, EARNED_SOURCE)) for field in fields
        ]
        if bad := unnamed(paths, library + earned):
            return f"{name}.active names {bad}, which match no prompt block library entry"
        if closed and not kept(paths, library):
            return (
                f"{name}.active keeps no library entry, and a `closed` reader may write only an "
                "entry's id — earned blocks are mined at run init and may be none"
            )
        return None

    def seed(self, ctx: NodeContext[PoolKnobs], paths: Sequence[str]) -> BlockPool:
        earned = (
            PoolEntry(id=entry_id(field, text), group=(field, EARNED_SOURCE), text=text)
            for field, texts in ctx.earned_blocks().items()
            for text in texts
        )
        entries: Mapping[str, PoolEntry] = {
            **{e.id: e for e in earned},
            **library_entries(),
        }
        active = kept(paths, ((i, e.group) for i, e in entries.items()))
        return BlockPool(entries=tuple(entries[i] for i in active))


class _Partition:
    def identity(self) -> object:
        return None

    def refusal(
        self, name: str, paths: Sequence[str], *, closed: bool, demo: Sequence[Sample] | None
    ) -> str | None:
        # A partition's size is its split's declaration, so only its names are refused.
        if demo is not None and (bad := unnamed(paths, [(s.key, ()) for s in demo])):
            return f"{name}.active names {bad}, which the demo partition lacks"
        return None

    def seed(self, ctx: NodeContext[PoolKnobs], paths: Sequence[str]) -> DemoPool:
        return DemoPool(keys=kept(paths, ((s.key, ()) for s in ctx.demo_split)))


def _source(source: PoolSource) -> _Source:
    match source:
        case PoolSource.LIBRARY:
            return _Library()
        case PoolSource.PARTITION:
            return _Partition()
        case unreachable:
            assert_never(unreachable)


class PoolInitializer:
    """One member per pool kind, seeding it from the kind's source."""

    kind: ClassVar[NodeKind] = NodeKind.ALGORITHM
    prompt_arity: ClassVar[PromptArity] = "none"
    knobs: ClassVar[type[StrictModel]] = PoolKnobs
    reads: ClassVar[frozenset[type[StatePart]]] = nodes.NO_STATE
    draws: ClassVar[Mapping[type[Pool], Draw]] = nodes.NO_DRAWS
    consults: ClassVar[frozenset[type]] = nodes.NO_CONSULTS

    def __init__(self, pool: type[Pool]) -> None:
        self.pool = pool
        self.name = pool.kind
        self.writes = frozenset({pool})
        self._source = _source(pool.source)

    def starting_content(self) -> object:
        """What it seeds from beside the run's own data, which ``Treatment.source`` hashes."""
        return self._source.identity()

    def active(self, selected: SelectedOptimizer) -> list[str]:
        return selected.knobs_of(self.name, PoolKnobs).active

    def refusal(
        self, selected: SelectedOptimizer, *, closed: bool, demo: Sequence[Sample] | None
    ) -> str | None:
        """At load with ``demo`` ``None``, again before the mint with the partition."""
        return self._source.refusal(self.name, self.active(selected), closed=closed, demo=demo)

    def initialize(self, ctx: NodeContext[PoolKnobs]) -> None:
        ctx.put(self._source.seed(ctx, ctx.knobs.active))


MEMBERS = (PoolInitializer(BlockPool), PoolInitializer(DemoPool))
